"""PaddleOCR engine adapter."""

from __future__ import annotations

import logging
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class PaddleEngine(OCREngine):
    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr = None  # lazy-init (heavy import)

    def _get_ocr(self, lang: str):
        if self._ocr is None:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        return self._ocr

    def run(self, image_path: Path, options: dict) -> str:
        languages = options.get("languages", ["en"])
        lang = languages[0] if isinstance(languages, list) else languages

        logger.debug("Running PaddleOCR (lang=%s) on %s", lang, image_path.name)
        ocr = self._get_ocr(lang)
        result = ocr.ocr(str(image_path), cls=True)

        lines: list[str] = []
        if result and result[0]:
            for line_info in result[0]:
                text = line_info[1][0]
                lines.append(text)

        return "\n".join(lines)
