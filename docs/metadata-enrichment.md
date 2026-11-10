# Metadata Enrichment

The metadata enrichment feature adds **film** and **composer** context to an
analysis run. Its main purpose is to support **public-domain (PD)
copyright analysis** of identified music: in CH/EU, music enters the public
domain 70 years after the **composer's** death.

It runs in two places:

- **Film** — before the analysis starts, in the **Confirm film** panel. The
  fields are pre-filled from the video file's MP4 tags; **Look up online** fills
  the empty ones. The confirmed film is passed to `/analyze`, so the film year is
  known while matches are ranked.
- **Composer** — automatically at the end of `/analyze` (and of `/resume`), for
  every identified cue that carries an ISRC.

---

## What gets enriched

### Film block (`results.film`)

```json
{
  "title": "Sunset Boulevard",
  "year": 1950,
  "director": "Billy Wilder",
  "director_death_year": 2002,
  "imdb_id": "tt0043014",
  "tmdb_id": 599,
  "sources": ["tmdb"]
}
```

Rendered as a purple header above the results table. The director's death year
is shown for **context only** — a copyright hint reminds the user that the
operative PD date is the composer's death year, not the director's.

### Per-cue enrichment (`cue.enrichment`)

```json
{
  "composer": "Freddie Mercury",
  "composer_death_year": 1991,
  "work_title": "Bohemian Rhapsody",
  "composer_match": "isrc",
  "sources": ["musicbrainz"]
}
```

Surfaces as three extra columns in the results table:
**Komponist · † · Werk**.

`composer_match` distinguishes confidence:

| Value | Confidence | Provenance | UI |
|---|---|---|---|
| `"isrc"` | High | MusicBrainz ISRC chain | Normal styling |
| `"search"` | Low | Wikidata title+artist search | Italic + dimmed + `?` badge with tooltip |
| `null` | None | No source had a hit | Empty cell |

---

## Data flow

```
                   ┌───────────────────────────────────────────────────────┐
   Upload          │  POST /identify → stage the upload, read MP4 tags     │
                   │  via mutagen (Subler-compatible):                     │
                   │    • ©nam → title    ©day → year                      │
                   │    • iTunMOVI plist → director                        │
                   │    • ----:com.apple.iTunes: → IMDb/TMDb IDs           │
                   └───────────────────────────┬───────────────────────────┘
                                               ▼
                   ┌───────────────────────────────────────────────────────┐
   Confirm film    │  Panel pre-filled from the tags; the user edits it    │
                   │  "Look up online" → POST /film/lookup                 │
                   │    (TMDb → Wikidata → imdbapi.dev, fills empty        │
                   │    fields only)                                       │
                   └───────────────────────────┬───────────────────────────┘
                                               ▼
                   ┌───────────────────────────────────────────────────────┐
   Analyze         │  POST /analyze {staging_id, film}                     │
                   │    ├─ pipeline.run(...) → cues; results.film = film   │
                   │    ├─ temporal re-rank using the film year            │
                   │    └─ composer lookup for every cue with an ISRC      │
                   │       (SSE step "enriching", detail "i/total")        │
                   │                                                       │
                   │  results.json written to disk                         │
                   └───────────────────────────────────────────────────────┘
```

---

## Lookup chains

### Film: TMDb → Wikidata → imdbapi.dev

| Source | Role | Why |
|---|---|---|
| **TMDb** | Primary | Best coverage; reliable API; auth via v4 Bearer header (no key leakage into proxy logs) |
| **Wikidata** | Strong secondary | Free, open SPARQL; good director death-year data via `wdt:P570` |
| **imdbapi.dev** | Tertiary fallback | Unofficial, no SLA; only invoked when prior sources lack director or director death year; failures swallowed silently |

Merge rule: first non-null value wins per field. The `sources` list records
which API supplied which data.

TMDb chain for a film:
1. `/3/find/{imdb_id}` (by IMDb ID) or `/3/search/movie?query=...` (by title)
2. `/3/movie/{tmdb_id}` + `/3/movie/{tmdb_id}/credits`
3. If director found but no death year: `/3/person/{director_id}` for `deathday`

### Composer: MusicBrainz → Wikidata

| Source | Role | When |
|---|---|---|
| **MusicBrainz (ISRC chain)** | Primary, high confidence | When cue has an ISRC |
| **Wikidata (P434 cross-ref)** | Death-year top-up | When MB found composer but lacks `life-span.end` — looked up by **MusicBrainz artist ID** via Wikidata's P434 property, *not* by name (avoids name collisions like multiple "Hans Zimmer") |
| **Wikidata (title+artist search)** | Last-resort fallback, low confidence | When any MB chain hop misses; tagged `composer_match: "search"` |

Only cues with an ISRC are looked up; cues without one keep no composer unless
it is entered by hand.

MusicBrainz ISRC chain:
1. `GET /ws/2/isrc/{isrc}` → recordings (note: no `inc` param — recordings nest directly)
2. `GET /ws/2/recording/{rec_id}?inc=work-rels` → linked work
3. `GET /ws/2/work/{work_id}?inc=artist-rels` → composer relation → artist MBID + work title
4. `GET /ws/2/artist/{artist_id}` → `life-span.end`

MusicBrainz is rate-limited to **1 req/sec** with a polite User-Agent header.
Sleep is enforced class-level by the last-call timestamp; cache hits skip the
sleep.

### Intra-run caching

`MusicLookup` keeps four per-instance dicts that survive one run's composer
lookup:

- `recording_id → work_data`
- `work_id → composer_relations`
- `artist_id → life_span`
- `wikidata_name → death_year`

A film with 30 cues built around 5 unique composers issues roughly **20 API
calls instead of ~150**.

---

## TMDb credentials (`.env`)

```bash
FIWI_TMDB_API_TOKEN=eyJhbGc...      # v4 Bearer JWT (preferred — header auth)
FIWI_TMDB_KEY=xxxxxxxxxxxxxx         # v3 hex (fallback — query param)
```

Loaded by `python-dotenv` at server startup. `.env` is gitignored. For the
bundled `.app`, the PyInstaller spec bundles `.env` next to the binary so
production builds pick up the same credentials.

When the Bearer token is present, the v3 query param is never used — keys do
not leak into HTTP access logs or proxy traces.

If neither credential is set, the film chain skips TMDb and falls through to
Wikidata directly.

---

## HTTP API

### `POST /identify`

Multipart upload of the video (`file`). Stages it for `/analyze` and reads its
MP4 tags. Returns `{staging_id, original_name, film}`, where `film` holds
whatever the tags provided.

### `POST /film/lookup`

JSON body with any of `imdb_id`, `tmdb_id`, `title`, `year`. Runs the film
chain (TMDb → Wikidata → imdbapi.dev) and returns `{film: {...}}`; `502` on an
unexpected failure.

### Composer progress

The composer lookup has no endpoint of its own. `/analyze` and `/resume` stream
it as `{"step": "enriching", "detail": "<done>/<total>"}` events, counting
unique tracks (a piece that recurs in the film is looked up once).

---

## Realistic timing

Per cue without cache hits (4 sequential MB calls × 1 s rate-limit sleep, plus
Wikidata fallback ~0.5 s) is roughly **~4.5 s/cue**.

| Scenario | Approx. time |
|---|---|
| 30 cues, all unique composers | ~2:15 |
| 30 cues, 5 recurring composers | ~30–60 s (cache hits skip the sleep) |
| Pop-heavy film with strong MB coverage | Faster — most chains complete |
| Score-heavy art film | Slower and lower hit rate — many cues end null or fall back to Wikidata search |

The analysis progress shows this as the **Identifying composers** step with a
`current / total` counter; cancelling the analysis also stops the lookup.

---

## Known coverage limits

- **MusicBrainz coverage is the binding constraint**, especially for film
  scores. The Shazam match identifies a commercial recording, but that
  recording may not be in MB, may have no linked work, or the work may have no
  composer artist-relation. Expect a substantial fraction of cues —
  potentially the majority for non-pop material — to come back with no
  high-confidence composer.
- **Wikidata title+artist search is unreliable.** Classical works in
  particular rarely have a label match for the form Shazam returns
  ("Symphony No. 9" vs the canonical Wikidata label). Any result from this
  fallback is rendered as low-confidence (italic + `?`), never as fact.
- **imdbapi.dev is unofficial.** No SLA, has already migrated v1 → v2.
  Treated as tertiary fallback only; failures are swallowed silently. If it
  goes dark permanently, TMDb + Wikidata still carry the feature.
- **Director death year is informational, not authoritative for PD.** The UI
  surfaces it for context with an explicit hint that the operative PD date is
  the composer's death year.

---

## File layout

```
src/soundtrackID/metadata/
  __init__.py
  mp4_tags.py      # mutagen-based Subler-compatible MP4 atom reader
  film.py          # TMDb + Wikidata + imdbapi.dev — FilmInfo + FilmLookup
  music.py         # MusicBrainz + Wikidata — ComposerInfo + MusicLookup
  enricher.py      # Pure orchestrator: composer lookup for a results dict

tests/metadata/
  test_film_parse.py    # Parser tests with checked-in JSON fixtures (no network)
  test_music_parse.py
  fixtures/             # API response samples
```

The parsers are pure functions (`_parse_tmdb_movie`, `_parse_wikidata_film`,
`_parse_imdbapi_title`, `_extract_composer`, `_artist_death`,
`_parse_wikidata_composer`, …) and are unit-tested directly against checked-in
JSON fixtures. No vcrpy, no integration tests, no network in CI.

---

## Verification status

- 17/17 parser tests pass (`pytest tests/metadata/`)
