"""Abstract base for OCR engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class OCREngine(ABC):
    """Process a single page image and return raw text."""

    name: str

    @abstractmethod
    def run(self, image_path: Path, options: dict) -> str:
        """Run OCR on *image_path* and return extracted text."""
        ...
