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
.venv/bin/python -m soundtrackID

# Backend + Tauri dev window
.venv/bin/python -m soundtrackID &
npm run tauri dev
```

## 3. macOS release build (.app + DMG)

The release wraps a PyInstaller onedir of the FastAPI server inside the Tauri
app bundle.

### 3.1 Native ShazamKit helper (built automatically)

`fiwi-server.spec` runs `bash shazamkit/build.sh` on every macOS build. It
compiles the helper and wraps it in a minimal bundle,
`shazamkit/build/shazamkit-match.app`, with its own provisioning profile. The
spec copies that bundle to `dist/fiwi-server/_internal/`. Build outputs are
gitignored; a compile failure aborts the build. To build it on its own:

```bash
bash shazamkit/build.sh   # -> shazamkit/build/shazamkit-match.app
```

See "Note on ShazamKit" below for why it is a bundle.

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

On macOS, ShazamKit is an App Service granted to an App ID. It only works for a
caller that carries an **embedded provisioning profile** for that App ID. A bare
command-line binary cannot carry one and always gets error 202. There is no
`com.apple.developer.shazamkit` entitlement on macOS (it is iOS-only; adding it
gets the process killed).

Setup, as it exists now:

| Piece | Value |
|-------|-------|
| Helper App ID | `ch.uzh.soundtrackid.shazamkit-match`, ShazamKit enabled under App Services |
| Profile | Developer ID profile for that App ID, committed as `shazamkit/shazamkit-match.provisionprofile` (override with `SHAZAMKIT_PROVISION_PROFILE=/path`) |
| Entitlements | `shazamkit/shazamkit-match.entitlements`: only `com.apple.application-identifier` and `com.apple.developer.team-identifier` |
| Bundle | `shazamkit-match.app/Contents/{Info.plist, MacOS/shazamkit-match, embedded.provisionprofile}` |

`scripts/sign-fiwi-server.sh` signs the bundle with those entitlements **only if
the profile lists the certificate behind `APPLE_SIGNING_IDENTITY`** (a Developer
ID profile only accepts the certificates selected when it was generated), and
then test-launches the helper. If the profile is missing, doesn't match, or the
launch test fails, it signs the helper without the entitlements instead and
prints why; Shazam then falls back to `shazamio`, as before. The build log line
`native ShazamKit: enabled` confirms it's active.

**When the signing certificate changes** (the current one expires 2027-02-01):
in the Apple Developer portal, edit the `shazamkit-match` profile, select the new
Developer ID Application certificate, download it and replace
`shazamkit/shazamkit-match.provisionprofile`.

Check a built helper by hand:

```bash
H=dist/fiwi-server/_internal/shazamkit-match.app
codesign -d --entitlements - "$H"
$H/Contents/MacOS/shazamkit-match some-clip.wav   # {"title": ...} or {"result": null}
```

If it still returns 202 for well-known music, watch
`log stream --predicate 'process == "shazamd"'` while matching: a 401 there
means the App ID's ShazamKit service isn't active; "status 200" means
authorization is fine and the clip just can't be matched.

## 4. Windows build

Supported and documented (see the "Build the installer (Windows)" section in
`README.md`). Windows builds must run on a Windows machine, PyInstaller and Tauri
do not cross-compile from macOS. The same `fiwi-server.spec` and
`npm run tauri build` produce an NSIS installer:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[onnx,shazam,web,metadata,build]"
npm install
pyinstaller --noconfirm fiwi-server.spec
npm run tauri build
```

Output: `src-tauri\target\release\bundle\nsis\SoundtrackID_0.1.0_x64-setup.exe`.
The bundled Swift ShazamKit helper is macOS-only and is simply absent on Windows;
Shazam identification uses the `shazamio` fallback there.

The one open item is signing: Windows builds are currently unsigned, so
SmartScreen shows a "More info -> Run anyway" prompt on first launch. Native
matching and Gatekeeper concerns are macOS-only.
