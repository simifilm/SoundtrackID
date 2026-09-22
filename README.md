<br />
<div align="center">
  <img src="images/logo.png" alt="Soundtrack ID Analyzer logo" width="120" height="120">

  <h3 align="center">Soundtrack ID Analyzer</h3>

  <p align="center">
    Finds and identifies the music in films.
  </p>
</div>

Soundtrack ID Analyzer finds the music in a film and tells you what it is. Drop in a
video and it locates every music cue, identifies the track behind it, and can look up
details about the film and its composer — including the composer's year of death, which
determines whether the music is in the public domain.

It runs as a desktop app on Windows and macOS. Everything it needs is included; you
don't have to install anything else.

## How it works

1. **Extract** — the soundtrack is separated out of the video file.
2. **Detect music** — the audio is cut into short chunks and each chunk is scored for
   whether it contains music.
3. **Group into cues** — neighbouring music chunks are merged into continuous cues, and
   short gaps are bridged.
4. **Identify** — each cue is matched against a music database, the same way a phone
   recognises a song playing in the room.
5. **Enrich** *(optional)* — film and composer details are looked up and added to the
   results.

---

## Getting Started

### Requirements

#### Windows

- 64-bit Windows 10 (version 1809 or newer) or Windows 11.
- WebView2, which is preinstalled on Windows 11 and on Windows 10 since the May 2021
  update.
- About < 2 GB of free disk space.

### Install

#### Windows

Download the latest release, then follow the installer.

> **Note:** If you get a SmartScreen warning click through it ("More info → Run anyway"),
> because the installer isn't code-signed.

#### macOS

1. Download `SoundtrackID.zip` from the latest release.
2. Unzip it and move **Soundtrack ID.app** into your `Applications` folder.


## Usage

### Analyze a video

Drag a video file onto the drop zone, adjust the settings if you want, then click
**Analyze**.

| Setting | Default | What it does |
|---|---|---|
| Detection API | ACRCloud | Which music database is used to identify the cues |
| Chunk Duration | 15 s | How finely the audio is sliced — shorter finds shorter cues |
| Sensitivity | 20 % | Lower values catch more music, but also more false positives |
| Export | JSON | Switch to CSV to get a spreadsheet file alongside the results |

Long films take a while: every cue has to be looked up individually, and the music
services answer one request at a time.

### Results

Detected cues appear in a list, with a minimap and a filmstrip of the whole film below
it. Hovering a cue shows a preview of that moment in the video.

When the analysis is done, a **Download results** button appears — click it to save
`results.json` (and `results.csv` if you enabled the CSV export). Earlier analyses stay
listed under **Recent runs** and can be downloaded again at any time.

### Metadata enrichment

After an analysis, click **Metadaten anreichern** to add information about the film and
the composer:

- **Film** — title, year, director, and the director's year of death.
- **Composer** — composer name, title of the work, and the composer's year of death.
  Results found through a reliable match are shown as-is; uncertain matches are marked
  with a `?` badge.

The dialog fills itself in from the video file's own tags when the file carries them.
For older runs without that information, type the film title, year or IMDb/TMDb ID
yourself.

> **Note:** whether the music is in the public domain depends on the **composer's** year
> of death, not the director's. The film header shows the director's year of death only
> as context.

Details of where each field comes from are in
[docs/metadata-enrichment.md](docs/metadata-enrichment.md).

### Detection providers

Two music services can identify the cues. Both cope with short, quiet, film-mixed audio,
which is what makes them usable on film soundtracks at all.

| Provider | Notes |
|---|---|
| **ACRCloud** (default) | Finds the most music. Matches often come with Spotify, YouTube and MusicBrainz links, which are saved into the results. Needs an account. |
| **Shazam** | A solid fallback, and needs no account. |

[docs/detection-providers.md](docs/detection-providers.md) compares both against
AcoustID and records the tests behind this recommendation.

---

## Run from source

### Browser

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.zhaw.ch/stmh/SoundtrackID.git
cd SoundtrackID
git lfs pull                          # downloads the ONNX model (~331 MB)
```

Start the server:

```bash
uv run --extra app python -m soundtrackID
```

Then open [http://localhost:8000](http://localhost:8000).

### CLI

```bash
uv run --extra app python -m soundtrackID.pipeline path/to/video.mp4 --no-isolation
```

```
--output-dir DIR      Output directory (default: ./output)
--api {shazam,acrcloud}
                      Detection provider (default: acrcloud)
--classifier {onnx,hf}
                      Music classifier backend (default: onnx)
--no-isolation        Skip vocal isolation (faster)
--chunk-duration SEC  Classifier chunk size in seconds (default: 10)
--max-segment SEC     Split segments into chunks of at most N seconds before detection
--threshold FLOAT     Music classification threshold 0–1 (default: 0.2)
```

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

## Tests

```bash
uv run pytest
```

## Project structure

```
.
├── src/soundtrackID/           Python package: pipeline, web server and UI
│   ├── __main__.py             Entry point for `python -m soundtrackID`
│   ├── main.py                 Starts the web server
│   ├── app.py                  FastAPI endpoints: upload, analysis, enrichment, history
│   ├── pipeline.py             Runs the full pipeline; also the CLI entry point
│   ├── models.py               Data models and the results.json output format
│   ├── config.py               Config dataclass and config.yaml loader
│   ├── loaders.py              Decodes the video and extracts its audio track
│   ├── chunker.py              Slices the audio into overlapping chunks
│   ├── classifiers.py          Scores each chunk for music (ONNX or HuggingFace)
│   ├── aggregator.py           Merges music chunks into cues, writes segment .wav files
│   ├── isolators.py            Optional Demucs vocal isolation
│   ├── detection.py            Identification clients: ACRCloud, Shazam, AcoustID
│   ├── thumbnails.py           Builds the filmstrip sprite shown under the waveform
│   ├── metadata/               Film and composer enrichment
│   │   ├── enricher.py         Orchestrates enrichment of a results file
│   │   ├── film.py             Film lookup: TMDb → Wikidata → imdbapi.dev
│   │   ├── music.py            Composer lookup: MusicBrainz → Wikidata
│   │   └── mp4_tags.py         Reads iTunes/Subler tags to pre-fill the film form
│   └── static/index.html       The complete web UI, in one file
├── src-tauri/                  Desktop shell (Rust / Tauri)
├── assets/ast_model/           ONNX classifier model (Git LFS)
├── scripts/                    macOS signing script and entitlements
├── tests/                      pytest suite
├── docs/                       Detailed documentation
├── config.yaml                 Default pipeline settings
├── fiwi-server.spec            PyInstaller spec for bundling the server
└── pyproject.toml              Python dependencies and optional extras
```

Further reading:

- [docs/detection-providers.md](docs/detection-providers.md) — Shazam vs ACRCloud vs AcoustID
- [docs/metadata-enrichment.md](docs/metadata-enrichment.md) — enrichment fields and sources

---

## Building a release

Builds must run on the target OS — cross-compilation is supported by neither PyInstaller
nor Tauri.

### Prerequisites (macOS)

```bash
# Rust (one-time)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Xcode Command Line Tools
xcode-select --install

# Node packages (for the Tauri CLI)
npm install
```

### Prerequisites (Windows)

```powershell
# Rust
winget install Rustlang.Rustup
# Reload your terminal, then:
rustup default stable

# Node.js (if not already installed)
winget install OpenJS.NodeJS

# Rust on Windows uses Microsoft's C++ linker (link.exe) to build programs
winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

# Node packages
npm install
```

### Step 1 — Bundle the Python server

Same command on both platforms:

```bash
uv run --extra app --group build pyinstaller --noconfirm fiwi-server.spec
```

This produces `dist/fiwi-server/` (~750 MB), which Tauri copies into the app bundle.

### Step 2 — Build the app

**macOS:**

```bash
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

Output:
- `src-tauri/target/release/bundle/macos/Soundtrack ID.app`
- `src-tauri/target/release/bundle/dmg/Soundtrack ID_0.1.0_aarch64.dmg`

**Windows:**

```powershell
npm run tauri build
```

Output: `src-tauri\target\release\bundle\nsis\Soundtrack ID_0.1.0_x64-setup.exe`

An `.msi` is written to `src-tauri\target\release\bundle\msi\` as well; the NSIS `.exe`
is the one to distribute.

### Step 3 - Code signing and notarization (macOS)

Without signing, recipients see a *"damaged and can't be opened"* Gatekeeper error.
Signing requires a **Developer ID Application** certificate, which is only available to
the **Account Holder** or **Admin** of the Apple Developer team.

For distribution outside the App Store, **notarization is mandatory** — a valid
signature alone is not enough. Gatekeeper blocks any Developer-ID-signed app that Apple
has not notarized. Tauri runs signing, notarization and stapling automatically when the
credentials below are present.

Because the bundled `fiwi-server` (a PyInstaller directory of Python `.so`/`.dylib`
files) is copied in as a Tauri **resource** — which Tauri does *not* auto-sign — it must
be deep-signed with Hardened Runtime before notarization. This is handled automatically
by `scripts/sign-fiwi-server.sh` (with `scripts/fiwi-server.entitlements`), wired into
the build via `beforeBundleCommand` in `src-tauri/tauri.macos.conf.json`, so Windows
builds skip it. No manual step is needed; it only runs when `APPLE_SIGNING_IDENTITY` is
set.

#### Option A — Account Holder builds and distributes

The Account Holder runs the build with their credentials:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Org (TEAMID)" \
APPLE_ID="holder@example.com" \
APPLE_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
APPLE_TEAM_ID="TEAMID" \
PATH="$HOME/.cargo/bin:$PATH" npm run tauri build
```

`APPLE_PASSWORD` is an app-specific password from
[appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific
Passwords. `APPLE_TEAM_ID` is the 10-character team ID from
[developer.apple.com/account](https://developer.apple.com/account) → Membership.

Verify the finished app after building:

```bash
spctl -a -vvv --type exec "src-tauri/target/release/bundle/macos/Soundtrack ID.app"
# expect: "accepted" + "source=Notarized Developer ID"
```

#### Option B — Export the certificate to a team member

1. Account Holder: Keychain Access → find *Developer ID Application: …* → right-click →
   **Export** → save as `.p12` with a password.
2. Share the `.p12` and its password securely with the developer.
3. Developer: double-click the `.p12` to import it into Keychain, then run the build
   with the signing env vars above.

#### Option C — Distribute unsigned

Recipients run this once in Terminal after moving the app to `/Applications`:

```bash
xattr -cr /Applications/Soundtrack\ ID.app
```

After that the app opens normally without any warning.

### Optional - create logo
```bash
npm run tauri -- icon <path-to-logo>
```

### Development mode

```bash
uv run --extra app python -m soundtrackID &   # start Python server on :8000
PATH="$HOME/.cargo/bin:$PATH" npm run tauri dev
```

### TMDb credentials

Enrichment uses TMDb. Everyone can use the same credentials for TMDb. The credentials must be stored in the `.env` file at the project root (gitignored):

```
FIWI_TMDB_API_TOKEN=eyJhbGc...   # v4 Bearer JWT (preferred — header auth, not query param)
FIWI_TMDB_KEY=xxxxxxxx           # v3 hex (fallback)
```

The server reads `.env` at startup. For the bundled `.app`, the spec file bundles `.env` into the application so the production build picks up the same credentials.

---

## License

Copyright 2026 University of Zurich. Licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE): free to use, modify and share for any
noncommercial purpose, including research and teaching. Commercial use requires a
separate license.