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
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[onnx,shazam,web,metadata]"
```

Start the server:

```bash
python3 -m fiwi_filmmusik
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Optional: HuggingFace classifier (PyTorch)

The default classifier uses ONNX Runtime (fast, no GPU needed). To use the original HuggingFace/PyTorch pipeline instead:

```bash
pip install -e ".[hf,shazam,web]"
FIWI_HF=1 python3 -m fiwi_filmmusik
```

### Optional: vocal isolation with Demucs

Strips non-music audio before Shazam fingerprinting (improves accuracy on dialogue-heavy films). Requires a CUDA GPU for practical speed.

```bash
pip install -e ".[demucs]"
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
source .venv/bin/activate
pip install -e ".[build]"
pyinstaller --noconfirm fiwi-server.spec
```

This produces `dist/fiwi-server/` (~300 MB), which Tauri copies into the `.app` bundle.

### Build the `.app` (macOS)

```bash
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

Output:
- `src-tauri/target/release/bundle/macos/FIWI Filmmusik.app`
- `src-tauri/target/release/bundle/dmg/FIWI Filmmusik_0.1.0_aarch64.dmg`

### Code signing and notarization (macOS)

Without signing, recipients see a *"damaged and can't be opened"* Gatekeeper error. Signing requires a **Developer ID Application** certificate, which is only available to the **Account Holder** or **Admin** of the Apple Developer team.

**Option A — Account Holder builds and distributes**

The Account Holder runs the build with their credentials:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Org (TEAMID)" \
APPLE_ID="holder@example.com" \
APPLE_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
APPLE_TEAM_ID="TEAMID" \
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

`APPLE_PASSWORD` is an app-specific password from [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords. Tauri signs, notarizes, and staples automatically.

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

# Python 3.10+ with dependencies
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[onnx,shazam,web,metadata,build]"

# Install npm packages
npm install
```

**Bundle the Python server:**
```powershell
.venv\Scripts\activate
pyinstaller --noconfirm fiwi-server.spec
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
source .venv/bin/activate
python3 -m fiwi_filmmusik &          # start Python server on :8000
PATH="$HOME/.cargo/bin:$PATH" npm run tauri dev
```

---

## Development (Python)

```bash
pip install -e ".[dev]"
pytest
```
