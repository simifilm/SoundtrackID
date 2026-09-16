# FIWI Filmmusik Analyzer

Detects and identifies music in film videos. Upload a video, and the pipeline classifies audio chunks, aggregates them into music segments, and identifies each segment via Shazam — all inside a native macOS desktop app.

---

## Desktop App (macOS)

### Install

1. Download `FIWI-Filmmusik.zip` from the latest release.
2. Unzip and move **FIWI Filmmusik.app** to `/Applications`.
3. First launch: right-click the app → **Open** → **Open** (required once to bypass Gatekeeper on unsigned builds).

The app is self-contained — no Python or Rust installation needed.

### Usage

Drag a video file onto the drop zone. Configure analysis settings in the sidebar, then click **Analyze**.

| Setting | Default | Description |
|---|---|---|
| Chunk Duration | 15 s | Audio chunk size for both classification and Shazam fingerprinting |
| Sensitivity | 20 % | Classification threshold — lower catches more, with more false positives |
| Export CSV | off | Include a CSV alongside the JSON results download |

When analysis completes, a **Download results** button appears. Click it to save `results.json` (and optionally `results.csv`). Previous runs are listed under **Recent runs** and can be downloaded at any time.

### Metadata enrichment

After analysis, click **Metadaten anreichern** to enrich the run with film and composer metadata:

- **Film** — title, year, director and director's death year via TMDb (primary), Wikidata, and imdbapi.dev.
- **Composer** — composer name, work title and death year via MusicBrainz (ISRC chain, high confidence) with Wikidata as a fallback (title+artist search, flagged as low confidence with a `?` badge).

The modal is pre-filled from MP4 container tags (Subler-compatible iTunes Movie atoms: title, year, IMDb ID, TMDb ID) when present. For history runs without IDs, type them manually.

**Note**: copyright (PD) status of the music depends on the *composer*'s death year, not the director's. The film header shows the director's death year only for context.

#### TMDb credentials

Enrichment uses TMDb. Put your credentials into a `.env` file at the project root (gitignored):

```
FIWI_TMDB_API_TOKEN=eyJhbGc...   # v4 Bearer JWT (preferred — header auth, not query param)
FIWI_TMDB_KEY=xxxxxxxx           # v3 hex (fallback)
```

The server reads `.env` at startup. For the bundled `.app`, the spec file bundles `.env` into the application so the production build picks up the same credentials.

---

## Running from source (Python)

Requires Python 3.10+.

```bash
git clone https://github.zhaw.ch/stmh/FIWI-Filmmusik.git
cd FIWI-Filmmusik
git lfs pull                          # downloads the ONNX model (~331 MB)
```

Start the server:

```bash
uv run --extra app python -m fiwi_filmmusik
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Optional: HuggingFace classifier (PyTorch)

The default classifier uses ONNX Runtime (fast, no GPU needed). To use the original HuggingFace/PyTorch pipeline instead:

```bash
FIWI_HF=1 uv run --extra hf --extra shazam --extra web python -m fiwi_filmmusik
```

### Optional: ACRCloud identification

An alternative to Shazam, selectable via the **ACRCloud** option in the
Detection API dropdown. Like Shazam (and unlike AcoustID), ACRCloud recognises
short, degraded, film-mixed audio, so it works on individual film cues. When a
match carries external IDs, the results store Spotify / YouTube / MusicBrainz
links too. See [docs/detection-providers.md](docs/detection-providers.md) for a
detailed comparison of Shazam, ACRCloud and AcoustID and the test findings
behind this recommendation.

`pyacrcloud` is a core dependency; no extra install step is needed.

Create a project connected to the **Music Recognition** database at
[console.acrcloud.com](https://console.acrcloud.com) (a plain "Audio
Fingerprinting" project only matches your own uploaded audio and will return no
results for commercial music). Add its credentials to your `.env`:

```
ACRCLOUD_HOST=identify-eu-west-1.acrcloud.com
ACRCLOUD_ACCESS_KEY=xxxxxxxx
ACRCLOUD_ACCESS_SECRET=xxxxxxxx
```

### Shazam via ShazamKit (macOS)

The **Shazam** provider uses Apple's official **ShazamKit** through a small native
Swift helper in [`shazamkit/`](shazamkit/), not a Python library. Build it once:

```bash
bash shazamkit/build.sh
```

This produces `shazamkit/shazamkit-match`, which the server invokes as a
subprocess. ShazamKit matching against Apple's catalog requires the
`com.apple.developer.shazamkit` **entitlement**, which is only honored when the
helper runs inside an app whose **provisioning profile** grants it:

1. In the Apple Developer portal, register the App ID `ch.uzh.soundtrackid` and
   enable the **ShazamKit** capability on it.
2. Create a **Developer ID** provisioning profile including ShazamKit and embed
   it in the built `.app`.

Without that (a bare dev build, or before the profile is activated), ShazamKit
returns error `202`; the client then **falls back to `shazamio`** (install the
`shazam` extra), so the **Shazam** provider keeps working and silently upgrades to
native ShazamKit once the profile is in place. Set
`SHAZAMKIT_HELPER=/path/to/shazamkit-match` to override the helper location.

### Optional: vocal isolation with Demucs

Strips non-music audio before Shazam fingerprinting (improves accuracy on dialogue-heavy films). Requires a CUDA GPU for practical speed.

```bash
uv run --extra app --extra demucs python -m fiwi_filmmusik
```

---

## CLI

```bash
python3 -m fiwi_filmmusik.pipeline path/to/video.mp4 --no-isolation
```

```
--output-dir DIR      Output directory (default: ./output)
--no-isolation        Skip vocal isolation (faster)
--chunk-duration SEC  Classifier chunk size in seconds (default: 10)
--threshold FLOAT     Music classification threshold 0–1 (default: 0.2)
```

---

## Output

Results are written to `output/<video_name>/`:

```
output/
  My Film (2024)/
    results.json        # all segments and identifications
    results.csv         # optional CSV export
    segments/
      seg_001.wav       # extracted audio for each music cue
      seg_002.wav
```

---

## Building the desktop app

### Prerequisites

```bash
# Rust (one-time)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Xcode Command Line Tools (macOS)
xcode-select --install

# Node / npm (for Tauri CLI)
npm install
```

### Bundle the Python server

```bash
uv run --extra app --group build pyinstaller --noconfirm fiwi-server.spec
```

This produces `dist/fiwi-server/` (~300 MB), which Tauri copies into the `.app`
bundle.

### Build the `.app` (macOS)

```bash
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

Output:
- `src-tauri/target/release/bundle/macos/FIWI Filmmusik.app`
- `src-tauri/target/release/bundle/dmg/FIWI Filmmusik_0.1.0_aarch64.dmg`

### Code signing and notarization (macOS)

Without signing, recipients see a *"damaged and can't be opened"* Gatekeeper error. Signing requires a **Developer ID Application** certificate, which is only available to the **Account Holder** or **Admin** of the Apple Developer team.

For distribution outside the App Store, **notarization is mandatory** — a valid signature alone is not enough. Gatekeeper blocks any Developer-ID-signed app that Apple has not notarized. Tauri runs signing, notarization, and stapling automatically when the credentials below are present.

Because the bundled `fiwi-server` (a PyInstaller directory of Python `.so`/`.dylib` files) is copied in as a Tauri **resource** — which Tauri does *not* auto-sign — it must be deep-signed with Hardened Runtime before notarization. This is handled automatically by `scripts/sign-fiwi-server.sh` (with `scripts/fiwi-server.entitlements`), wired into the build via `beforeBundleCommand` in `src-tauri/tauri.macos.conf.json`, so Windows builds skip it. No manual step is needed; it only runs when `APPLE_SIGNING_IDENTITY` is set.

**Option A — Account Holder builds and distributes**

The Account Holder runs the build with their credentials:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Org (TEAMID)" \
APPLE_ID="holder@example.com" \
APPLE_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
APPLE_TEAM_ID="TEAMID" \
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

`APPLE_PASSWORD` is an app-specific password from [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords. `APPLE_TEAM_ID` is the 10-character team ID from [developer.apple.com/account](https://developer.apple.com/account) → Membership. Verify the finished app after building:

```bash
spctl -a -vvv --type exec "src-tauri/target/release/bundle/macos/FIWI Filmmusik.app"
# expect: "accepted" + "source=Notarized Developer ID"
```

**Option B — Export certificate to a team member**

1. Account Holder: Keychain Access → find *Developer ID Application: …* → right-click → **Export** → save as `.p12` with a password
2. Share the `.p12` + password securely with the developer
3. Developer: double-click the `.p12` to import it into Keychain, then run the build with the signing env vars above

**Option C — Distribute unsigned (workaround for recipients)**

Recipients run this once in Terminal after moving the app to `/Applications`:

```bash
xattr -cr /Applications/FIWI\ Filmmusik.app
```

After that the app opens normally without any warning.

### Build the installer (Windows)

Windows builds must be run on a Windows machine — cross-compilation from macOS is not supported by PyInstaller or Tauri.

**Prerequisites (Windows):**
```powershell
# Rust
winget install Rustlang.Rustup
# Reload your terminal, then:
rustup default stable

# Node.js (if not already installed)
winget install OpenJS.NodeJS

# Rust on Windows uses Microsoft's C++ linker (link.exe) to build programs
winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

# Install npm packages
npm install
```

**Bundle the Python server:**
```powershell
uv run --extra app --group build pyinstaller --noconfirm fiwi-server.spec
```

**Build the Windows installer:**
```powershell
npm run tauri build
```

Output: `src-tauri\target\release\bundle\nsis\FIWI Filmmusik_0.1.0_x64-setup.exe`

> **Note:** Windows uses WebView2 (Edge) as the embedded browser, which ships pre-installed on Windows 10 (May 2021 update) and Windows 11. No separate browser installation is required.

**Installation on Windows:**
Run the `.exe` installer. Windows SmartScreen may show a warning for unsigned builds — click **More info → Run anyway** to proceed.

### Development mode

```bash
uv run --extra app python -m fiwi_filmmusik &   # start Python server on :8000
PATH="$HOME/.cargo/bin:$PATH" npm run tauri dev
```

---

## Development (Python)

```bash
uv run pytest
```
