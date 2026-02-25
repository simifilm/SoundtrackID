"""Configuration loading and management."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    """Pipeline configuration."""

    video_dir: Path = Path("./videos")
    output_dir: Path = Path("./output")
    sample_rate: int = 44100
    mono: bool = True
    chunk_duration: float = 10.0
    overlap: float = 2.0
    classifier_model: str = "dummy"
    confidence_threshold: float = 0.5
    gap_tolerance: float = 1.0
    isolator_model: str = "dummy"
    identification_api: str = "dummy"

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        # Convert path strings to Path objects
        if "video_dir" in data:
            data["video_dir"] = Path(data["video_dir"])
        if "output_dir" in data:
            data["output_dir"] = Path(data["output_dir"])

        return cls(**data)


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from file or return defaults."""
    if path is None:
        path = Path("config.yaml")
    else:
        path = Path(path)

    if path.exists():
        return Config.from_yaml(path)

    return Config()
