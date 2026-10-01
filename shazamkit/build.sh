#!/usr/bin/env bash
#
# Compile the ShazamKit identification helper and wrap it in a minimal .app
# bundle.
#
# Output:
#   shazamkit/shazamkit-match                        (bare binary, intermediate)
#   shazamkit/build/shazamkit-match.app              (what gets bundled)
#     Contents/Info.plist                            (CFBundleIdentifier = helper App ID)
#     Contents/MacOS/shazamkit-match
#     Contents/embedded.provisionprofile             (if a profile is available)
#
# Why a bundle: on macOS, ShazamKit only authorizes a caller that carries an
# embedded provisioning profile for an App ID with the ShazamKit App Service
# enabled. A bare command-line binary cannot carry a profile, so it always gets
# error 202. The bundle gives the helper its own identity
# (ch.uzh.soundtrackid.shazamkit-match) and its own Developer ID profile.
#
# Profile source, in order: $SHAZAMKIT_PROVISION_PROFILE, then the committed
# shazamkit/shazamkit-match.provisionprofile. Without one, the bundle is built
# without a profile; scripts/sign-fiwi-server.sh then signs the helper normally
# and the app falls back to shazamio (same behavior as before).
#
# Signing is NOT done here; scripts/sign-fiwi-server.sh signs the bundle as
# part of the packaged .app build.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$DIR/shazamkit-match.swift"
OUT="$DIR/shazamkit-match"
BUNDLE_ID="ch.uzh.soundtrackid.shazamkit-match"
APP="$DIR/build/shazamkit-match.app"
PROFILE="${SHAZAMKIT_PROVISION_PROFILE:-$DIR/shazamkit-match.provisionprofile}"

echo "[shazamkit] compiling $SRC ..."
swiftc "$SRC" -o "$OUT" \
    -framework ShazamKit -framework AVFoundation -framework Foundation \
    -O

echo "[shazamkit] assembling $APP ..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$OUT" "$APP/Contents/MacOS/shazamkit-match"
chmod +x "$APP/Contents/MacOS/shazamkit-match"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>         <string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key>         <string>shazamkit-match</string>
  <key>CFBundleName</key>               <string>shazamkit-match</string>
  <key>CFBundlePackageType</key>        <string>APPL</string>
  <key>CFBundleVersion</key>            <string>1</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>LSMinimumSystemVersion</key>     <string>12.0</string>
  <key>LSUIElement</key>                <true/>
</dict>
</plist>
EOF
plutil -lint "$APP/Contents/Info.plist" >/dev/null

if [[ -f "$PROFILE" ]]; then
  cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"
  echo "[shazamkit] embedded provisioning profile: $PROFILE"
else
  echo "[shazamkit] no provisioning profile found ($PROFILE)."
  echo "            The helper will be signed without ShazamKit authorization;"
  echo "            Shazam identification falls back to shazamio."
fi

echo "[shazamkit] built $APP (unsigned; the .app build signs it)"
