"""Slot-level music decisions from overlapping classification windows."""

from collections.abc import Iterable, Iterator

import numpy as np
from numpy.typing import NDArray

from soundtrackID.models import AudioChunk, ClassificationResult


def stream_slots(
    results: Iterable[ClassificationResult],
    audio: NDArray[np.float32],
    sample_rate: int,
    hop: float,
) -> Iterator[ClassificationResult]:
    """Turn overlapping window results into consecutive, gap-free hop-sized slots.

    A slot is music if any window fully containing it is music. Its confidence
    and probs are the mean over the containing windows that agree with that
    verdict, so its music score stays on the same side of the threshold as the
    verdict. Assumes windows arrive in start order and are at least ``hop``
    long, which the chunker guarantees.
    """
    hop_n = round(hop * sample_rate)
    total = len(audio)
    pending: list[tuple[int, int, ClassificationResult]] = []
    next_slot = 0

    def emit(a: int) -> ClassificationResult:
        b = min(a + hop_n, total)
        covering = [r for s, e, r in pending if s <= a and e >= b]
        is_music = any(r.is_music for r in covering)
        agreeing = [r for r in covering if r.is_music == is_music]
        return ClassificationResult(
            chunk=AudioChunk(
                data=audio[a:b],
                start_time=a / sample_rate,
                end_time=b / sample_rate,
                sample_rate=sample_rate,
            ),
            is_music=is_music,
            confidence=float(np.mean([r.confidence for r in agreeing])),
            probs=np.mean([r.probs for r in agreeing], axis=0),
        )

    for result in results:
        s = round(result.chunk.start_time * sample_rate)
        e = round(result.chunk.end_time * sample_rate)
        # A window starting after a slot's start can't contain it, so every
        # slot before this window is final.
        while next_slot < s:
            yield emit(next_slot)
            next_slot += hop_n
        pending.append((s, e, result))
        pending = [p for p in pending if p[1] > next_slot]

    # No windows (audio shorter than one window) means no slots.
    if pending:
        while next_slot < total:
            yield emit(next_slot)
            next_slot += hop_n


def top_classes(
    probs: NDArray[np.float32], labels: list[str], k: int
) -> dict[str, float]:
    """The k most likely classes with their probabilities, most likely first."""
    return {labels[i]: round(float(probs[i]), 4) for i in np.argsort(probs)[::-1][:k]}


def merge_spans(slots: list[ClassificationResult], labels: list[str]) -> list[dict]:
    """Merge consecutive slots with the same verdict into gap-free spans.

    Each span carries its five most likely classes, by mean probability over
    its slots.
    """
    spans: list[dict] = []
    for slot in slots:
        if spans and spans[-1]["is_music"] == slot.is_music:
            spans[-1]["end"] = slot.chunk.end_time
            spans[-1]["_probs"].append(slot.probs)
        else:
            spans.append({
                "start": slot.chunk.start_time,
                "end": slot.chunk.end_time,
                "is_music": slot.is_music,
                "_probs": [slot.probs],
            })
    for span in spans:
        span["classes"] = top_classes(np.mean(span.pop("_probs"), axis=0), labels, 5)
    return spans
