"""Video thumbnail filmstrip extraction.

Produces a single horizontal sprite of evenly-spaced frames from a video, used
by the web UI as a scrubbable timeline below the waveform (see static/index.html).
"""

from pathlib import Path

import av

THUMB_HEIGHT = 48        # px, per thumbnail
MAX_COLUMNS = 240        # cap on number of frames in the strip
SECONDS_PER_THUMB = 4    # target spacing


def extract_filmstrip(video_path: Path, out_path: Path) -> dict | None:
    """Extract an evenly-spaced frame strip and save it as one JPEG sprite.

    Returns metadata ``{"count", "thumb_width", "thumb_height", "duration"}`` on
    success, or ``None`` if the file has no usable video stream or extraction
    fails (callers treat that as "no filmstrip" and carry on).
    """
    try:
        container = av.open(str(video_path))
    except Exception:
        return None

    try:
        if not container.streams.video:
            return None
        stream = container.streams.video[0]

        # Duration in seconds (prefer stream, fall back to container).
        duration = None
        if stream.duration is not None and stream.time_base is not None:
            duration = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            duration = float(container.duration / av.time_base)
        if not duration or duration <= 0:
            return None

        n = min(MAX_COLUMNS, max(24, int(duration / SECONDS_PER_THUMB)))

        # Thumbnail size from the display aspect ratio (fallback 16:9).
        w, h = stream.codec_context.width, stream.codec_context.height
        aspect = (w / h) if (w and h) else (16 / 9)
        thumb_w = max(1, round(THUMB_HEIGHT * aspect))

        from PIL import Image

        strip = Image.new("RGB", (thumb_w * n, THUMB_HEIGHT), (0, 0, 0))
        time_base = stream.time_base

        for i in range(n):
            t = duration * (i + 0.5) / n  # centre of each column's slice
            try:
                offset = int(t / time_base)
                container.seek(offset, stream=stream, any_frame=False, backward=True)
                frame = next(container.decode(stream))
                img = frame.to_image().resize((thumb_w, THUMB_HEIGHT))
                strip.paste(img, (i * thumb_w, 0))
            except Exception:
                # Leave this column black if a frame can't be decoded.
                continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        strip.save(out_path, format="JPEG", quality=70)
        return {
            "count": n,
            "thumb_width": thumb_w,
            "thumb_height": THUMB_HEIGHT,
            "duration": round(duration, 2),
        }
    except Exception:
        return None
    finally:
        container.close()
