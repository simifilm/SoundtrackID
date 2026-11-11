"""Music isolation."""

import logging
from abc import ABC, abstractmethod

import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)


class BaseMusicIsolator(ABC):
    @abstractmethod
    def isolate(
        self, audio: NDArray[np.float32], sample_rate: int
    ) -> NDArray[np.float32]:
        """Isolate music from audio."""
        ...


class DummyIsolator(BaseMusicIsolator):
    def isolate(
        self, audio: NDArray[np.float32], sample_rate: int
    ) -> NDArray[np.float32]:
        return audio


class DemucsIsolator(BaseMusicIsolator):
    """Music isolator using Demucs for audio source separation.

    Demucs separates audio into stems (drums, bass, other, vocals).
    This isolator returns the sum of non-vocal stems as "music".

    Requires: pip install demucs torch
    """

    def __init__(self, model_name: str = "htdemucs", device: str | None = None):
        """Initialize DemucsIsolator.

        Args:
            model_name: Demucs model to use. Options include:
                - "htdemucs" (default, hybrid transformer)
                - "htdemucs_ft" (fine-tuned, better quality)
                - "mdx_extra" (MDX challenge winner)
            device: Device to run on ('cuda', 'cpu', or None for auto-detect)
        """
        import torch
        from demucs.pretrained import get_model

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        log.info("Loading Demucs model '%s'...", model_name)
        self.model = get_model(model_name)
        self.model.to(self.device)
        self.model.eval()
        self.model_name = model_name
        log.info("Demucs ready on %s", self.device)

    def isolate(
        self, audio: NDArray[np.float32], sample_rate: int
    ) -> NDArray[np.float32]:
        """Isolate music from audio using Demucs.

        Separates audio into stems and returns the sum of non-vocal stems.

        Args:
            audio: Input audio as float32 array, shape (samples,) or (channels, samples)
            sample_rate: Sample rate in Hz

        Returns:
            Isolated music as float32 array, same shape as input
        """
        import torch
        from demucs.apply import apply_model
        from demucs.audio import convert_audio

        # Ensure audio is (channels, samples)
        if audio.ndim == 1:
            audio = audio[np.newaxis, :]  # mono -> (1, samples)
            was_mono = True
        else:
            was_mono = False

        # Convert to tensor and ensure stereo for Demucs
        audio_tensor = torch.from_numpy(audio).float()
        if audio_tensor.shape[0] == 1:
            audio_tensor = audio_tensor.repeat(2, 1)  # mono -> stereo

        # Resample to model's sample rate if needed
        audio_tensor = convert_audio(
            audio_tensor, sample_rate, self.model.samplerate, self.model.audio_channels
        )

        # Add batch dimension: (channels, samples) -> (1, channels, samples)
        audio_tensor = audio_tensor.unsqueeze(0).to(self.device)

        # Apply model
        with torch.no_grad():
            sources = apply_model(self.model, audio_tensor, progress=True)

        # sources shape: (1, num_stems, channels, samples)
        # stems are typically: drums, bass, other, vocals
        sources = sources[0]  # remove batch dim

        # Sum non-vocal stems to get music
        # Find vocal index
        stem_names = self.model.sources
        vocal_idx = stem_names.index("vocals") if "vocals" in stem_names else -1

        if vocal_idx >= 0:
            # Sum all stems except vocals
            music = torch.sum(
                sources[[i for i in range(len(stem_names)) if i != vocal_idx]], dim=0
            )
        else:
            # No vocals stem, sum all
            music = torch.sum(sources, dim=0)

        # Resample back to original sample rate
        music = convert_audio(music, self.model.samplerate, sample_rate, music.shape[0])

        # Convert back to numpy
        music_np = music.cpu().numpy()

        # Convert back to mono if input was mono
        if was_mono:
            music_np = music_np.mean(axis=0)

        return music_np.astype(np.float32)
