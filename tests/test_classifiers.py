"""Tests for classifiers."""

import importlib.util
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from soundtrackID.models import AudioChunk


class TestHuggingFaceClassifier:
    """Tests for HuggingFaceClassifier with mocked dependencies."""

    @pytest.fixture
    def mock_classifier(self):
        """Create a HuggingFaceClassifier with mocked internals."""
        from soundtrackID.classifiers import HuggingFaceClassifier

        # Create instance without calling __init__
        classifier = HuggingFaceClassifier.__new__(HuggingFaceClassifier)

        # Set up mock torch
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock()

        # Set required attributes
        classifier.model_id = "test-model"
        classifier.music_labels = ["Music"]
        classifier.device = "cpu"
        classifier._torch = mock_torch
        classifier.label2id = {"Music": 0, "Speech": 1, "Silence": 2}
        classifier.music_indices = [0]

        # Mock processor
        classifier.processor = MagicMock()
        classifier.processor.sampling_rate = 16000

        # Mock model
        classifier.model = MagicMock()

        return classifier, mock_torch

    def test_classify_returns_the_music_labels_probability(self, mock_classifier):
        """Whether that is music is the run's call (its threshold)."""
        classifier, mock_torch = mock_classifier

        # Set up mock inputs
        mock_inputs = {"input_values": MagicMock()}
        mock_inputs["input_values"].to = MagicMock(return_value=MagicMock())
        classifier.processor.return_value = mock_inputs

        # Set up softmax to return high probability for music
        mock_probs = MagicMock()
        mock_prob_slice = MagicMock()
        mock_prob_slice.sum.return_value.item.return_value = 0.8
        mock_probs.__getitem__ = MagicMock(return_value=mock_prob_slice)
        mock_torch.softmax.return_value = mock_probs

        # Mock _resample
        classifier._resample = MagicMock(side_effect=lambda a, o, t: a)

        chunk = AudioChunk(
            data=np.zeros(16000, dtype=np.float32),
            start_time=0.0,
            end_time=1.0,
            sample_rate=16000,
        )

        music, _ = classifier.classify(chunk)

        assert music == 0.8

    def test_resample_same_rate(self, mock_classifier):
        """Test _resample returns audio unchanged when rates match."""
        classifier, _ = mock_classifier
        audio = np.ones(1000, dtype=np.float32)

        # Call the actual _resample method from the class
        from soundtrackID.classifiers import HuggingFaceClassifier

        result = HuggingFaceClassifier._resample(classifier, audio, 16000, 16000)

        assert result is audio

    def test_resample_different_rate(self, mock_classifier):
        """Test _resample calls librosa when rates differ."""
        classifier, _ = mock_classifier
        audio = np.ones(1000, dtype=np.float32)

        with patch("librosa.resample") as mock_resample:
            mock_resample.return_value = np.ones(500, dtype=np.float32)

            from soundtrackID.classifiers import HuggingFaceClassifier

            result = HuggingFaceClassifier._resample(classifier, audio, 32000, 16000)

            mock_resample.assert_called_once_with(audio, orig_sr=32000, target_sr=16000)
            assert len(result) == 500


@pytest.mark.skipif(
    importlib.util.find_spec("torch") is None, reason="torch not installed"
)
class TestHuggingFaceClassifierIntegration:
    """Integration tests that require actual HF dependencies."""

    def test_real_model_loading(self):
        """Test loading real model (skipped in CI)."""
        from soundtrackID.classifiers import HuggingFaceClassifier

        classifier = HuggingFaceClassifier()
        assert classifier.model is not None
        assert len(classifier.label2id) > 0
        assert "Music" in classifier.label2id

    def test_classify_real_audio(self):
        """Test classification of 10 s of audio with the real model."""
        from soundtrackID.classifiers import HuggingFaceClassifier

        # A 440 Hz tone; the checks below don't depend on what the audio is
        sr = 16000
        t = np.arange(10 * sr) / sr
        audio = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        classifier = HuggingFaceClassifier(
            music_labels=["Music", "Musical instrument", "Singing", "Background music"],
        )

        chunk = AudioChunk(
            data=audio,
            start_time=60.0,
            end_time=70.0,
            sample_rate=sr,
        )

        music, probs = classifier.classify(chunk)

        assert music > 0.0
        assert len(probs) == len(classifier.labels)


def test_the_onnx_export_is_used_unless_hf_is_asked_for_or_it_is_missing(
    tmp_path, monkeypatch
):
    """The app's and the CLI's classifier, from source."""
    from soundtrackID import classifiers

    built = []
    monkeypatch.setattr(
        classifiers,
        "OnnxClassifier",
        lambda model_dir, music_labels: built.append(("onnx", model_dir)),
    )
    monkeypatch.setattr(
        classifiers,
        "HuggingFaceClassifier",
        lambda music_labels: built.append(("hf",)),
    )
    monkeypatch.setattr(classifiers, "_MODEL_DIR", tmp_path)

    classifiers.load_classifier(hf=False)
    classifiers.load_classifier(hf=True)
    monkeypatch.setattr(classifiers, "_MODEL_DIR", tmp_path / "not exported")
    classifiers.load_classifier(hf=False)

    assert built == [("onnx", str(tmp_path)), ("hf",), ("hf",)]
