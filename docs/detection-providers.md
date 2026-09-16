# Music detection providers

SoundtrackID can identify music cues through three different backends. This
document records what each one is, how it behaves, and — importantly — the
empirical findings from testing them against real film audio. The short version:

> **For identifying music *inside films*, ACRCloud has the best recall, Shazam is
> a solid baseline, and AcoustID is unsuitable.**

All three implement `BaseMusicDetectionClient.detect()` in
[`detection.py`](../src/soundtrackID/detection.py) and are selectable via the
**Detection API** dropdown in the UI, the `--api` CLI flag, or
`build_detection_client(...)`.

---

## TL;DR comparison

| | **Shazam** (shazamio) | **ACRCloud** | **AcoustID** |
|---|---|---|---|
| Recognition style | Acoustic (Shazam-class) | Acoustic (Shazam-class) | Exact-fingerprint (Chromaprint) |
| Works on **short** samples (10–15 s) | ✅ yes | ✅ yes | ❌ no |
| Works on **film-mixed** audio (dialogue/SFX over music) | ✅ often | ✅ often | ❌ never observed |
| Needs (near) **full track** to match | no | no | **yes** |
| Matches **exact master only** | no | no | **yes** |
| Film-cue recall (Interstellar, 15 s chunks) | 6/14 | **11–12/14** | 0/14 |
| Recognized a 1950 orchestral score (Sunset Blvd) | — | ✅ real Waxman cues | ❌ |
| False positives on ambiguous audio | rare | **occasional** | none (just no match) |
| Metadata | title, artist, album, ISRC, genre, links | title, artist, album, ISRC, genre, Spotify/YouTube/MusicBrainz | title, artist, album, MusicBrainz ID |
| Credentials required | none | project host + key + secret | API key |
| Native bundling concern | aiohttp (handled) | `acrcloud_extr_tool.so` | `fpcalc` / libchromaprint |
| Cost | free (unofficial API) | metered (free trial) | free |

Numbers come from the test runs documented below.

---

## Test material

| File | Content | Notes |
|---|---|---|
| `b_o_b_d.mp4` | Green Day – *Boulevard of Broken Dreams* (~259 s) | Direct music-video rip ≈ commercial master |
| `interstellar.mp4` | Interstellar clip (~263 s), Hans Zimmer score | Film mix — music under sound design |
| `sunset_boulevard.mp4` | *Sunset Boulevard* (1950), Franz Waxman score | 76-year-old orchestral film score |
| downloaded full tracks | Rick Astley, Coldplay, Nirvana | Clean full commercial recordings |

All pipeline runs used `--no-isolation --chunk-duration 15 --threshold 0.2` with
15 s detection chunks unless stated otherwise.

---

## Shazam (shazamio) — the baseline

Unofficial Shazam API via the `shazamio` library. No credentials needed.

- **Green Day**: 18/18 chunks → *Boulevard of Broken Dreams*.
- **Interstellar**: 6/14 chunks → *Cornfield Chase* and *Flying Drone* (Hans
  Zimmer). Returns the commercial-release cue names.

Strengths: zero setup, good precision (few false positives), rich metadata
(YouTube/Spotify/Apple Music links, ISRC, cover art). Weakness: recall on film
cues is lower than ACRCloud, and it depends on an unofficial endpoint.

---

## ACRCloud — best for film music

Shazam-class acoustic recognition. Fingerprints are extracted **locally** by the
`pyacrcloud` SDK (native `acrcloud_extr_tool.so`) and matched against ACRCloud's
Music Recognition database. Credentials: `ACRCLOUD_HOST`,
`ACRCLOUD_ACCESS_KEY`, `ACRCLOUD_ACCESS_SECRET`.

### Results

- **Green Day**: 18/18 chunks ✅
- **Interstellar**: 11–12/14 chunks — *Cornfield Chase* + *Flying Drone* (Hans
  Zimmer). **More than Shazam's 6/14.** It even matched the *Complete Motion
  Picture Score* edit ("Chasing Drone"). Two false positives in the intro
  (*Murphy's Law – Hoesay*, *Abyss – Golden Corpse*).
- **Sunset Boulevard (1950)**: 14/28 chunks. Correctly identified genuine
  **Franz Waxman** cues:
  - *Afternoon Outings / Sacrifice of Self-Respect* — Franz Waxman
  - *La Cumparsita* — Waxman & Matos Rodríguez
  - *Diane* — Waxman, Rapee & Pollack

  Mixed with false positives (*Rushmore – Ruffles Jenkins*, *See you in my
  nightmares – sxralm*, *Rainy – Rusty Water*).

### The critical setup gotcha

A plain **"Audio Fingerprinting"** ACRCloud project matches only against **your
own uploaded custom bucket** (empty by default) — every commercial track returns
`{"status":{"code":1001,"msg":"No result"}}`, even globally famous ones. The
authentication and fingerprinting still succeed; there is simply nothing in the
attached database.

**You must connect the project to the ACRCloud _Music Recognition_ database**
(create it under *Audio & Video Recognition → Music Recognition* at
[console.acrcloud.com](https://console.acrcloud.com); typically a 14-day free
trial, then metered). Once connected, recognition works immediately.

### Trade-offs

- **Best recall** of the three for film music (short cues, film mixes, even old
  orchestral scores).
- **Occasional false positives** on ambiguous/orchestral passages — obscure
  modern tracks matched at high confidence.
- A **score threshold does not cleanly separate** good from bad: a *legitimate*
  Interstellar cue scored 55 % while a *false* positive scored 100 %. Filtering
  on score alone would drop real cues before it removes the bad ones. The client
  therefore uses `min_score=0.0` by default; FP mitigation (if wanted) should be
  structural (e.g. cross-check with Shazam, or discard isolated single-chunk
  matches) rather than score-based.

---

## AcoustID — unsuitable for film music

AcoustID identifies audio by an **exact Chromaprint fingerprint** matched against
a crowd-sourced database (built from people fingerprinting their own music
files → MusicBrainz). It is designed for **tagging complete files that _are_ a
released recording**, not for recognizing music playing in a scene.

Fingerprints are produced by Chromaprint's `fpcalc` binary; lookups go to the
AcoustID web service (`ACOUSTID_API_KEY`).

### Why it fails here

**1. It needs (near) full-track fingerprints.** Short excerpts do not match.
Measured across four clean commercial tracks (fingerprint of the first *N*
seconds from the start):

| Track | 30 s | 60 s | 90 s | full |
|---|:---:|:---:|:---:|:---:|
| Green Day – Boulevard of Broken Dreams | ✗ | ✗ | — | ✅ 0.98 |
| Rick Astley – Never Gonna Give You Up | ✗ | score .93 / *no title* | ✗ | ✅ 0.98 |
| Coldplay – Viva la Vida | ✗ | ✗ | ✗ | ✅ 0.98 |
| Nirvana – Smells Like Teen Spirit | ✗ | ✗ | score .85 / *no title* | ✅ 0.95 |

Only full-track fingerprints produced a titled match. This was **confirmed with
the reference `pyacoustid` library** (not just our client), so it is genuine
AcoustID behaviour, not an implementation artefact. The partial hits that
returned a score but `title=None` are bare fingerprint matches with no linked
MusicBrainz recording — unusable.

**2. It matches only the exact master.** Film audio is a different mix/master
than the commercial soundtrack (dialogue and SFX layered in, re-mastered,
re-edited), so it does not match. On `interstellar.mp4`, AcoustID returned
**0 matches** even with the whole music segment as one fingerprint — while
Shazam got 6 and ACRCloud got 11–12.

### The `meta` encoding bug (fixed during testing)

Worth recording because it was subtle: the AcoustID lookup was originally sent
with `meta=recordings+releasegroups`. `httpx` percent-encodes the `+` to `%2B`,
so AcoustID received one bogus meta token, ignored it, and returned results with
**zero recording metadata** (a match with `score` but no title/artist). The fix
is a **space-separated** value — `"recordings releasegroups"` — which httpx
encodes as `+` (the query-string space), which AcoustID parses as the delimiter.

### Where AcoustID *would* work

Identifying **whole music files** (e.g. a downloaded track) — feed it the entire
file and it returns title, album, and a MusicBrainz recording ID with high
confidence. That is simply not this project's task, so **AcoustID was removed in
favour of ACRCloud.**

---

## Recommendation

- **Default: Shazam** — no setup, good precision.
- **For maximum film-cue recall: ACRCloud** — connect a Music Recognition
  project; expect a few false positives and review flagged cues.
- **AcoustID**: not used. Kept here only as documented rationale.

## Credentials summary

```
# .env
ACRCLOUD_HOST="identify-eu-west-1.acrcloud.com"
ACRCLOUD_ACCESS_KEY="..."
ACRCLOUD_ACCESS_SECRET="..."
# (Shazam needs nothing.)
```
