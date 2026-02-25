"""Pipeline orchestrator."""

from pathlib import Path

from fiwi_filmmusik.aggregator import ChunkAggregator
from fiwi_filmmusik.chunker import AudioChunker
from fiwi_filmmusik.classifiers import BaseClassifier
from fiwi_filmmusik.detection import BaseMusicDetectionClient
from fiwi_filmmusik.isolators import BaseMusicIsolator
from fiwi_filmmusik.loaders import VideoLoader
from fiwi_filmmusik.models import DetectionResult, ResultsOutput


class OutputWriter:
    """Handles writing detection results to disk."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write(
        self,
        video_path: Path,
        results: list[DetectionResult],
    ) -> ResultsOutput:
        """Write detection results to disk.

        Creates:
        - output_dir/<video_name>/results.json
        - output_dir/<video_name>/chunks/<index>_<start>-<end>.mp3
        """
        raise NotImplementedError


class Pipeline:
    def __init__(
        self,
        loader: VideoLoader,
        chunker: AudioChunker,
        classifier: BaseClassifier,
        aggregator: ChunkAggregator,
        isolator: BaseMusicIsolator,
        detection_client: BaseMusicDetectionClient,
        output_dir: Path | None = None,
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.classifier = classifier
        self.aggregator = aggregator
        self.isolator = isolator
        self.detection_client = detection_client
        self.output_dir = output_dir or Path("./output")
        self.output_writer = OutputWriter(self.output_dir)

    def run(self, video_path: Path) -> ResultsOutput:
        """Run the pipeline on a video file.

        Returns:
            ResultsOutput containing all detected segments and their metadata.
        """
        raise NotImplementedError
