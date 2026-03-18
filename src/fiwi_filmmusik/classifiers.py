"""Music classification."""

from abc import ABC, abstractmethod

import numpy as np

from fiwi_filmmusik.models import AudioChunk, ClassificationResult


class BaseClassifier(ABC):
    @abstractmethod
    def classify(self, chunk: AudioChunk) -> ClassificationResult:
        """Classify an audio chunk for music presence."""
        ...


class DummyClassifier(BaseClassifier):
    def classify(self, chunk: AudioChunk) -> ClassificationResult:
        return ClassificationResult(chunk=chunk, is_music=True, confidence=1.0)


class HuggingFaceClassifier(BaseClassifier):
    """Audio classifier using HuggingFace models."""

    def __init__(
        self,
        model_id: str = "MIT/ast-finetuned-audioset-10-10-0.4593",
        music_labels: list[str] | None = None,
        threshold: float = 0.5,
        device: str = "cpu",
    ):
        try:
            import torch
            from transformers import (
                AutoFeatureExtractor,
                AutoModelForAudioClassification,
            )
        except ImportError as e:
            raise ImportError(
                "HuggingFaceClassifier requires the 'hf' extras. "
                "Install with: pip install fiwi-filmmusik[hf]"
            ) from e

        self.model_id = model_id
        self.music_labels = music_labels or ["Music"]
        self.threshold = threshold
        self.device = device
        self._torch = torch

        self.processor = AutoFeatureExtractor.from_pretrained(model_id)
        self.model = AutoModelForAudioClassification.from_pretrained(model_id).to(
            device
        )
        self.model.eval()

        # Build label -> index mapping
        self.label2id = self.model.config.label2id
        self.music_indices = [
            self.label2id[label]
            for label in self.music_labels
            if label in self.label2id
        ]

    def classify(self, chunk: AudioChunk) -> ClassificationResult:
        """Classify an audio chunk for music presence."""
        # Resample if needed (most models expect 16kHz)
        audio = self._resample(
            chunk.data, chunk.sample_rate, self.processor.sampling_rate
        )

        # Process and run inference
        inputs = self.processor(
            audio, sampling_rate=self.processor.sampling_rate, return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with self._torch.no_grad():
            logits = self.model(**inputs).logits
            probs = self._torch.softmax(logits, dim=-1)

        # Sum probabilities of music-related labels
        confidence = probs[0, self.music_indices].sum().item()
        is_music = confidence > self.threshold

        return ClassificationResult(chunk=chunk, is_music=is_music, confidence=confidence)

    def _resample(
        self, audio: np.ndarray, orig_sr: int, target_sr: int
    ) -> np.ndarray:
        """Resample audio to target sample rate."""
        if orig_sr == target_sr:
            return audio
        try:
            import librosa
        except ImportError as e:
            raise ImportError(
                "Resampling requires librosa. "
                "Install with: pip install fiwi-filmmusik[hf]"
            ) from e
        return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr)


if __name__ == "__main__":
    import sys
    import warnings

    import librosa

    warnings.filterwarnings("ignore")

    path = sys.argv[1] if len(sys.argv) > 1 else "tests/test_video.mp4"
    classifier = HuggingFaceClassifier(music_labels=["Music"], threshold=0.3)

    for offset in range(0, 240, 10):
        try:
            audio, sr = librosa.load(path, sr=16000, duration=10.0, offset=offset)
        except Exception:
            break
        chunk = AudioChunk(
            data=audio, start_time=offset, end_time=offset + 10, sample_rate=sr
        )
        result = classifier.classify(chunk)
        status = "YES" if result.is_music else "NO"
        print(f"{offset:3d}-{offset+10:3d}s: {status}  ({result.confidence*100:.1f}%)")