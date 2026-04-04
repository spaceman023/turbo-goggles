"""Tesseract OCR engine adapter."""

from __future__ import annotations

import logging
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

        logger.debug("Running Tesseract (lang=%s) on %s", lang, image_path.name)
        text: str = pytesseract.image_to_string(str(image_path), lang=lang)
        return text
