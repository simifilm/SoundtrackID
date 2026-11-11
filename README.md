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
5. **Look up composers** — for every identified cue with an ISRC, the composer and their
   year of death are looked up automatically.

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

1. Download `SoundtrackID_<version>_aarch64.dmg` from the latest release.
2. Open it and drag **SoundtrackID** into your `Applications` folder.


## Usage

### Analyze a video

Drag a video file onto the drop zone, adjust the settings if you want, then click
**Analyze**.

| Setting | Default | What it does |
|---|---|---|
| Detection providers | Shazam | Which music services identify the cues; tick two or more to let them vote (see [Detection providers](#detection-providers)) |
| Chunk Duration | 30 s | Longest piece of a cue sent for identification — long cues are split, so a change of track inside a cue is caught; shorter means more requests |
| Hop Size | 5 s | Step between the overlapping windows scored for music — smaller places cue boundaries more precisely but takes longer |
| Sensitivity | 20 % | Lower values catch more music, but also more false positives |
| Bridge Gaps | 0 hops | Joins clips of the same track across up to this many non-music hops |

Long films take a while: every cue has to be looked up individually, and the music
services answer one request at a time.

### Results

Detected cues appear in a list, with a minimap and a filmstrip of the whole film below
it. Hovering a cue shows a preview of that moment in the video.

When the analysis is done, a **Download results** button appears — choose JSON or CSV
(a spreadsheet). Both hold the same results, including your edits. Earlier analyses stay
listed under **Recent runs** and can be downloaded again at any time.

### Metadata enrichment

Before the analysis starts, a **Confirm film** panel shows the details read from the video
file's own tags. Correct or complete them, or click **Look up online** to fill the empty
fields. The year matters: it is used to flag music that was released after the film.
The composers are then looked up automatically during the analysis:

- **Film** — title, year, director, and the director's year of death.
- **Composer** — the work's authors (composer, lyricist), its title and their years of
  death, and from those whether the work is in the public domain (the **Rights**
  column). Results found through a reliable match are shown as-is; uncertain matches
  are shown in grey italics. Only cues with an ISRC are looked up.

> **Note:** whether the music is in the public domain depends on the **composer's** year
> of death, not the director's. The film header shows the director's year of death only
> as context.

Details of where each field comes from are in
[docs/metadata-enrichment.md](docs/metadata-enrichment.md).

### Detection providers

Three music services can identify the cues. All cope with short, quiet, film-mixed
audio, which is what makes them usable on film soundtracks at all.

| Provider | Notes |
|---|---|
| **ACRCloud** | Finds the most music (and is the CLI default). Matches often come with Spotify, YouTube and MusicBrainz links, which are saved into the results. Needs an account. |
| **Shazam** | Preselected in the app, and needs no account. |
| **AudD** | Needs an API token. Matches come with a MusicBrainz link and usually an ISRC, which the composer lookup needs. |

Tick two or more and they vote: each cue gets the answer most of them agree on, with a
badge showing how many agreed. Keys for ACRCloud and AudD are entered under
**Settings → API keys**.

[docs/detection-providers.md](docs/detection-providers.md) compares ACRCloud and Shazam
against AcoustID and records the tests behind this recommendation.

---

## Run from source

### Browser

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/simifilm/SoundtrackID.git
cd SoundtrackID
git lfs pull                          # downloads the ONNX model (~331 MB)
```

Start the server:

```bash
uv run --extra app python -m soundtrackID
```

Then open [http://localhost:8000](http://localhost:8000).

#### Optional: HuggingFace classifier (PyTorch)
The default classifier uses ONNX Runtime (fast, no GPU needed). To use the original HuggingFace/PyTorch pipeline instead:

```bash
FIWI_HF=1 uv run --extra hf --extra shazam --extra web python -m soundtrackID
```

### CLI

The CLI runs the same analysis as the app: identification, the date re-ranking
and the composer lookup, saved as one run.

```bash
uv run --extra app python -m soundtrackID.cli analyze path/to/video.mp4 --no-isolation
```

```
--output-dir DIR      Folder of the runs (default: ./output)
--api API             Detection provider(s): acrcloud, shazam, audd — or a
                      comma-separated list for ensemble voting (default: acrcloud)
--classifier {onnx,hf}
                      Music classifier backend (default: onnx)
--no-isolation        Skip vocal isolation (faster)
--chunk-duration SEC  Length of a classified window in seconds (default: 10)
--hop SEC             Step between overlapping classified windows (default: 5)
--max-segment SEC     Split segments into chunks of at most N seconds before detection
--threshold FLOAT     Music classification threshold 0–1 (default: 0.2)
--bridge-gap SEC      Join clips of the same track across up to N seconds without
                      music (default: 2)
--title, --year, --imdb-id
                      The film, where its file's tags lack it or are wrong; the
                      year flags music released after the film
--lookup-film         Fill in what is missing about the film online (TMDb,
                      Wikidata), like the app's Look up online
--csv                 Also write results.csv: the same results as a spreadsheet
```

When a provider stops early (a rate limit, or unreachable), continue the run later;
it identifies only the cues still pending:

```bash
uv run --extra app python -m soundtrackID.cli resume "output/My Film_2026-11-11_10-15-00"
```

ACRCloud and AudD read their keys from the environment or a `.env` file:
`ACRCLOUD_HOST`, `ACRCLOUD_ACCESS_KEY`, `ACRCLOUD_ACCESS_SECRET`, `AUDD_API_TOKEN`;
`--lookup-film` reads `FIWI_TMDB_API_TOKEN` or `FIWI_TMDB_KEY`.

## Output

Each analysis is a run of its own, in `output/<video_name>_<date>_<time>/`:

```
output/
  My Film_2026-11-11_10-15-00/
    results.json        # all segments and identifications
    timeline.json       # waveform and music classification, for the app's timeline
    results.csv         # with --csv: the same results as a spreadsheet
    segments/
      seg_004.wav       # audio of a cue left pending at a rate limit, for Resume
```

## Tests

```bash
uv run --extra app pytest
```

The tests need no network and no API keys: the music services and lookup APIs are
replaced by recorded or canned responses, and test videos are generated on the fly.

The tests in `tests/ui` drive the page in a headless Chromium; without it they are
skipped. Install it once with `uv run playwright install chromium`. To run only those,
or all others: `pytest -m browser`, `pytest -m "not browser"`.

## Project structure

```
.
├── src/soundtrackID/           Python package: pipeline, web server and UI
│   ├── __main__.py             Entry point for `python -m soundtrackID`
│   ├── main.py                 Starts the web server
│   ├── app.py                  FastAPI endpoints: upload, film lookup, analysis, history
│   ├── cli.py                  Command line: analyze a video, resume a run
│   ├── analysis.py             The analysis the app and the CLI share, and resume
│   ├── pipeline.py             Finds the music in a video and identifies it
│   ├── models.py               Data models and the results.json output format
│   ├── loaders.py              Decodes the video and extracts its audio track
│   ├── chunker.py              Slices the audio into overlapping chunks
│   ├── classifiers.py          Scores each chunk for music (ONNX or HuggingFace)
│   ├── aggregator.py           Merges music chunks into cues, writes segment .wav files
│   ├── isolators.py            Optional Demucs vocal isolation
│   ├── detection.py            Identification clients (ACRCloud, Shazam, AudD) and ensemble voting
│   ├── thumbnails.py           Builds the filmstrip sprite shown under the waveform
│   ├── metadata/               Film and composer enrichment
│   │   ├── enricher.py         Looks up the composers of a run's cues
│   │   ├── film.py             Film lookup: TMDb → Wikidata → imdbapi.dev
│   │   ├── music.py            Composer lookup: MusicBrainz → Wikidata
│   │   └── mp4_tags.py         Reads iTunes/Subler tags to pre-fill the film form
│   └── static/                 The web UI
│       ├── index.html          The page's markup
│       ├── app.css             Its styles
│       └── js/                 Its scripts, one per part of the page, run in the order
│                               index.html lists them (state.js first, main.js last)
├── src-tauri/                  Desktop shell (Rust / Tauri)
├── assets/ast_model/           ONNX classifier model (Git LFS)
├── scripts/                    macOS signing script and entitlements
├── tests/                      pytest suite; tests/ui drives the page in a headless browser
├── docs/                       Detailed documentation
├── fiwi-server.spec            PyInstaller spec for bundling the server
└── pyproject.toml              Python dependencies and optional extras
```

Further reading:

- [docs/detection-providers.md](docs/detection-providers.md) — Shazam vs ACRCloud vs AcoustID
- [docs/metadata-enrichment.md](docs/metadata-enrichment.md) — enrichment fields and sources

---

## Building a release

Releases are now built automatically by GitHub Actions
([.github/workflows/build.yml](.github/workflows/build.yml); the secrets it needs are
listed at the top of that file). To release a new version, run the following on the
`dev` branch before creating the pull request from `dev` to `main`, with the new
version number in place of `v0.3.0`:

```bash
git tag v0.3.0
git push origin v0.3.0
```

The workflow builds the signed macOS DMG and the Windows installer and attaches them to
a draft release `v0.3.0`; it stops if that release already exists. Review the draft on
the [releases page](https://github.com/simifilm/SoundtrackID/releases) and publish it.

> **Note:** the rest of this chapter describes what needs to be done to build a release
> manually.

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

Copyright 2026 University of Zurich and Zurich University of Applied Sciences. Licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE): free to use, modify and share for any
noncommercial purpose, including research and teaching. Commercial use requires a
separate license.

---

## Credits

A collaborative project between the [University of Zurich](https://www.uzh.ch) and the
[Zurich University of Applied Sciences](https://www.zhaw.ch).

### Contributors

| Role | Name |
|---|---|
| Concept | Simon Spiegel |
| Software Development | Linus Carl Stuhlmann<br>Adrian Thür |
| Academic Supervision | Ahmed Abdulkadir |

### Data sources

<a href="https://www.themoviedb.org/"><img src="src/soundtrackID/static/tmdb-logo.svg" alt="TMDB" height="14"></a>

This product uses the TMDB API but is not endorsed or certified by TMDB. Film metadata
comes from [TMDB](https://www.themoviedb.org/) and is used under the
[TMDB API Terms of Use](https://www.themoviedb.org/api-terms-of-use).

Composer and work data comes from [MusicBrainz](https://musicbrainz.org/) and
[Wikidata](https://www.wikidata.org/).
