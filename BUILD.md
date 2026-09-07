# Building SoundtrackID

This document describes how to build, sign, and package SoundtrackID from source.
It is the committed counterpart to the developer notes, so a new maintainer can
reproduce a release without local-only files.

## Prerequisites

- macOS (Apple Silicon or Intel), Xcode command line tools
- Python 3.12 with a virtual environment
- Node.js + npm
- Rust toolchain (`cargo`, via rustup) for Tauri
- For a signed release: an Apple Developer ID Application certificate in the
  login keychain and an App Store Connect API key (`.p8`) for notarization

## 1. Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,onnx,shazam,web,metadata,build]"
```

The ONNX classifier weights live in `assets/ast_model/` and are pulled via
`git lfs pull`.

## 2. Run in development

```bash
# Backend only (http://localhost:8000)
.venv/bin/python -m fiwi_filmmusik

# Backend + Tauri dev window
.venv/bin/python -m fiwi_filmmusik &
npm run tauri dev
```

## 3. macOS release build (.app + DMG)

The release wraps a PyInstaller onedir of the FastAPI server inside the Tauri
app bundle.

### 3.1 Compile the native ShazamKit helper (optional)

```bash
bash shazamkit/build.sh   # -> shazamkit/shazamkit-match
```

The helper is bundled by the PyInstaller spec. Native ShazamKit matching is
currently disabled at signing time (see note below); the app falls back to the
`shazamio` client, so this step is not required for a working build.

### 3.2 Bundle the Python server

```bash
.venv/bin/pyinstaller --noconfirm fiwi-server.spec   # -> dist/fiwi-server/
```

Re-run this after ANY Python or frontend change; the .app copies `dist/fiwi-server`
as-is.

### 3.3 Build, sign, notarize, staple

Signing credentials come from the environment (kept in the gitignored `.env`):

| Variable | Purpose |
|----------|---------|
| `APPLE_SIGNING_IDENTITY` | Developer ID Application identity string |
| `APPLE_TEAM_ID` | Apple Developer team id |
| `APPLE_API_KEY` / `APPLE_API_ISSUER` / `APPLE_API_KEY_PATH` | App Store Connect API key for notarytool |

`scripts/sign-fiwi-server.sh` runs automatically as the Tauri
`beforeBundleCommand`; it deep-signs every nested Mach-O in `dist/fiwi-server`
with Hardened Runtime so the bundle can be notarized.

```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: ... (TEAMID)"
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

Tauri produces `src-tauri/target/release/bundle/macos/SoundtrackID.app`. The
headless DMG step (Finder AppleScript) does not work without a GUI session; build
the DMG from the notarized app with `hdiutil` instead. Notarize and staple both
the .app and the DMG:

```bash
# notarize the app
ditto -c -k --keepParent SoundtrackID.app app.zip
xcrun notarytool submit app.zip --key "$APPLE_API_KEY_PATH" \
  --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER" --wait
xcrun stapler staple SoundtrackID.app

# wrap into a DMG and notarize that too
hdiutil create -volname SoundtrackID -srcfolder <stage-dir> -format UDZO SoundtrackID.dmg
xcrun notarytool submit SoundtrackID.dmg --key "$APPLE_API_KEY_PATH" \
  --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER" --wait
xcrun stapler staple SoundtrackID.dmg
```

### Note on ShazamKit

`scripts/sign-fiwi-server.sh` applies the restricted
`com.apple.developer.shazamkit` entitlement to the helper only when
`SHAZAMKIT_ENABLE=1`. It is off by default: Apple currently omits that
entitlement from Developer ID provisioning profiles (acknowledged bug
FB22582333), so an entitled helper is killed at runtime. With it off the helper
is signed normally and Shazam identification uses the `shazamio` fallback. Set
`SHAZAMKIT_ENABLE=1` once Apple ships the fix.

## 4. Windows build

Not yet set up. The Tauri front end is cross-platform, but the packaging
(PyInstaller spec, signing script) is macOS-specific and would need a Windows
equivalent (PyInstaller onedir for the server, Tauri NSIS/MSI target, code
signing). This is an open handover item.
