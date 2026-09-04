# FIWI Filmmusik — Implementation Backlog

Derived from the last project meeting. Status reflects the codebase as of 2026-09-04 (updated after the ensemble + copyright + editing work).

Legend: ✅ done · 🟡 partial · ⬜ not started

## 1. Date / authorship / copyright logic

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1.1 | Trace back to the **original work** (not the recording/Einspielung) for the true date | ✅ | Wikidata `original_year` + **MusicBrainz work-title fallback** when the reissue ISRC is absent (recovers Sunset Boulevard's Diane→1927). Obviously-wrong reissue years dropped from the Date column. |
| 1.2 | Decisive date = **authors' death year**, not release date | ✅ | All authors' death years resolved (MB life-span → Wikidata via MBID); PD clock = latest surviving author. |
| 1.3 | Keep release date as a **prefilter / data-cleaning** hint | ✅ | `release_year` captured (ACRCloud+Shazam), used as the anachronism fallback signal. |
| 1.4 | Read **all copyright roles — Composer, Lyricist, Writer** — from MusicBrainz; PD only if **all ≥70 y dead**; exclude **Performer** (50 y) | ✅ | `_extract_authors` reads all three roles (performer excluded); `_pd_status` → public_domain true/false/unknown. Shown as the Rights pill + authorship panel. La Cumparsita correctly Protected (lyricist d.1957). |
| 1.5 | Do **not** use IMDb/TMDb composer credit for authorship of pre-existing pieces (names the score composer) | ✅ | `film.music_composer` only *suppresses* anachronism false-positives, never used for PD authorship. Boundary documented in code + enricher notes. |

## 2. Detection quality / bundling

| # | Item | Status | Notes |
|---|------|--------|-------|
| 2.1 | Merge same track across chunks even when a **misclassified track** sits between | ✅ | Solved differently: the **Tracks tab groups by (title, artist)** across the whole film, so scattered occurrences collapse into one entry regardless of gaps. Enrichment now runs **once per unique track**. |
| 2.2 | Compact **"which pieces were found"** overview (no timeline detail) | ✅ | The **Tracks tab** is exactly this (unique pieces, full metadata/rights); the **Timeline tab** keeps the raw sequence. |
| 2.3 | **Anachronism prefilter**, optionally combined with **temporal matching** | ✅ | Prefilter (marking + hide toggle). Temporal matching: ACRCloud candidate list captured; a post-detection pass (`temporal.py`) prefers a period-plausible candidate over an anachronistic top hit when the film year is known (score-guarded); flagged with a ↻ badge. |
| 2.4 | **Ensemble** across providers + evaluate 1–2 more APIs | ✅ | Ensemble + majority voting done (ACRCloud/Shazam/AudD, provider checkboxes, Conf badge + per-provider breakdown). AudD added this session. Further API eval optional. |
| 2.5 | Test **Apple ShazamKit** (Shazam via `shazamio` weak/blank) | ⬜ | Not integrated. |
| 2.6 | **Benchmark harness** against the vTransfer dataset (clips + exact timestamps) | ⬜ | No eval harness yet; dataset received. |

## 3. UI / workflow

| # | Item | Status | Notes |
|---|------|--------|-------|
| 3.1 | Auto-fill film metadata on start (Wikipedia/IMDb/TMDb) | ✅ | Film-confirm step + automatic composer enrichment. |
| 3.2 | Synchronize video + audio into one play control | ✅ | `playSegment` drives the shared player, stops at segment end. |
| 3.4 | **Album column** too wide — compress or click-to-hide | ✅ | Album cell truncates with ellipsis (full text on hover); clicking the **Album header** hides/shows the column. |
| 3.5 | **Hand-labeling**: manually remove/correct wrongly-detected tracks | ✅ | Tracks tab: double-click any field to edit, hover **×** to remove; persisted to disk via `POST /results/{name}` (survives reload + History). |
| 3.6 | **Settings page for API keys** (for commercial use) | ✅ | "API keys" section in Settings; `GET/POST /settings` persist to gitignored `settings.json` and apply to the env (secrets masked, never returned in full). New analyses pick up saved keys. |

## 4. Robustness / bugs

| # | Item | Status | Notes |
|---|------|--------|-------|
| 4.1 | **JSON output error on long films** (suspected limit exceeded) | ✅ | Waveform (up to ~60k pts) no longer embedded in the streamed `done` event; the frontend uses the separate `waveform` step + disk `results.json`. Streamed payload stays small on long films. |
| 4.2 | **Restart / resume** on request-limit errors | ✅ | Provider quota errors raise `RateLimitError`; the pipeline stops calling that provider, keeps identified cues, sets `rate_limited`, and saves partial results. `POST /resume/{name}` re-detects only the still-unidentified cues from their saved segment WAVs. UI shows a Resume banner. |

## 5. Integrations / export

| # | Item | Status | Notes |
|---|------|--------|-------|
| 5.1 | Add the **film-annotation cooperation export format** (new interchange format) | ⬜ | Awaiting documentation link. |
| 5.2 | **SUISA data** API access — follow up | ⬜ | External/strategic; no code yet. |

## Context / handover (not implementation)

- Linus emigrates ~11 Sept; employed to end of October; project handover to discuss next week.
- SUISA confirms the tool's relevance but can't supply the data (≈1.5 of 10 titles documented;
  a ~2000 database migration lost much data). API access to SUISA data is worth pursuing.

## Suggested priority (remaining)

Sections 1, 3, and the 4.x robustness items are done. Remaining open items, roughly by value:

1. **2.6** Benchmark harness against the vTransfer dataset — needed to measure whether the ensemble
   + copyright logic is actually accurate; underpins every further tuning decision.
2. **2.5** Apple ShazamKit — Shazam via `shazamio` returns blank on most film scores; a stronger
   Shazam backend would materially help the ensemble.
3. **5.1** Film-annotation cooperation export format — high external value once the doc link arrives.
4. **5.2** SUISA data API access — external/strategic.
(UI polish 3.4 album compress/hide and 3.6 API-keys settings page are done; 3.3 pop-out player dropped.)

## Done this session
AudD provider; multi-provider ensemble + majority voting; multi-role public-domain logic
(Composer/Lyricist/Writer, all death years, MusicBrainz work-title fallback); Tracks/Timeline split
with enrich-once-per-track; Rights pill + authorship/copyright panel; manual edit + remove (persisted);
rename to **SoundtrackID**; em-dash cleanup.
