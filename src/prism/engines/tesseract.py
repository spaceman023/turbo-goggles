"""Tesseract OCR engine adapter."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class TesseractEngine(OCREngine):
    name = "tesseract"

    def run(self, image_path: Path, options: dict) -> str:
        import pytesseract

        lang = options.get("languages", ["eng"])
        if isinstance(lang, list):
            lang = "+".join(lang)

        logger.debug(
            "Tesseract starting: image=%s, lang=%s, image_size=%.1f KB",
            image_path.name,
            lang,
            image_path.stat().st_size / 1024 if image_path.exists() else 0,
        )

        t0 = time.monotonic()
        text: str = pytesseract.image_to_string(str(image_path), lang=lang)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        char_count = len(text)
        line_count = text.count("\n") + 1 if text else 0
        word_count = len(text.split()) if text else 0

        logger.debug(
            "Tesseract finished: image=%s, elapsed=%dms, chars=%d, words=%d, lines=%d",
            image_path.name,
            elapsed_ms,
            char_count,
            word_count,
            line_count,
        )

        if not text.strip():
            logger.warning("Tesseract returned empty/whitespace-only text for %s", image_path.name)

        return text
