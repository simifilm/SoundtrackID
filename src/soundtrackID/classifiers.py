"""Music classification."""

import sys
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from soundtrackID.models import AudioChunk


class BaseClassifier(ABC):
    labels: list[str]
    window_duration: float

    @abstractmethod
    def classify(self, chunk: AudioChunk) -> tuple[float, NDArray[np.float32]]:
        """How likely `chunk` is music, and the probability of each of `labels`.

        Whether that is music enough is the run's call (its threshold), so
        runs at different thresholds can share the loaded model.
        """
        ...


class OnnxClassifier(BaseClassifier):
    """Audio classifier using a pre-exported ONNX model (no PyTorch required)."""

    _SAMPLE_RATE = 16000
    _HOP_LENGTH = 160
    _WIN_LENGTH = 400
    _NUM_MEL_BINS = 128
    _MAX_LENGTH = 1024
    _MEAN = -4.2677393
    _STD = 4.5689974
    # librosa (center=True) yields 1 + n // hop frames, so this length gives
    # exactly _MAX_LENGTH frames: no padding, no truncation.
    window_duration = (_MAX_LENGTH - 1) * _HOP_LENGTH / _SAMPLE_RATE

    def __init__(
        self,
        model_dir: str,
        music_labels: list[str] | None = None,
    ):
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise ImportError(
                "OnnxClassifier requires onnxruntime. "
                "Install with: pip install onnxruntime"
            ) from e

        self.music_labels = music_labels or ["Music"]
        self.session = ort.InferenceSession(
            f"{model_dir}/model.onnx",
            providers=["CPUExecutionProvider"],
        )

        import json
        import pathlib

        cfg = json.loads((pathlib.Path(model_dir) / "config.json").read_text())
        label2id = cfg.get("label2id", {})
        self.music_indices = [
            int(label2id[label]) for label in self.music_labels if label in label2id
        ]
        id2label = cfg.get("id2label", {})
        self.labels = [id2label[str(i)] for i in range(len(id2label))]

    def _extract_features(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Compute Kaldi-style log-mel spectrogram matching ASTFeatureExtractor."""
        import librosa

        if sample_rate != self._SAMPLE_RATE:
            audio = librosa.resample(
                audio, orig_sr=sample_rate, target_sr=self._SAMPLE_RATE
            )
        mel = librosa.feature.melspectrogram(
            y=audio.astype(np.float32),
            sr=self._SAMPLE_RATE,
            n_fft=512,
            hop_length=self._HOP_LENGTH,
            win_length=self._WIN_LENGTH,
            window="hann",
            n_mels=self._NUM_MEL_BINS,
            htk=True,
            norm=None,
        )
        log_mel = np.log(np.maximum(mel, 1e-10)).T  # (time, mels)
        if log_mel.shape[0] < self._MAX_LENGTH:
            log_mel = np.pad(
                log_mel, ((0, self._MAX_LENGTH - log_mel.shape[0]), (0, 0))
            )
        else:
            log_mel = log_mel[: self._MAX_LENGTH]
        # Normalize with AudioSet statistics (matches ASTFeatureExtractor do_normalize)
        log_mel = (log_mel - self._MEAN) / (self._STD * 2)
        return log_mel[np.newaxis].astype(np.float32)  # (1, 1024, 128)

    def classify(self, chunk: AudioChunk) -> tuple[float, NDArray[np.float32]]:
        features = self._extract_features(chunk.data, chunk.sample_rate)
        logits = self.session.run(None, {"input_values": features})[0]
        exp = np.exp(logits - logits.max(axis=-1, keepdims=True))
        probs = exp / exp.sum(axis=-1, keepdims=True)
        return float(probs[0, self.music_indices].sum()), probs[0]


class HuggingFaceClassifier(BaseClassifier):
    """Audio classifier using HuggingFace models."""

    # Same AST input length as the ONNX export. The HF feature extractor frames
    # the audio itself, so it may still pad or cut a frame (not verified).
    window_duration = OnnxClassifier.window_duration

    def __init__(
        self,
        model_id: str = "MIT/ast-finetuned-audioset-10-10-0.4593",
        music_labels: list[str] | None = None,
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
                "Install with: pip install soundtrackID[hf]"
            ) from e

        self.model_id = model_id
        self.music_labels = music_labels or ["Music"]
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
        id2label = self.model.config.id2label
        self.labels = [id2label[i] for i in range(len(id2label))]

    def classify(self, chunk: AudioChunk) -> tuple[float, NDArray[np.float32]]:
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
        return probs[0, self.music_indices].sum().item(), probs[0].cpu().numpy()

    def _resample(self, audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """Resample audio to target sample rate."""
        if orig_sr == target_sr:
            return audio
        try:
            import librosa
        except ImportError as e:
            raise ImportError(
                "Resampling requires librosa. "
                "Install with: pip install soundtrackID[hf]"
            ) from e
        return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr)


# The ONNX export of the AST model, from source (assets/ast_model, Git LFS).
_MODEL_DIR = Path(__file__).parents[2] / "assets" / "ast_model"


def load_classifier(hf: bool = False) -> BaseClassifier:
    """The music classifier the app and the CLI analyze with: the ONNX export,
    or the HuggingFace original when `hf` asks for it or the export is missing.
    The installed app bundles only the export."""
    if getattr(sys, "frozen", False):
        model_dir = Path(sys._MEIPASS) / "ast_model"  # pyright: ignore[reportAttributeAccessIssue]
        return OnnxClassifier(model_dir=str(model_dir), music_labels=["Music"])
    if hf or not _MODEL_DIR.exists():
        return HuggingFaceClassifier(music_labels=["Music"])
    return OnnxClassifier(model_dir=str(_MODEL_DIR), music_labels=["Music"])
