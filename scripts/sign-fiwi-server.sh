#!/usr/bin/env bash
#
# Deep-sign the bundled PyInstaller `fiwi-server` onedir with the Developer ID
# certificate and Hardened Runtime, so the resulting .app can be notarized.
#
# Tauri auto-signs `externalBin` sidecars but NOT files under `bundle.resources`
# (which is how `fiwi-server` is bundled). Every nested Mach-O (.so/.dylib and
# helper executables) must therefore be signed here, inside-out, before Tauri
# copies the directory into the .app and notarizes it.
#
# Wired into `tauri build` via `build.beforeBundleCommand` in tauri.conf.json,
# so it runs automatically. Safe to run standalone too.
#
# Requires APPLE_SIGNING_IDENTITY in the environment. If it is unset (a normal
# unsigned dev build), the script no-ops so `tauri build` still works.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$REPO_ROOT/dist/fiwi-server"
ENTITLEMENTS="$SCRIPT_DIR/fiwi-server.entitlements"

if [[ -z "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  echo "[sign-fiwi-server] APPLE_SIGNING_IDENTITY not set — skipping (unsigned build)."
  exit 0
fi

if [[ ! -d "$TARGET" ]]; then
  echo "[sign-fiwi-server] $TARGET not found — run pyinstaller first." >&2
  exit 1
fi

echo "[sign-fiwi-server] Signing nested Mach-O binaries in $TARGET ..."
count=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$APPLE_SIGNING_IDENTITY" "$f" >/dev/null
    count=$((count + 1))
  fi
done < <(find "$TARGET" -type f -print0)

# Materialise Mach-O symlinks into real, FLAT-signed copies. Tauri's resource
# copy dereferences symlinks into standalone files during bundling. A binary
# inside a *.framework gets a bundle-format signature from codesign, which is
# only valid within its framework — so a dereferenced standalone copy (e.g.
# _internal/Python or Python.framework/Python, both symlinks to
# Python.framework/Versions/<v>/Python) fails notarization with "The signature
# of the binary is invalid". By replacing every Mach-O symlink here with a real
# file carrying a flat signature, Tauri copies valid standalone binaries, while
# the canonical framework binary keeps the bundle signature it needs.
link_count=0
while IFS= read -r -d '' link; do
  tgt="$(readlink "$link")"
  # Resolve the target (handles relative targets and symlinked version dirs;
  # `cd` follows intermediate symlinks like Versions/Current).
  resolved="$(cd "$(dirname "$link")" && cd "$(dirname "$tgt")" 2>/dev/null && pwd)/$(basename "$tgt")" || continue
  [[ -f "$resolved" ]] || continue
  file -b "$resolved" | grep -q "Mach-O" || continue
  rm "$link"
  tmp="$(mktemp -d)"
  cp "$resolved" "$tmp/f"
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$APPLE_SIGNING_IDENTITY" "$tmp/f" >/dev/null
  cp "$tmp/f" "$link"
  rm -rf "$tmp"
  link_count=$((link_count + 1))
done < <(find "$TARGET" -type l -print0)
echo "[sign-fiwi-server] Materialised $link_count Mach-O symlink(s) as flat-signed copies."

# Seal the main executable last so its signature covers the finished directory.
codesign --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$APPLE_SIGNING_IDENTITY" "$TARGET/fiwi-server" >/dev/null

echo "[sign-fiwi-server] Signed $count nested Mach-O binary(ies) + main executable."
echo "[sign-fiwi-server] Verifying main executable ..."
codesign --verify --strict --verbose=2 "$TARGET/fiwi-server"
echo "[sign-fiwi-server] Done."
