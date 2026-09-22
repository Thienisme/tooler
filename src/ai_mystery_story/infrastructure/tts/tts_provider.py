from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):

    @abstractmethod
    def generate(
        self,
        text: str,
        output_path: Path,
    ) -> float:
        """
        Generate audio from text.

        Returns:
            Audio duration in seconds.
        """
        raise NotImplementedError