"""Tests for slot-level music decisions from overlapping windows."""

import numpy as np

from soundtrackID.chunker import AudioChunker
from soundtrackID.models import ClassificationResult
from soundtrackID.slots import merge_spans, stream_slots

SR = 1000
LABELS = ["Speech", "Music", "Orchestra"]


def classify_windows(seconds, verdicts, probs, window=10.0, hop=5.0):
    """Chunk silent audio into overlapping windows and attach fake verdicts/probs."""
    audio = np.zeros(int(seconds * SR), dtype=np.float32)
    chunker = AudioChunker(chunk_duration=window, overlap=window - hop)
    results = [
        ClassificationResult(
            chunk=chunk,
            is_music=is_music,
            confidence=p[1],
            probs=np.array(p, dtype=np.float32),
        )
        for chunk, is_music, p in zip(
            chunker.chunk(audio, SR), verdicts, probs, strict=True
        )
    ]
    return audio, results


def spans_of(slots):
    return [(s.chunk.start_time, s.chunk.end_time, s.is_music) for s in slots]


def test_slot_is_music_if_any_covering_window_is_music():
    """One music window makes both slots it contains music (OR rule)."""
    p_no, p_music = [0.9, 0.05, 0.05], [0.4, 0.5, 0.1]
    audio, results = classify_windows(
        20, [False, True, False], [p_no, p_music, p_no]
    )

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert spans_of(slots) == [
        (0.0, 5.0, False),
        (5.0, 10.0, True),
        (10.0, 15.0, True),
        (15.0, 20.0, False),
    ]
    # A music slot only uses the music windows covering it.
    np.testing.assert_allclose(slots[1].probs, p_music)
    assert slots[1].confidence == 0.5


def test_no_music_slot_averages_all_covering_windows():
    """When every covering window says no music, the slot averages them all."""
    p0, p1, p2 = [0.9, 0.05, 0.05], [0.7, 0.1, 0.2], [0.8, 0.15, 0.05]
    audio, results = classify_windows(20, [False, False, False], [p0, p1, p2])

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert not any(s.is_music for s in slots)
    np.testing.assert_allclose(slots[1].probs, np.mean([p0, p1], axis=0))


def test_music_slot_averages_only_agreeing_windows():
    """A mixed slot is music and ignores the no-music window."""
    p0, p1, p2 = [0.3, 0.6, 0.1], [0.4, 0.5, 0.1], [0.9, 0.05, 0.05]
    audio, results = classify_windows(20, [True, True, False], [p0, p1, p2])

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    np.testing.assert_allclose(slots[1].probs, np.mean([p0, p1], axis=0))
    assert slots[2].is_music
    np.testing.assert_allclose(slots[2].probs, p1)


def test_slots_are_gapless_and_cover_the_tail():
    """Audio not on the hop grid: the end-anchored tail window covers the rest."""
    p = [0.9, 0.05, 0.05]
    audio, results = classify_windows(23, [False] * 4, [p] * 4)

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert [(s.chunk.start_time, s.chunk.end_time) for s in slots] == [
        (0.0, 5.0),
        (5.0, 10.0),
        (10.0, 15.0),
        (15.0, 20.0),
        (20.0, 23.0),
    ]


def test_audio_shorter_than_window_gives_no_slots():
    """No window fits, so there is nothing to decide."""
    audio, results = classify_windows(8, [], [])

    assert list(stream_slots(results, audio, SR, hop=5.0)) == []


def test_hop_2_5_uses_four_windows_per_slot():
    """With a 2.5 s hop each slot lies inside four windows."""
    probs = [[0.9, 0.1, 0.0], [0.8, 0.1, 0.1], [0.7, 0.1, 0.2], [0.6, 0.1, 0.3],
             [0.5, 0.1, 0.4]]
    audio, results = classify_windows(20, [False] * 5, probs, hop=2.5)

    slots = list(stream_slots(results, audio, SR, hop=2.5))

    assert len(slots) == 8
    # Slot 7.5-10 s is contained in the windows starting at 0, 2.5, 5 and 7.5 s.
    np.testing.assert_allclose(slots[3].probs, np.mean(probs[:4], axis=0))


def test_merge_spans_joins_same_verdict_slots_with_all_classes():
    """Neighbouring slots with the same verdict merge; every class is kept."""
    p_no, p_music = [0.9, 0.05, 0.05], [0.4, 0.5, 0.1]
    audio, results = classify_windows(
        20, [False, True, False], [p_no, p_music, p_no]
    )
    slots = list(stream_slots(results, audio, SR, hop=5.0))

    spans = merge_spans(slots, LABELS)

    assert [(s["start"], s["end"], s["is_music"]) for s in spans] == [
        (0.0, 5.0, False),
        (5.0, 15.0, True),
        (15.0, 20.0, False),
    ]
    music = spans[1]["classes"]
    assert list(music) == ["Music", "Speech", "Orchestra"]  # sorted by probability
    assert music["Music"] == 0.5


def test_merge_spans_keeps_top_five_classes():
    """Only the five most likely classes are written per span."""
    labels = ["a", "b", "c", "d", "e", "f", "g"]
    p = [0.3, 0.25, 0.15, 0.1, 0.08, 0.07, 0.05]
    audio, results = classify_windows(10, [False], [p])
    slots = list(stream_slots(results, audio, SR, hop=5.0))

    [span] = merge_spans(slots, labels)

    assert list(span["classes"]) == ["a", "b", "c", "d", "e"]
