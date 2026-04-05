"""EasyOCR engine adapter."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class EasyOCREngine(OCREngine):
    name = "easyocr"

    def __init__(self) -> None:
        self._reader = None  # lazy-init (heavy import)

    def _get_reader(self, languages: list[str], gpu: bool):
        if self._reader is None:
            logger.debug(
                "EasyOCR: first use — importing and initialising (languages=%s, gpu=%s) …",
                languages,
                gpu,
            )
            t0 = time.monotonic()
            try:
                import easyocr
            except ImportError:
                raise ImportError(
                    "EasyOCR is not installed. Install it with: pip install easyocr"
                )
            self._reader = easyocr.Reader(languages, gpu=gpu)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.debug("EasyOCR: initialised in %dms", elapsed_ms)
        return self._reader

    def run(self, image_path: Path, options: dict) -> str:
        languages = options.get("languages", ["en"])
        if isinstance(languages, str):
            languages = [languages]

        # Detect GPU availability
        from prism.engines.gpu import _gpu_available

        gpu = _gpu_available()

        logger.debug(
            "EasyOCR starting: image=%s, languages=%s, gpu=%s, image_size=%.1f KB",
            image_path.name,
            languages,
            gpu,
            image_path.stat().st_size / 1024 if image_path.exists() else 0,
        )

        reader = self._get_reader(languages, gpu)

        t0 = time.monotonic()
        results: list[str] = reader.readtext(str(image_path), detail=0)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        text = "\n".join(results)
        char_count = len(text)
        word_count = len(text.split()) if text else 0
        line_count = len(results)

        logger.debug(
            "EasyOCR finished: image=%s, elapsed=%dms, chars=%d, words=%d, lines=%d",
            image_path.name,
            elapsed_ms,
            char_count,
            word_count,
            line_count,
        )

        if not text.strip():
            logger.warning(
                "EasyOCR returned empty/whitespace-only text for %s", image_path.name
            )

        return text
