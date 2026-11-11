"""Music detection API."""

import json
import logging
import os
import re
import tempfile
import threading
from abc import ABC, abstractmethod

import numpy as np

from soundtrackID.models import (
    MATCH_KEYS,
    DetectionResult,
    MusicSegment,
    parse_year,
)

log = logging.getLogger(__name__)


def _find_shazamkit_helper() -> str | None:
    """Locate the compiled ShazamKit helper binary (macOS only).

    The helper ships as a minimal .app bundle (shazamkit-match.app) so it can
    carry its own provisioning profile, which ShazamKit requires; the bare
    binary is only a fallback (it always gets ShazamKit error 202).

    Order: SHAZAMKIT_HELPER env override → bundle, then bare binary, in
    sys._MEIPASS / next to the bundled binary (frozen app) or in the repo's
    shazamkit/ dir (dev build)."""
    import sys
    from pathlib import Path

    in_bundle = Path("shazamkit-match.app") / "Contents" / "MacOS" / "shazamkit-match"
    override = os.environ.get("SHAZAMKIT_HELPER")
    candidates = [Path(override)] if override else []
    if getattr(sys, "frozen", False):
        bases = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass))
        bases.append(Path(sys.executable).parent)
        candidates += [b / in_bundle for b in bases] + [
            b / "shazamkit-match" for b in bases
        ]
    else:
        shazamkit_dir = Path(__file__).resolve().parents[2] / "shazamkit"
        candidates.append(shazamkit_dir / "build" / in_bundle)
        candidates.append(shazamkit_dir / "shazamkit-match")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


class RateLimitError(RuntimeError):
    """A provider can't answer for now: it refused the request at its quota or
    rate limit, failed (HTTP 5xx), or couldn't be reached.

    Raised so the pipeline can stop calling that provider, keep whatever it has
    already identified, and offer a resume rather than losing the whole run.
    """


class ProviderAuthError(RuntimeError):
    """A provider rejected its API keys.

    Waiting doesn't fix that, so unlike a RateLimitError it stops the run (or
    resume) at once and says so, instead of leaving cues for a resume that
    would fail the same way.
    """


_CHECK_KEYS = "check them under Settings → API keys (the CLI reads .env.dev)"

# ACRCloud's codes for wrong keys: 3001 missing / invalid access key, 3014
# invalid signature (a wrong access secret).
_ACRCLOUD_KEY_ERRORS = (3001, 3014)


def _parse_acr_candidate(m: dict) -> dict:
    """Parse one ACRCloud music candidate into a flat cue-shaped dict."""
    ext = m.get("external_metadata", {})
    spotify = ext.get("spotify", {}).get("track", {}).get("id")
    youtube = ext.get("youtube", {}).get("vid")
    mb = ext.get("musicbrainz")
    mb_id = mb[0].get("track", {}).get("id") if isinstance(mb, list) and mb else None
    genres = m.get("genres") or []
    release_year = parse_year(m.get("release_date")) or parse_year(
        m.get("album", {}).get("release_date")
    )
    return {
        "title": m.get("title"),
        "artist": ", ".join(a["name"] for a in m.get("artists", []) if a.get("name"))
        or None,
        "album": m.get("album", {}).get("name"),
        "release_year": release_year,
        "isrc": m.get("external_ids", {}).get("isrc"),
        "genre": genres[0].get("name") if genres else None,
        "acrcloud_id": m.get("acrid"),
        "score": float(m.get("score", 0.0)),
        "spotify_url": f"https://open.spotify.com/track/{spotify}" if spotify else None,
        "youtube_link": (
            f"https://www.youtube.com/watch?v={youtube}" if youtube else None
        ),
        "musicbrainz_recording_id": mb_id,
        "musicbrainz_url": (
            f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None
        ),
    }


def _shazamio_http_client():
    """shazamio's default HTTP client, except that Shazam still throttling (HTTP
    429) or failing (5xx) after all its retries, or not reachable, raises
    RateLimitError: shazamio returns such a response, or raises, and either
    reads as "no match"."""
    import asyncio

    import aiohttp
    from aiohttp_retry import ExponentialRetry, RetryClient
    from shazamio.client import HTTPClient
    from shazamio.utils import validate_json

    class Client(HTTPClient):
        async def request(self, method, url, *args, **kwargs):
            try:
                async with RetryClient(
                    retry_options=self.retry_options,
                    raise_for_status=False,
                    trace_configs=[self.trace_config],
                ) as client:
                    async with client.request(method, url, **kwargs) as resp:
                        if resp.status == 429:
                            raise RateLimitError("Shazam rate limit (HTTP 429)")
                        if resp.status >= 500:
                            raise RateLimitError(f"Shazam error: HTTP {resp.status}")
                        return await validate_json(resp, *args)
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                raise RateLimitError(f"Shazam unreachable: {exc!r}") from exc

    # The retries of shazamio's default client: about 12 minutes on a 429.
    return Client(
        retry_options=ExponentialRetry(
            attempts=20, max_timeout=60, statuses={500, 502, 503, 504, 429}
        )
    )


class BaseMusicDetectionClient(ABC):
    @abstractmethod
    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in a segment."""
        ...


class ShazamDetectionClient(BaseMusicDetectionClient):
    """Music detection via Shazam.

    Prefers Apple's official **ShazamKit** through a small native Swift helper
    (``shazamkit-match.app``). On macOS, ShazamKit only authorizes a caller that
    carries an embedded provisioning profile for an App ID with the ShazamKit App
    Service, so the helper ships as its own small .app with its own profile; an
    unauthorized helper (e.g. an unsigned dev build) gets error 202. On 202 or any
    other error (or off macOS / no helper), this client **falls back to
    shazamio**. A genuine ShazamKit "no match" is trusted and does not fall back.
    """

    _warned = False

    def __init__(self, helper_path: str | None = None, language: str = "en-US"):
        self._helper = helper_path or _find_shazamkit_helper()
        self._language = language
        if not self._helper:
            # Then shazamio is the only way to ask Shazam: fail here rather than
            # with a "no match" for every segment.
            try:
                import shazamio  # noqa: F401
            except ImportError as exc:
                raise ImportError(
                    "Shazam without the ShazamKit helper requires the 'shazam' "
                    "extras. Install with: pip install soundtrackID[shazam]"
                ) from exc

    def detect(self, segment: MusicSegment) -> DetectionResult:
        empty = DetectionResult(
            segment=segment, title=None, artist=None, provider="shazam"
        )
        # Try ShazamKit first (best source when available).
        if self._helper:
            result, available = self._detect_shazamkit(segment)
            if result is not None:
                return result  # a real match
            if available:
                return empty  # ShazamKit worked and said "no match" — trust it
            # else: ShazamKit unavailable (202 / broke) -> fall through to shazamio
        # Fallback: shazamio.
        return self._detect_shazamio(segment) or empty

    def _detect_shazamkit(self, segment: MusicSegment):
        """Return (DetectionResult|None, available). available=False means ShazamKit
        couldn't run (missing entitlement/helper error) so the caller should fall back.
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            segment.write_wav(temp_path)

        import subprocess

        try:
            proc = subprocess.run(
                [self._helper, temp_path], capture_output=True, text=True, timeout=30
            )
        except (subprocess.TimeoutExpired, OSError):
            return None, False
        finally:
            os.unlink(temp_path)

        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None, False

        if data.get("title"):
            return (
                DetectionResult(
                    segment=segment,
                    title=data.get("title"),
                    artist=data.get("artist"),
                    provider="shazam",
                    metadata={
                        "isrc": data.get("isrc"),
                        "apple_music_url": data.get("appleMusicURL"),
                        "shazam_id": data.get("shazamID"),
                    },
                ),
                True,
            )
        # 202 = missing entitlement -> unavailable (fall back). result:null -> a real
        # ShazamKit no-match (available, trust it). Other errors -> unavailable.
        if data.get("code") == 202 or data.get("error"):
            if data.get("code") == 202 and not ShazamDetectionClient._warned:
                ShazamDetectionClient._warned = True
                log.warning(
                    "ShazamKit unavailable (202: no entitlement/profile yet) "
                    "— falling back to shazamio until it is activated."
                )
            return None, False
        return None, True  # {"result": null}: ShazamKit ran, no match

    def _detect_shazamio(self, segment: MusicSegment) -> DetectionResult | None:
        try:
            import asyncio

            from shazamio import Shazam
        except ImportError:
            return None

        async def _run() -> dict:
            shazam = Shazam(
                language=self._language,
                http_client=_shazamio_http_client(),
                segment_duration_seconds=12,
            )
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
                segment.write_wav(path)
            try:
                return await shazam.recognize(path)
            finally:
                os.unlink(path)

        try:
            result = asyncio.run(_run())
        except RateLimitError:
            raise
        except Exception:
            return None

        track = result.get("track")
        if not track:
            return DetectionResult(
                segment=segment,
                title=None,
                artist=None,
                provider="shazam",
            )

        album = release_year = None
        sections = track.get("sections", [])
        if sections:
            meta = sections[0].get("metadata", [])
            if meta:
                album = meta[0].get("text")
            for item in meta:
                if str(item.get("title", "")).strip().lower() == "released":
                    release_year = parse_year(item.get("text"))
                    break
        youtube_link = next(
            (s.get("youtubeurl") for s in sections if s.get("type") == "VIDEO"), None
        )
        hub = track.get("hub", {})
        providers = hub.get("providers", [])
        spotify_url = (
            providers[0]["actions"][0]["uri"]
            if providers and providers[0].get("actions")
            else None
        )
        options = hub.get("options", [])
        apple_music_url = (
            options[0]["actions"][0]["uri"]
            if options and options[0].get("actions")
            else None
        )
        return DetectionResult(
            segment=segment,
            title=track.get("title"),
            artist=track.get("subtitle"),
            provider="shazam",
            metadata={
                "album": album,
                "release_year": release_year,
                "genre": track.get("genres", {}).get("primary"),
                "isrc": track.get("isrc"),
                "photo_url": track.get("images", {}).get("coverart"),
                "youtube_link": youtube_link,
                "spotify_url": spotify_url,
                "apple_music_url": apple_music_url,
            },
        )


class ACRCloudDetectionClient(BaseMusicDetectionClient):
    """Music detection via ACRCloud.

    Extracts an ACRCloud fingerprint from each segment locally (via the
    ``pyacrcloud`` SDK) and identifies it against ACRCloud's Music Recognition
    database. Unlike AcoustID, ACRCloud is Shazam-class: it recognises short,
    degraded, film-mixed audio, so it works on individual film cues.

    Requires the ``pyacrcloud`` SDK and a project connected to the ACRCloud
    Music database. Credentials come from the environment:
    ``ACRCLOUD_HOST``, ``ACRCLOUD_ACCESS_KEY``, ``ACRCLOUD_ACCESS_SECRET``.
    """

    def __init__(
        self,
        host: str | None = None,
        access_key: str | None = None,
        access_secret: str | None = None,
        min_score: float = 0.0,
    ):
        host = host or os.environ.get("ACRCLOUD_HOST")
        access_key = access_key or os.environ.get("ACRCLOUD_ACCESS_KEY")
        access_secret = access_secret or os.environ.get("ACRCLOUD_ACCESS_SECRET")
        if not (host and access_key and access_secret):
            raise RuntimeError(
                "ACRCloud credentials missing — set ACRCLOUD_HOST, "
                "ACRCLOUD_ACCESS_KEY and ACRCLOUD_ACCESS_SECRET (create a "
                "Music Recognition project at https://console.acrcloud.com)."
            )
        # Keep the score floor low by default: legitimate film cues can score
        # well below commercial tracks (a real Interstellar cue matched at 55%).
        self._min_score = min_score

        self._config = {
            "host": host,
            "access_key": access_key,
            "access_secret": access_secret,
            "timeout": 10,
        }
        # The pipeline identifies chunks concurrently across worker threads. Give
        # each thread its own ACRCloudRecognizer (construction only stores config,
        # no I/O) to sidestep any non-reentrancy in the native acrcloud_extr_tool.
        self._local = threading.local()
        # Prime the constructing thread's recognizer so a missing pyacrcloud SDK
        # fails here rather than on the first detect().
        _ = self._recognizer

    @property
    def _recognizer(self):
        recognizer = getattr(self._local, "recognizer", None)
        if recognizer is None:
            from acrcloud.recognizer import ACRCloudRecognizer

            recognizer = ACRCloudRecognizer(self._config)
            self._local.recognizer = recognizer
        return recognizer

    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in segment using ACRCloud."""
        empty = DetectionResult(
            segment=segment,
            title=None,
            artist=None,
            provider="acrcloud",
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            segment.write_wav(temp_path)

        duration = max(1, int(round(len(segment.audio) / segment.sample_rate)))
        try:
            raw = self._recognizer.recognize_by_file(temp_path, 0, duration)
        finally:
            os.unlink(temp_path)

        payload = json.loads(raw)
        code = payload.get("status", {}).get("code")
        if code != 0:
            # 1001 = no result; 2xxx = recording/fingerprint issues (tolerate,
            # treat as no match). Wrong keys stop the run; other 3xxx (quota,
            # QpS limit, HTTP error) stop the calls and allow a resume.
            msg = payload.get("status", {}).get("msg")
            if code in _ACRCLOUD_KEY_ERRORS:
                raise ProviderAuthError(
                    f"ACRCloud rejected its API keys (error {code}: {msg}): "
                    + _CHECK_KEYS
                )
            if isinstance(code, int) and 3000 <= code < 4000:
                raise RateLimitError(f"ACRCloud error {code}: {msg}")
            return empty

        music = payload.get("metadata", {}).get("music", [])
        if not music:
            return empty

        # ACRCloud returns candidates highest-score first. Parse the top few so a
        # later temporal-rerank pass (which knows the film year) can prefer a
        # period-plausible candidate over an anachronistic top hit.
        candidates = [_parse_acr_candidate(m) for m in music[:5]]
        primary = candidates[0]
        if primary["score"] < self._min_score:
            return empty

        md = {k: v for k, v in primary.items() if k not in ("title", "artist", "score")}
        md["candidates"] = candidates
        return DetectionResult(
            segment=segment,
            title=primary["title"],
            artist=primary["artist"],
            provider="acrcloud",
            metadata=md,
        )


class AudDDetectionClient(BaseMusicDetectionClient):
    """Music detection via AudD (https://audd.io).

    AudD is a catalog-backed, Shazam-class recognizer: a single multipart POST
    of the audio to ``https://api.audd.io/`` returns the matched track with
    Apple Music / Spotify / MusicBrainz metadata (including ISRC, which feeds
    the composer-enrichment chain). No SDK is required.

    The API token comes from the environment: ``AUDD_API_TOKEN``.
    """

    ENDPOINT = "https://api.audd.io/"

    def __init__(self, api_token: str | None = None):
        api_token = api_token or os.environ.get("AUDD_API_TOKEN")
        if not api_token:
            raise RuntimeError(
                "AudD API token missing — set AUDD_API_TOKEN (get a token at "
                "https://dashboard.audd.io)."
            )
        self._token = api_token

    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in segment using AudD."""
        import httpx

        empty = DetectionResult(
            segment=segment, title=None, artist=None, provider="audd"
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            segment.write_wav(temp_path)

        try:
            with open(temp_path, "rb") as fh:
                # httpx.Client is created per call — network-bound and safe under
                # the pipeline's concurrent detection threads.
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        self.ENDPOINT,
                        data={
                            "api_token": self._token,
                            "return": "apple_music,spotify,musicbrainz",
                        },
                        files={"file": fh},
                    )
        except httpx.HTTPError as exc:
            # Nobody listened to this segment: stop and allow resume, as
            # ACRCloud does with its code 3000, instead of failing the run.
            raise RateLimitError(f"AudD unreachable: {exc}") from exc
        finally:
            os.unlink(temp_path)

        try:
            payload = resp.json()
        except ValueError as exc:
            # A server error page instead of an answer: also try again later.
            raise RateLimitError(f"AudD error: HTTP {resp.status_code}") from exc
        status = payload.get("status")
        if status == "error":
            err = payload.get("error", {}) or {}
            code = err.get("error_code")
            # 900 = no/invalid api_token: stop the run. 901 = request limit
            # reached: stop the calls and allow resume (as ACRCloud's 3003).
            if code == 900:
                raise ProviderAuthError(
                    f"AudD rejected its API token (error 900: "
                    f"{err.get('error_message')}): " + _CHECK_KEYS
                )
            if code == 901:
                raise RateLimitError(f"AudD error {code}: {err.get('error_message')}")
            return empty

        result = payload.get("result")
        if not result:
            return empty

        apple = result.get("apple_music") or {}
        spotify = result.get("spotify") or {}
        mb = result.get("musicbrainz") or []

        mb_first = mb[0] if isinstance(mb, list) and mb else {}
        # Apple Music / Spotify blocks can be missing even when requested; the
        # MusicBrainz recording usually carries the ISRC too.
        mb_isrcs = mb_first.get("isrcs") or []
        isrc = (
            apple.get("isrc")
            or (spotify.get("external_ids") or {}).get("isrc")
            or (mb_isrcs[0] if mb_isrcs else None)
        )
        spotify_url = (spotify.get("external_urls") or {}).get("spotify")
        apple_music_url = apple.get("url")
        mb_id = mb_first.get("id")

        return DetectionResult(
            segment=segment,
            title=result.get("title"),
            artist=result.get("artist"),
            provider="audd",
            metadata={
                "album": result.get("album"),
                "release_year": parse_year(result.get("release_date")),
                "isrc": isrc,
                "genre": None,
                "song_link": result.get("song_link"),
                "spotify_url": spotify_url,
                "apple_music_url": apple_music_url,
                "youtube_link": None,
                "musicbrainz_recording_id": mb_id,
                "musicbrainz_url": (
                    f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None
                ),
            },
        )


# ── Ensemble (multi-provider majority voting) ────────────────────────────

# Provider priority for tie-breaks and canonical title/artist selection, based
# on observed catalog quality for film scores (ACRCloud strongest).
_PROVIDER_PRIORITY = ["acrcloud", "shazam", "audd"]

# Metadata keys merged across a winning cluster (first non-null by priority),
# so the consensus keeps the best ISRC + platform links any provider supplied:
# every field a cue takes from an answer, but those the ensemble sets itself.
_MERGE_KEYS = tuple(
    key
    for key in MATCH_KEYS
    if key not in ("provider", "title", "artist", "agreement", "votes")
)


def _normalize_title(s: str | None) -> str:
    """Collapse a track title to a comparison key: drop parentheticals,
    bracketed tags, feat./remaster suffixes, and punctuation."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # (From "Interstellar")
    s = re.sub(r"\[[^\]]*\]", " ", s)  # [Remastered]
    s = re.sub(r"\bfeat\.?.*$", " ", s)  # feat. …
    s = re.sub(r"-\s*remaster(ed)?.*$", " ", s)  # - Remastered 2011
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_artist(s: str | None) -> str:
    """Normalize the primary artist (first credited) for comparison."""
    if not s:
        return ""
    s = s.lower().split(",")[0].split("&")[0].split(" feat")[0]
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_key(vote: dict) -> tuple[str, str] | None:
    t = _normalize_title(vote.get("title"))
    return (t, _normalize_artist(vote.get("artist"))) if t else None


def _isrc_key(vote: dict) -> str | None:
    isrc = (vote.get("isrc") or "").strip().upper()
    return isrc or None


def _cluster_votes(matched: list[dict]) -> list[list[int]]:
    """Greedy-cluster matched votes that agree, where two votes agree when they
    share an ISRC or a normalized title+artist. Votes are processed in the given
    (priority) order; returns clusters as lists of indices into `matched`."""
    clusters: list[dict] = []  # {"isrcs": set, "titles": set, "members": [idx]}
    for i, v in enumerate(matched):
        ik, tk = _isrc_key(v), _title_key(v)
        found = None
        for c in clusters:
            if (ik and ik in c["isrcs"]) or (tk and tk in c["titles"]):
                found = c
                break
        if found is None:
            found = {"isrcs": set(), "titles": set(), "members": []}
            clusters.append(found)
        if ik:
            found["isrcs"].add(ik)
        if tk:
            found["titles"].add(tk)
        found["members"].append(i)
    return [c["members"] for c in clusters]


def _consensus_from_votes(
    votes: list[dict], total: int
) -> tuple[str | None, str | None, dict, dict]:
    """Pure voting core. `votes` is every provider's result in priority order
    (each a dict with provider/title/artist/isrc/matched + metadata).
    Returns (title, artist, merged_metadata, agreement)."""
    matched = [v for v in votes if v.get("matched")]
    if not matched:
        return None, None, {}, {"count": 0, "total": total, "providers": []}

    clusters = _cluster_votes(matched)

    # Winner: most providers, then — since only ACRCloud scores its matches,
    # and AudD and Shazam don't — the cluster containing the highest-priority
    # provider. `matched` is in priority order, so the smallest member index is
    # best.
    winner = max(clusters, key=lambda m: (len(m), -min(m)))
    members = [matched[i] for i in winner]

    rep = members[0]  # highest-priority provider in the cluster → canonical text
    merged: dict = {}
    for key in _MERGE_KEYS:
        for m in members:
            val = (m.get("metadata") or {}).get(key)
            if val is not None:
                merged[key] = val
                break

    agreement = {
        "count": len(members),
        "total": total,
        "providers": [m["provider"] for m in members],
    }
    return rep.get("title"), rep.get("artist"), merged, agreement


class EnsembleDetectionClient(BaseMusicDetectionClient):
    """Run several providers per segment and return a consensus by majority vote.

    Presents as an ordinary detection client, so the pipeline's grouping, merging
    and writing are unchanged. The consensus carries two extra metadata keys:
    ``agreement`` (how many providers concurred) and ``votes`` (each provider's
    raw answer), which the frontend renders as a confidence badge + breakdown.
    """

    def __init__(self, clients: list[tuple[str, BaseMusicDetectionClient]]):
        if not clients:
            raise ValueError("EnsembleDetectionClient needs at least one client")
        self._clients = list(clients)

    def detect(self, segment: MusicSegment) -> DetectionResult:
        from concurrent.futures import ThreadPoolExecutor

        def run(name: str, client: BaseMusicDetectionClient):
            try:
                return name, client.detect(segment), None, False
            except ProviderAuthError:
                # Voting on without it would quietly drop a chosen provider.
                raise
            except RateLimitError as exc:
                return name, None, str(exc), True
            except Exception as exc:  # one provider failing must not sink the run
                log.warning("%s failed", name, exc_info=True)
                return name, None, str(exc), False

        with ThreadPoolExecutor(max_workers=len(self._clients)) as ex:
            raw = [f.result() for f in [ex.submit(run, n, c) for n, c in self._clients]]

        # If no provider answered, nobody listened to this window: propagate, so
        # the pipeline keeps it pending for a resume instead of recording it as
        # "no match". With one rate-limited, the ensemble is throttled and the
        # pipeline stops calling it; when all failed otherwise, it asks on.
        if not any(r is not None for _, r, _, _ in raw):
            errors = "no provider answered: " + "; ".join(e for _, _, e, _ in raw if e)
            if any(rl for _, _, _, rl in raw):
                raise RateLimitError(errors)
            raise RuntimeError(errors)
        raw = [(n, r, e) for n, r, e, _ in raw]

        # Keep providers in the configured priority order.
        order = {n: i for i, (n, _) in enumerate(self._clients)}
        raw.sort(key=lambda t: order.get(t[0], 99))

        votes: list[dict] = []
        for name, result, error in raw:
            matched = bool(result and result.title)
            votes.append(
                {
                    "provider": name,
                    "title": result.title if result else None,
                    "artist": result.artist if result else None,
                    "isrc": (result.metadata.get("isrc") if result else None),
                    "matched": matched,
                    "metadata": (result.metadata if result else {}),
                    "error": error,
                }
            )

        title, artist, merged, agreement = _consensus_from_votes(
            votes, total=len(self._clients)
        )

        public_votes = [
            {
                k: v[k]
                for k in ("provider", "title", "artist", "isrc", "matched", "error")
            }
            for v in votes
        ]

        return DetectionResult(
            segment=segment,
            title=title,
            artist=artist,
            provider="ensemble",
            metadata={**merged, "agreement": agreement, "votes": public_votes},
        )


def _build_single_client(api: str) -> BaseMusicDetectionClient:
    if api == "acrcloud":
        return ACRCloudDetectionClient()
    if api == "shazam":
        return ShazamDetectionClient()
    if api == "audd":
        return AudDDetectionClient()
    raise ValueError(f"Unknown detection API: {api!r}")


def build_detection_client(api: str = "acrcloud") -> BaseMusicDetectionClient:
    """Instantiate a detection client from a provider name or a comma-separated
    list. One name → that single client; several → an EnsembleDetectionClient
    that majority-votes across them. Known names: acrcloud, shazam, audd."""
    names: list[str] = []
    for part in api.split(","):
        part = part.strip()
        if part and part not in names:
            names.append(part)
    if not names:
        raise ValueError("No detection provider specified")
    if len(names) == 1:
        return _build_single_client(names[0])
    ordered = sorted(
        names,
        key=lambda n: _PROVIDER_PRIORITY.index(n) if n in _PROVIDER_PRIORITY else 99,
    )
    return EnsembleDetectionClient([(n, _build_single_client(n)) for n in ordered])


if __name__ == "__main__":
    import argparse

    from scipy.io import wavfile as scipy_wavfile

    from soundtrackID.defaults import DEFAULT_API
    from soundtrackID.envfiles import load_env_files

    load_env_files()  # .env.dev, then .env, like the app and the CLI

    parser = argparse.ArgumentParser(
        description="Identify music in one or more WAV files via Shazam or ACRCloud."
    )
    parser.add_argument("wav_files", nargs="+", help="WAV file(s) to identify")
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
        help="Detection provider(s): acrcloud, shazam, audd — or a comma-separated "
        f"list for ensemble majority voting (default: {DEFAULT_API})",
    )
    args = parser.parse_args()

    client = build_detection_client(args.api)
    print(f"Using provider: {args.api}")

    for wav_path in args.wav_files:
        print(f"\nProcessing: {wav_path}")

        # Load WAV file
        sample_rate, audio_data = scipy_wavfile.read(wav_path)

        # Convert to float32 if needed
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32767.0
        elif audio_data.dtype == np.int32:
            audio_data = audio_data.astype(np.float32) / 2147483647.0

        # Handle stereo by taking mean
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)

        segment = MusicSegment(
            audio=audio_data,
            start_time=0.0,
            end_time=len(audio_data) / sample_rate,
            sample_rate=sample_rate,
        )

        result = client.detect(segment)

        if result.title:
            print(f"  Title:      {result.title}")
            print(f"  Artist:     {result.artist}")
            if result.metadata.get("album"):
                print(f"  Album:      {result.metadata['album']}")
            if result.metadata.get("genre"):
                print(f"  Genre:      {result.metadata['genre']}")
            if result.metadata.get("musicbrainz_url"):
                print(f"  MusicBrainz: {result.metadata['musicbrainz_url']}")
        else:
            print("  No match found")
