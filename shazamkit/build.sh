#!/usr/bin/env bash
#
# Compile the ShazamKit identification helper and (when signing creds are present)
# sign it with the ShazamKit entitlement so it can match against Apple's catalog.
#
# Output: shazamkit/shazamkit-match  (a native arm64/x86_64 binary)
#
# ShazamKit matching only works when this binary runs inside an app whose embedded
# provisioning profile authorizes com.apple.developer.shazamkit (see README). An
# unsigned/dev build compiles fine but returns error 202 at match time; the Python
# ShazamDetectionClient handles that as "unavailable".

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$DIR/shazamkit-match.swift"
OUT="$DIR/shazamkit-match"
ENTITLEMENTS="$DIR/shazamkit-match.entitlements"

echo "[shazamkit] compiling $SRC ..."
swiftc "$SRC" -o "$OUT" \
    -framework ShazamKit -framework AVFoundation -framework Foundation \
    -O

# Signing is intentionally NOT done here. For a dev build the binary stays
# unsigned (ShazamKit returns 202, and the Python client falls back to shazamio).
# For the packaged .app, scripts/sign-fiwi-server.sh signs this helper — with the
# ShazamKit entitlement only when SHAZAMKIT_PROVISION_PROFILE points at a profile
# that authorizes it (see README). $ENTITLEMENTS is used there.
echo "[shazamkit] built $OUT (unsigned; the .app build signs it)"
