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


def test_isolated_music_window_makes_no_music():
    """At a 5 s hop both windows covering a slot must say music."""
    p_no, p_music = [0.9, 0.05, 0.05], [0.4, 0.5, 0.1]
    audio, results = classify_windows(
        20, [False, True, False], [p_no, p_music, p_no]
    )

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert not any(s.is_music for s in slots)
    # A no-music slot only uses the no-music windows covering it.
    np.testing.assert_allclose(slots[1].probs, p_no)


def test_slot_is_music_when_both_windows_agree():
    """Two neighbouring music windows make the slot they share music."""
    p_no, p_music = [0.9, 0.05, 0.05], [0.4, 0.5, 0.1]
    audio, results = classify_windows(
        25, [False, True, True, False], [p_no, p_music, p_music, p_no]
    )

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert spans_of(slots) == [
        (0.0, 5.0, False),
        (5.0, 10.0, False),
        (10.0, 15.0, True),
        (15.0, 20.0, False),
        (20.0, 25.0, False),
    ]
    np.testing.assert_allclose(slots[2].probs, p_music)
    assert slots[2].confidence == 0.5


def test_no_music_slot_averages_all_covering_windows():
    """When every covering window says no music, the slot averages them all."""
    p0, p1, p2 = [0.9, 0.05, 0.05], [0.7, 0.1, 0.2], [0.8, 0.15, 0.05]
    audio, results = classify_windows(20, [False, False, False], [p0, p1, p2])

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert not any(s.is_music for s in slots)
    np.testing.assert_allclose(slots[1].probs, np.mean([p0, p1], axis=0))


def test_three_of_four_is_music_and_averages_only_agreeing_windows():
    """At a 2.5 s hop 3 of 4 music windows suffice; the no-music one is ignored."""
    p = [[0.3, 0.6, 0.1], [0.4, 0.5, 0.1], [0.9, 0.05, 0.05], [0.2, 0.7, 0.1],
         [0.9, 0.05, 0.05]]
    audio, results = classify_windows(20, [True, True, False, True, False], p, hop=2.5)

    slots = list(stream_slots(results, audio, SR, hop=2.5))

    # Slot 7.5-10 s lies in the windows starting at 0, 2.5, 5 (no music) and 7.5.
    assert slots[3].is_music
    np.testing.assert_allclose(slots[3].probs, np.mean([p[0], p[1], p[3]], axis=0))


def test_edge_slots_need_all_their_windows_at_hop_2_5():
    """Near the start and end fewer than 4 windows cover a slot; all must agree."""
    p = [[0.4, 0.5, 0.1]] * 5
    audio, results = classify_windows(20, [True, True, False, True, True], p, hop=2.5)

    slots = list(stream_slots(results, audio, SR, hop=2.5))

    # Windows covering each slot: 1, 2, 3, 4, 4, 3, 2, 1 (the third has the miss).
    assert [s.is_music for s in slots] == [
        True, True, False, True, True, False, True, True,
    ]


def test_end_slots_covered_only_by_the_tail_window():
    """The end-anchored tail can be the only window for the last two slots."""
    p = [[0.4, 0.5, 0.1]] * 4
    # 25.1 s: windows 0-10.23, 5-15.23, 10-20.23 and the tail 14.87-25.1.
    audio, results = classify_windows(
        25.1, [False, False, False, True], p, window=10.23, hop=5.0
    )

    slots = list(stream_slots(results, audio, SR, hop=5.0))

    assert spans_of(slots)[-3:] == [
        (15.0, 20.0, False),  # 10-20.23 and the tail disagree
        (20.0, 25.0, True),
        (25.0, 25.1, True),
    ]


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


def test_hop_10_uses_one_window_per_slot_including_the_tail():
    """With 10.23 s windows and a 10 s hop each slot lies inside exactly one window."""
    probs = [[0.9, 0.1, 0.0], [0.6, 0.1, 0.3], [0.5, 0.1, 0.4]]
    audio, results = classify_windows(23, [False] * 3, probs, window=10.23, hop=10.0)

    slots = list(stream_slots(results, audio, SR, hop=10.0))

    assert [(s.chunk.start_time, s.chunk.end_time) for s in slots] == [
        (0.0, 10.0),
        (10.0, 20.0),
        (20.0, 23.0),
    ]
    # Windows: 0-10.23, 10-20.23 and the end-anchored tail 12.77-23.
    for slot, p in zip(slots, probs, strict=True):
        np.testing.assert_allclose(slot.probs, p)


def test_merge_spans_joins_same_verdict_slots_with_all_classes():
    """Neighbouring slots with the same verdict merge; every class is kept."""
    p_no, p_music = [0.9, 0.05, 0.05], [0.4, 0.5, 0.1]
    audio, results = classify_windows(
        30, [False, True, True, True, False], [p_no, p_music, p_music, p_music, p_no]
    )
    slots = list(stream_slots(results, audio, SR, hop=5.0))

    spans = merge_spans(slots, LABELS)

    assert [(s["start"], s["end"], s["is_music"]) for s in spans] == [
        (0.0, 10.0, False),
        (10.0, 20.0, True),
        (20.0, 30.0, False),
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
