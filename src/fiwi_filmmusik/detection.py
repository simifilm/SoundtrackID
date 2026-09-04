"""Music detection API."""

import json
import os
import re
import tempfile
import threading
from abc import ABC, abstractmethod

import numpy as np
from scipy.io import wavfile

from fiwi_filmmusik.models import DetectionResult, MusicSegment


def _find_shazamkit_helper() -> str | None:
    """Locate the compiled ShazamKit helper binary (macOS only).

    Order: SHAZAMKIT_HELPER env override → next to the bundled binary (frozen
    app) → the repo's shazamkit/ dir (dev build)."""
    import sys
    from pathlib import Path

    override = os.environ.get("SHAZAMKIT_HELPER")
    candidates = [Path(override)] if override else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "shazamkit-match")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "shazamkit-match")
    else:
        candidates.append(Path(__file__).resolve().parents[2] / "shazamkit" / "shazamkit-match")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


class RateLimitError(RuntimeError):
    """A provider refused the request due to a quota / rate limit / auth problem.

    Raised so the pipeline can stop calling that provider, keep whatever it has
    already identified, and offer a resume rather than losing the whole run.
    """


def _extract_year(value) -> int | None:
    """Pull a 4-digit year out of a date string / year field (e.g. '2022-05-01')."""
    if value is None:
        return None
    m = re.search(r"\d{4}", str(value))
    return int(m.group()) if m else None


def _parse_acr_candidate(m: dict) -> dict:
    """Parse one ACRCloud music candidate into a flat cue-shaped dict."""
    ext = m.get("external_metadata", {})
    spotify = ext.get("spotify", {}).get("track", {}).get("id")
    youtube = ext.get("youtube", {}).get("vid")
    mb = ext.get("musicbrainz")
    mb_id = mb[0].get("track", {}).get("id") if isinstance(mb, list) and mb else None
    genres = m.get("genres") or []
    release_year = _extract_year(m.get("release_date")) or _extract_year(
        m.get("album", {}).get("release_date")
    )
    return {
        "title": m.get("title"),
        "artist": ", ".join(a["name"] for a in m.get("artists", []) if a.get("name")) or None,
        "album": m.get("album", {}).get("name"),
        "release_year": release_year,
        "isrc": m.get("external_ids", {}).get("isrc"),
        "genre": genres[0].get("name") if genres else None,
        "acrcloud_id": m.get("acrid"),
        "score": float(m.get("score", 0.0)),
        "spotify_url": f"https://open.spotify.com/track/{spotify}" if spotify else None,
        "youtube_link": f"https://www.youtube.com/watch?v={youtube}" if youtube else None,
        "musicbrainz_recording_id": mb_id,
        "musicbrainz_url": f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None,
    }


class BaseMusicDetectionClient(ABC):
    @abstractmethod
    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in a segment."""
        ...


class ShazamDetectionClient(BaseMusicDetectionClient):
    """Music detection via Apple's official ShazamKit.

    Delegates to a small native Swift helper (``shazamkit-match``) that generates
    a Shazam signature and matches it against Apple's catalog. ShazamKit is
    macOS-only and requires the ``com.apple.developer.shazamkit`` entitlement,
    honored only when the helper runs inside an app whose provisioning profile
    grants it. Without that (a bare dev build) matching returns error 202; this
    client treats any failure as a graceful no-match so the pipeline keeps
    running (the ensemble simply relies on the other providers).
    """

    _warned = False

    def __init__(self, helper_path: str | None = None):
        self._helper = helper_path or _find_shazamkit_helper()

    def detect(self, segment: MusicSegment) -> DetectionResult:
        empty = DetectionResult(
            segment=segment, title=None, artist=None, confidence=0.0, provider="shazam"
        )
        if not self._helper:
            if not ShazamDetectionClient._warned:
                ShazamDetectionClient._warned = True
                print("[shazam] ShazamKit helper not found — build it with "
                      "shazamkit/build.sh (macOS). Skipping Shazam.")
            return empty

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            audio_int16 = (np.clip(segment.audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(temp_path, segment.sample_rate, audio_int16)

        import subprocess
        try:
            proc = subprocess.run(
                [self._helper, temp_path],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            return empty
        finally:
            os.unlink(temp_path)

        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return empty

        if data.get("error") or not data.get("title"):
            # 202 = missing ShazamKit entitlement (dev build); warn once, no-match.
            if data.get("code") == 202 and not ShazamDetectionClient._warned:
                ShazamDetectionClient._warned = True
                print("[shazam] ShazamKit returned 202 (missing entitlement / "
                      "provisioning profile) — Shazam only works in the signed app.")
            return empty

        apple_url = data.get("appleMusicURL")
        return DetectionResult(
            segment=segment,
            title=data.get("title"),
            artist=data.get("artist"),
            confidence=1.0,  # ShazamKit gives no score
            provider="shazam",
            metadata={
                "isrc": data.get("isrc"),
                "apple_music_url": apple_url,
                "shazam_id": data.get("shazamID"),
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
            segment=segment, title=None, artist=None, confidence=0.0, provider="acrcloud"
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            audio_int16 = (np.clip(segment.audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(temp_path, segment.sample_rate, audio_int16)

        duration = max(1, int(round(len(segment.audio) / segment.sample_rate)))
        try:
            raw = self._recognizer.recognize_by_file(temp_path, 0, duration)
        finally:
            os.unlink(temp_path)

        payload = json.loads(raw)
        code = payload.get("status", {}).get("code")
        if code != 0:
            # 1001 = no result; 2xxx = recording/fingerprint issues (tolerate,
            # treat as no match). 3xxx = auth/quota limit — stop and allow resume.
            if isinstance(code, int) and 3000 <= code < 4000:
                raise RateLimitError(
                    f"ACRCloud error {code}: {payload.get('status', {}).get('msg')}"
                )
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
            confidence=primary["score"] / 100.0,  # ACRCloud score is 0-100
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
            segment=segment, title=None, artist=None, confidence=0.0, provider="audd"
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            audio_int16 = (np.clip(segment.audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(temp_path, segment.sample_rate, audio_int16)

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
        finally:
            os.unlink(temp_path)

        payload = resp.json()
        status = payload.get("status")
        if status == "error":
            err = payload.get("error", {}) or {}
            code = err.get("error_code")
            # 900 = no/invalid api_token, 901 = request limit reached. Stop and
            # allow resume (parallels ACRCloud 3xxx).
            if code in (900, 901):
                raise RateLimitError(
                    f"AudD error {code}: {err.get('error_message')}"
                )
            return empty

        result = payload.get("result")
        if not result:
            return empty

        apple = result.get("apple_music") or {}
        spotify = result.get("spotify") or {}
        mb = result.get("musicbrainz") or []

        isrc = apple.get("isrc") or (spotify.get("external_ids") or {}).get("isrc")
        spotify_url = (spotify.get("external_urls") or {}).get("spotify")
        apple_music_url = apple.get("url")
        mb_id = mb[0].get("id") if isinstance(mb, list) and mb else None

        return DetectionResult(
            segment=segment,
            title=result.get("title"),
            artist=result.get("artist"),
            confidence=1.0,  # AudD returns no score
            provider="audd",
            metadata={
                "album": result.get("album"),
                "release_year": _extract_year(result.get("release_date")),
                "isrc": isrc,
                "genre": None,
                "song_link": result.get("song_link"),
                "spotify_url": spotify_url,
                "apple_music_url": apple_music_url,
                "youtube_link": None,
                "musicbrainz_recording_id": mb_id,
                "musicbrainz_url": f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None,
            },
        )


# ── Ensemble (multi-provider majority voting) ────────────────────────────

# Provider priority for tie-breaks and canonical title/artist selection, based
# on observed catalog quality for film scores (ACRCloud strongest).
_PROVIDER_PRIORITY = ["acrcloud", "shazam", "audd"]

# Metadata keys merged across a winning cluster (first non-null by priority),
# so the consensus keeps the best ISRC + platform links any provider supplied.
_MERGE_KEYS = (
    "album", "release_year", "isrc", "genre", "photo_url", "youtube_link",
    "spotify_url", "apple_music_url", "musicbrainz_recording_id",
    "musicbrainz_url", "song_link", "candidates",
)


def _normalize_title(s: str | None) -> str:
    """Collapse a track title to a comparison key: drop parentheticals,
    bracketed tags, feat./remaster suffixes, and punctuation."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\([^)]*\)", " ", s)          # (From "Interstellar")
    s = re.sub(r"\[[^\]]*\]", " ", s)          # [Remastered]
    s = re.sub(r"\bfeat\.?.*$", " ", s)        # feat. …
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
) -> tuple[str | None, str | None, float, dict, dict]:
    """Pure voting core. `votes` is every provider's result in priority order
    (each a dict with provider/title/artist/isrc/confidence/matched + metadata).
    Returns (title, artist, confidence, merged_metadata, agreement)."""
    matched = [v for v in votes if v.get("matched")]
    if not matched:
        return None, None, 0.0, {}, {"count": 0, "total": total, "providers": []}

    clusters = _cluster_votes(matched)

    # Winner: most providers, then — since per-provider confidences aren't
    # comparable across services (AudD/Shazam report a constant 1.0, ACRCloud a
    # real score) — the cluster containing the highest-priority provider.
    # `matched` is in priority order, so the smallest member index is best.
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
    confidence = len(members) / total if total else 0.0
    return rep.get("title"), rep.get("artist"), confidence, merged, agreement


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
            except RateLimitError as exc:
                return name, None, str(exc), True
            except Exception as exc:  # one provider failing must not sink the run
                return name, None, str(exc), False

        with ThreadPoolExecutor(max_workers=len(self._clients)) as ex:
            raw = [f.result() for f in [ex.submit(run, n, c) for n, c in self._clients]]

        # If every provider is rate-limited, the ensemble as a whole is throttled:
        # propagate so the pipeline can save partial results and offer a resume.
        if raw and all(rl for _, _, _, rl in raw):
            raise RateLimitError("all providers rate-limited: " + "; ".join(
                e for _, _, e, _ in raw if e))
        raw = [(n, r, e) for n, r, e, _ in raw]

        # Keep providers in the configured priority order.
        order = {n: i for i, (n, _) in enumerate(self._clients)}
        raw.sort(key=lambda t: order.get(t[0], 99))

        votes: list[dict] = []
        for name, result, error in raw:
            matched = bool(result and result.title)
            votes.append({
                "provider": name,
                "title": result.title if result else None,
                "artist": result.artist if result else None,
                "isrc": (result.metadata.get("isrc") if result else None),
                "confidence": result.confidence if result else 0.0,
                "matched": matched,
                "metadata": (result.metadata if result else {}),
                "error": error,
            })

        title, artist, confidence, merged, agreement = _consensus_from_votes(
            votes, total=len(self._clients)
        )

        public_votes = [
            {k: v[k] for k in ("provider", "title", "artist", "isrc", "confidence", "matched", "error")}
            for v in votes
        ]

        return DetectionResult(
            segment=segment,
            title=title,
            artist=artist,
            confidence=confidence,
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

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Identify music in one or more WAV files via Shazam or ACRCloud."
    )
    parser.add_argument("wav_files", nargs="+", help="WAV file(s) to identify")
    parser.add_argument(
        "--api",
        default="acrcloud",
        help="Detection provider(s): acrcloud, shazam, audd — or a comma-separated "
             "list for ensemble majority voting (default: acrcloud)",
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
            print(f"  Confidence: {result.confidence}")
            if result.metadata.get("album"):
                print(f"  Album:      {result.metadata['album']}")
            if result.metadata.get("genre"):
                print(f"  Genre:      {result.metadata['genre']}")
            if result.metadata.get("musicbrainz_url"):
                print(f"  MusicBrainz: {result.metadata['musicbrainz_url']}")
        else:
            print("  No match found")
