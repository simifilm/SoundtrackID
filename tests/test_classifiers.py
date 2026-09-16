"""Tests for classifiers."""

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
        classifier.threshold = 0.5
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

    def test_classify_above_threshold(self, mock_classifier):
        """Test classify returns is_music=True when above threshold."""
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

        result = classifier.classify(chunk)

        assert result.is_music is True
        assert result.confidence == 0.8
        assert result.chunk is chunk

    def test_classify_below_threshold(self, mock_classifier):
        """Test classify returns is_music=False when below threshold."""
        classifier, mock_torch = mock_classifier

        mock_inputs = {"input_values": MagicMock()}
        mock_inputs["input_values"].to = MagicMock(return_value=MagicMock())
        classifier.processor.return_value = mock_inputs

        # Set up softmax to return low probability
        mock_probs = MagicMock()
        mock_prob_slice = MagicMock()
        mock_prob_slice.sum.return_value.item.return_value = 0.3
        mock_probs.__getitem__ = MagicMock(return_value=mock_prob_slice)
        mock_torch.softmax.return_value = mock_probs

        classifier._resample = MagicMock(side_effect=lambda a, o, t: a)

        chunk = AudioChunk(
            data=np.zeros(16000, dtype=np.float32),
            start_time=0.0,
            end_time=1.0,
            sample_rate=16000,
        )

        result = classifier.classify(chunk)

        assert result.is_music is False
        assert result.confidence == 0.3

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
    not pytest.importorskip("torch", reason="torch not installed"),
    reason="HF dependencies not installed",
)
class TestHuggingFaceClassifierIntegration:
    """Integration tests that require actual HF dependencies."""

    @pytest.fixture
    def test_video_path(self):
        """Path to test video file."""
        import pathlib

        path = pathlib.Path(__file__).parent / "test_video.mp4"
        if not path.exists():
            pytest.skip("test_video.mp4 not found")
        return path

    @pytest.mark.slow
    def test_real_model_loading(self):
        """Test loading real model (skipped in CI)."""
        from soundtrackID.classifiers import HuggingFaceClassifier

        classifier = HuggingFaceClassifier()
        assert classifier.model is not None
        assert len(classifier.label2id) > 0
        assert "Music" in classifier.label2id

    @pytest.mark.slow
    def test_classify_real_audio(self, test_video_path):
        """Test classification with real audio from test video."""
        import librosa

        from soundtrackID.classifiers import HuggingFaceClassifier

        # Load 10 seconds of audio starting at 60s (music section)
        audio, sr = librosa.load(test_video_path, sr=16000, duration=10.0, offset=60.0)

        classifier = HuggingFaceClassifier(
            music_labels=["Music", "Musical instrument", "Singing", "Background music"],
            threshold=0.3,
        )

        chunk = AudioChunk(
            data=audio,
            start_time=60.0,
            end_time=70.0,
            sample_rate=sr,
        )

        result = classifier.classify(chunk)

        # The test video contains music, so we expect detection
        assert result.confidence > 0.0
        assert result.chunk is chunk
        assert isinstance(result.is_music, bool)
