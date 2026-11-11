"""Tests for OnnxClassifier without running the real model."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from soundtrackID.classifiers import OnnxClassifier
from soundtrackID.models import AudioChunk


def test_window_duration_gives_exactly_max_length_frames(monkeypatch):
    """A window_duration chunk needs no padding or truncation."""
    import librosa

    frames = []
    real_melspectrogram = librosa.feature.melspectrogram

    def recording_melspectrogram(**kwargs):
        mel = real_melspectrogram(**kwargs)
        frames.append(mel.shape[1])
        return mel

    monkeypatch.setattr(librosa.feature, "melspectrogram", recording_melspectrogram)
    classifier = OnnxClassifier.__new__(OnnxClassifier)
    sr = OnnxClassifier._SAMPLE_RATE
    audio = np.zeros(round(OnnxClassifier.window_duration * sr), dtype=np.float32)

    classifier._extract_features(audio, sr)

    assert frames == [OnnxClassifier._MAX_LENGTH]


def test_labels_from_config(tmp_path):
    """labels lists the class names in model output order."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "label2id": {"Speech": 0, "Music": 1},
                "id2label": {"0": "Speech", "1": "Music"},
            }
        )
    )
    with patch("onnxruntime.InferenceSession"):
        classifier = OnnxClassifier(model_dir=str(tmp_path))

    assert classifier.labels == ["Speech", "Music"]
    assert classifier.music_indices == [1]


def test_classify_returns_probs():
    """classify keeps the full softmax vector next to the music score."""
    classifier = OnnxClassifier.__new__(OnnxClassifier)
    classifier.music_indices = [1]
    classifier.session = MagicMock()
    classifier.session.run.return_value = [np.array([[0.0, 1.0, 0.0]], np.float32)]
    classifier._extract_features = MagicMock()
    chunk = AudioChunk(
        data=np.zeros(16000, dtype=np.float32),
        start_time=0.0,
        end_time=1.0,
        sample_rate=16000,
    )

    music, probs = classifier.classify(chunk)

    assert probs.shape == (3,)
    assert probs.sum() == pytest.approx(1.0)
    assert music == pytest.approx(probs[1])
