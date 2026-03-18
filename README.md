# FIWI Filmmusik Analyzer

Detects and identifies music in film videos. Upload a video through the web UI (or run the CLI) and the pipeline will classify audio chunks, aggregate them into music segments, and identify each segment via Shazam.

## Requirements

- Python 3.10+

## Installation

```bash
git clone https://github.zhaw.ch/stmh/FIWI-Filmmusik.git
cd FIWI-Filmmusik
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[hf,shazam,web]"
```

### Optional: vocal isolation with Demucs

To strip non-music audio before identification (improves accuracy on dialogue-heavy films):

```bash
pip install -e ".[demucs]"
```

> **Note:** Demucs requires a CUDA-capable GPU for practical performance. CPU inference is very slow.

## Running the web app

```bash
python3 -m fiwi_filmmusik
```

Open [http://localhost:8000](http://localhost:8000) in your browser, then drag in a video file.

### Analysis settings

| Setting | Default | Description |
|---|---|---|
| Chunk Duration | 10 s | Length of audio chunks fed to the classifier |
| Sensitivity | 20 % | Music classification threshold (lower = more sensitive) |
| Max Segment to API | 10 s | Each music segment is split into this size before Shazam fingerprinting — must match Shazam's 10 s fingerprint window |
| Export CSV | off | Also download results as CSV (JSON is always downloaded) |

## Running the CLI

```bash
python3 -m fiwi_filmmusik.pipeline path/to/video.mp4 --no-isolation
```

Options:

```
--output-dir DIR      Output directory (default: ./output)
--no-isolation        Skip vocal isolation (faster, uses DummyIsolator)
--chunk-duration SEC  Classifier chunk size in seconds (default: 10)
--threshold FLOAT     Music classification threshold 0–1 (default: 0.2)
```

## Output

Results are written to `output/<video_name>/`:

```
output/
  My Film (2024)/
    results.json        # All segments and identifications
    results.csv         # Optional CSV export
    segments/
      seg_001.wav       # Extracted audio for each music segment
      seg_002.wav
```

## Development

```bash
pip install -e ".[dev]"
pytest
```
