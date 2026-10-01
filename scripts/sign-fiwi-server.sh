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
# Wired into `tauri build` via `build.beforeBundleCommand` in
# tauri.macos.conf.json (macOS only), so it runs automatically. Safe to run
# standalone too.
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

# ShazamKit helper bundle (_internal/shazamkit-match.app, see shazamkit/build.sh).
#
# macOS only authorizes ShazamKit for a caller that carries an embedded
# provisioning profile for an App ID with the ShazamKit App Service. The helper
# therefore has its own App ID (ch.uzh.soundtrackid.shazamkit-match), its own
# Developer ID profile, and entitlements that bind it to that App ID
# (shazamkit/shazamkit-match.entitlements). The loop above signed the loose
# binary inside the bundle; here the whole bundle is (re-)signed.
#
# Safety: the ShazamKit entitlements are applied only if the embedded profile
# lists the certificate we sign with. A mismatch would get the helper killed at
# launch (AMFI), so in that case — and if no profile is embedded — the bundle is
# signed with the standard entitlements instead. ShazamKit then returns 202 and
# the app falls back to shazamio, exactly as before. A launch smoke test at the
# end catches any remaining mismatch the same way.
HELPER_APP="$TARGET/_internal/shazamkit-match.app"
SHAZAMKIT_ENTITLEMENTS="$REPO_ROOT/shazamkit/shazamkit-match.entitlements"

sign_helper_plain() {
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$APPLE_SIGNING_IDENTITY" "$HELPER_APP" >/dev/null
}

# True if the embedded profile lists the certificate behind APPLE_SIGNING_IDENTITY.
profile_accepts_signer() {
  local profile="$HELPER_APP/Contents/embedded.provisionprofile"
  [[ -f "$profile" ]] || return 1
  local signer
  if [[ "$APPLE_SIGNING_IDENTITY" =~ ^[0-9A-Fa-f]{40}$ ]]; then
    signer="$APPLE_SIGNING_IDENTITY"
  else
    signer="$(security find-identity -v -p codesigning \
      | grep -F "\"$APPLE_SIGNING_IDENTITY\"" | head -1 | awk '{print $2}')"
  fi
  [[ -n "$signer" ]] || return 1
  signer="$(echo "$signer" | tr '[:lower:]' '[:upper:]')"
  local tmp n i h found=1
  tmp="$(mktemp -d)"
  security cms -D -i "$profile" > "$tmp/profile.plist" 2>/dev/null || { rm -rf "$tmp"; return 1; }
  n="$(plutil -extract DeveloperCertificates raw -o - "$tmp/profile.plist" 2>/dev/null || echo 0)"
  for ((i = 0; i < n; i++)); do
    h="$(plutil -extract "DeveloperCertificates.$i" raw -o - "$tmp/profile.plist" \
      | base64 -D | shasum | awk '{print toupper($1)}')"
    [[ "$h" == "$signer" ]] && found=0
  done
  rm -rf "$tmp"
  return $found
}

if [[ -d "$HELPER_APP" ]]; then
  if [[ -f "$SHAZAMKIT_ENTITLEMENTS" ]] && profile_accepts_signer; then
    codesign --force --timestamp --options runtime \
      --entitlements "$SHAZAMKIT_ENTITLEMENTS" \
      --sign "$APPLE_SIGNING_IDENTITY" "$HELPER_APP" >/dev/null
    shazamkit_mode="enabled"
  else
    sign_helper_plain
    shazamkit_mode="disabled (no profile, or profile does not list the signing certificate)"
  fi

  # Launch smoke test: without arguments the helper prints a usage JSON and
  # exits 0. If macOS kills it (profile/entitlement mismatch), fall back.
  if ! "$HELPER_APP/Contents/MacOS/shazamkit-match" >/dev/null 2>&1; then
    echo "[sign-fiwi-server] WARNING: ShazamKit helper failed to launch with the"
    echo "                   ShazamKit entitlements — re-signing without them."
    sign_helper_plain
    shazamkit_mode="disabled (launch test failed)"
  fi
  codesign --verify --strict "$HELPER_APP"
  echo "[sign-fiwi-server] ShazamKit helper signed; native ShazamKit: $shazamkit_mode."
else
  echo "[sign-fiwi-server] ShazamKit helper bundle not found — skipping."
fi

# Seal the main executable last so its signature covers the finished directory.
codesign --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$APPLE_SIGNING_IDENTITY" "$TARGET/fiwi-server" >/dev/null

echo "[sign-fiwi-server] Signed $count nested Mach-O binary(ies) + main executable."
echo "[sign-fiwi-server] Verifying main executable ..."
codesign --verify --strict --verbose=2 "$TARGET/fiwi-server"
echo "[sign-fiwi-server] Done."
