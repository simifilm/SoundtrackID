# Audio-identification providers — evaluation for SoundtrackID

_Evaluated 2026-08-25. Use case: identify **short film-music cues** (segments extracted
from a film's audio) and return title / artist / album and — critically — the **ISRC**,
which we chain via MusicBrainz → work → composer/lyricist → death year for the
public-domain assessment._

## The decisive question

Not "does it fingerprint audio?" but **"does it ship with a catalog of commercial music
to match against?"** Two very different product categories:

- **Catalog-backed recognition services** — they hold the music database, so they can
  identify an *unknown* cue. These are the only ones usable for us.
- **Self-hosted ACR engines** — you must build/maintain your *own* reference fingerprint
  database. Excellent for ad/broadcast monitoring or detecting repeats of your own
  content; useless for identifying arbitrary commercial film music we don't already own.

## Comparison

| Service | Type | Music catalog? | Short-cue recognition | Audio heard per clip | ISRC / metadata | Access & cost | Verdict |
|---|---|---|---|---|---|---|---|
| **ACRCloud** | Commercial API | Yes, huge | Yes (snippet fingerprint) | The whole clip, fingerprinted locally; ACRCloud suggests under 15 s | ISRC + Spotify/YT/MusicBrainz | REST, paid (trial tier) | ✅ Best — current default |
| **AudD** | Commercial API | Yes, 160M+ | Yes (one best match per clip; enterprise splits long files into 12 s chunks, with timestamps) | Not documented; the standard endpoint takes files up to 10 MB (about 5 min of the app's 16 kHz mono WAV) | ISRC, UPC, label (Startup plan+) | REST, 300 free then $5→$2 / 1k | ✅ Strong alternative / cross-check |
| **Shazam (ShazamKit)** | Apple framework | Yes, full Shazam catalog | Excellent | About 12 s: the middle 12 s (`shazamio`), the first 10 s (ShazamKit); no matches from 15 s on | ISRC + Apple Music id | Free w/ Apple Dev; native Swift framework, not REST | ✅ Usable (we use unofficial `shazamio`); official = native effort |
| **Gracenote** | Commercial API | Yes, huge | Yes | Not checked | ISRC (must request in query) | Dev license non-commercial only; commercial = enterprise sales | ⚠️ Maybe — heavy onboarding/licensing |
| **AcoustID** | Open / MusicBrainz | Yes (via MusicBrainz) | No — needs ~full track (exact-file fingerprint) | Not checked | MBID → ISRC/composer via MB | Free / open | ⚠️ Limited — free, chains into PD logic, but weak on short cues |
| **EmySound** | Self-host ACR engine | No (bring-your-own DB) | n/a | n/a | No | Free (non-commercial) / self-host | ❌ No commercial catalog |
| **Veritone (aiWARE)** | Enterprise ACR platform | No (your libraries) | n/a | n/a | No | Enterprise | ❌ Wrong tool (known-content monitoring) |
| **Audioneex** | Open-source engine (MPL-2.0) | No (bring-your-own DB) | n/a | n/a | No | Free/open + commercial SDK | ❌ Engine only; no catalog |
| **MADCAT** (Oxford Wave) | Forensic dedup/sync | No | n/a — matches overlapping content between *your* recordings | n/a | No | Commercial | ❌ Not a music-ID service |

## Recommendation

- **Keep ACRCloud as primary** — best fit, already integrated, returns ISRC.
- **Add AudD as a secondary / cross-check** — cleanest drop-in: simple REST, cheap, returns
  ISRC + MusicBrainz links. Good for A/B-ing against ACRCloud to cut "later-sample" false
  matches.
- **Shazam** stays as secondary; the only upgrade is going native ShazamKit for
  reliability/legitimacy vs. the unofficial wrapper.
- **AcoustID** only as a *free* fallback for *longer* segments (its full-file fingerprint
  needs the length), since it chains straight into our MusicBrainz→composer→PD logic.
- **EmySound / Audioneex / Veritone / MADCAT** — not usable: they match against a reference
  DB you supply. (Only relevant if we later want to detect repeats of the *same* cue within
  one film — self-fingerprinting — a different feature.)

## Sources

- AudD — https://audd.io/ , pricing https://audd.io/resources/articles/music-recognition-api-pricing.html , API docs (10 MB limit) https://docs.audd.io/
- ACRCloud identification API (under 15 s suggested) — https://docs.acrcloud.com/reference/identification-api/identification-api
- Shazam matching nothing from 15 s on — https://github.com/shazamio/ShazamIO/issues/150
- EmySound — https://emysound.com/
- Audioneex licensing — https://www.audioneex.com/licensing/
- MADCAT — https://oxfordwaveresearch.com/products/madcat/
- Veritone audio fingerprinting — https://www.veritone.com/aiware/ai-engines/audio-fingerprinting/
- Gracenote ISRC — https://developer.gracenote.com/isrc-code , licensing FAQ https://developer.gracenote.com/faq-page
- ShazamKit — https://developer.apple.com/documentation/ShazamKit
- AcoustID — https://en.wikipedia.org/wiki/AcoustID
