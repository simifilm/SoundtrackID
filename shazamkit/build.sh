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

if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]]; then
    echo "[shazamkit] signing with ShazamKit entitlement ..."
    codesign --force --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" \
        --sign "$APPLE_SIGNING_IDENTITY" "$OUT"
else
    echo "[shazamkit] APPLE_SIGNING_IDENTITY not set — leaving the binary unsigned"
    echo "           (compiles + runs, but ShazamKit matching returns 202 without"
    echo "            the entitlement + provisioning profile)."
fi

echo "[shazamkit] built $OUT"
