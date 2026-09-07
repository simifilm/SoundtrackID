# Services and APIs overview

Consolidated reference of every external service SoundtrackID uses: its purpose,
the access it needs, and notes on when a provider is excluded or unavailable.
Handover companion to `detection-providers.md` and `metadata-enrichment.md`.

## Music identification

| Service | Purpose | Required access | Env vars | Notes / exclusion reason |
|---------|---------|-----------------|----------|--------------------------|
| ACRCloud | Primary music identification. Returns a ranked candidate list used for majority voting and temporal reranking. | ACRCloud project (host + access key/secret) | `ACRCLOUD_HOST`, `ACRCLOUD_ACCESS_KEY`, `ACRCLOUD_ACCESS_SECRET` | Default provider. Rate-limited (error 3xxx); the pipeline saves partial results and can resume. |
| AudD | Second identification provider; participates in ensemble voting. | AudD API token | `AUDD_API_TOKEN` | Reports a constant confidence of 1.0, so ensemble tie-breaks by provider priority, not confidence. Rate limit codes 900/901 trigger resume. |
| Shazam (shazamio) | Third identification provider (ensemble). Unofficial Shazam client. | None (no key) | - | Interim path. Used because native ShazamKit is currently unshippable (see below). |
| Shazam (native ShazamKit) | Official Apple music matching via a bundled Swift helper. | Apple entitlement `com.apple.developer.shazamkit` in the app's provisioning profile | - | Excluded by default: Apple omits the entitlement from Developer ID profiles (bug FB22582333), so the entitled helper is killed at runtime. Auto-upgrades once Apple fixes it (`SHAZAMKIT_ENABLE=1`). |

## Metadata enrichment

| Service | Purpose | Required access | Env vars | Notes / exclusion reason |
|---------|---------|-----------------|----------|--------------------------|
| TMDb | Film metadata (title, year, director) and IMDb id bridge. | TMDb API v4 bearer token (preferred) or v3 key | `FIWI_TMDB_API_TOKEN`, `FIWI_TMDB_KEY` | Primary film source; Wikidata is the fallback. |
| MusicBrainz | Composer / lyricist / writer and work metadata via the ISRC chain from identified cues, plus work-title lookup. | None (public API, User-Agent only) | - | No key. Public rate limits apply; client uses timeouts and retries. |
| Wikidata | Fallback for film and composer metadata, original release year, composer death year (public-domain check). | None (public SPARQL/API) | - | Fallback only. |

## Build / distribution

| Service | Purpose | Required access | Env vars | Notes |
|---------|---------|-----------------|----------|-------|
| Apple Developer ID + notarization | Sign and notarize the macOS app so it runs without Gatekeeper warnings. | Developer ID Application certificate in keychain + App Store Connect API key (.p8) | `APPLE_SIGNING_IDENTITY`, `APPLE_TEAM_ID`, `APPLE_API_KEY`, `APPLE_API_ISSUER`, `APPLE_API_KEY_PATH` | Registered under the UZH Filmwissenschaft Apple account (team VA5P3GGHKQ). The .p8 key must never be committed or bundled. |

## Key handling

- Keys are read from `.env` (gitignored) at startup and can be overridden per
  install through the in-app settings page, which persists them to
  `settings.json` (gitignored) and pushes them into the process environment.
- No key is committed to the repository. Handover of the actual key values is a
  manual step outside git.
