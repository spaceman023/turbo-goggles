"""PaddleOCR engine adapter."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class PaddleEngine(OCREngine):
    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr = None  # lazy-init (heavy import)

    def _get_ocr(self, lang: str):
        if self._ocr is None:
            logger.debug("PaddleOCR: first use — importing and initialising (lang=%s) …", lang)
            t0 = time.monotonic()
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.debug("PaddleOCR: initialised in %dms", elapsed_ms)
        return self._ocr

    def run(self, image_path: Path, options: dict) -> str:
        languages = options.get("languages", ["en"])
        lang = languages[0] if isinstance(languages, list) else languages

        logger.debug(
            "PaddleOCR starting: image=%s, lang=%s, image_size=%.1f KB",
            image_path.name,
            lang,
            image_path.stat().st_size / 1024 if image_path.exists() else 0,
        )

        ocr = self._get_ocr(lang)

        t0 = time.monotonic()
        result = ocr.ocr(str(image_path), cls=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        lines: list[str] = []
        detection_count = 0
        if result and result[0]:
            for line_info in result[0]:
                text = line_info[1][0]
                confidence = line_info[1][1]
                lines.append(text)
                detection_count += 1
                logger.debug(
                    "  PaddleOCR detection: conf=%.3f text=%r",
                    confidence,
                    text[:80] + ("…" if len(text) > 80 else ""),
                )

        joined = "\n".join(lines)
        word_count = len(joined.split()) if joined else 0

        logger.debug(
            "PaddleOCR finished: image=%s, elapsed=%dms, detections=%d, words=%d, chars=%d",
            image_path.name,
            elapsed_ms,
            detection_count,
            word_count,
            len(joined),
        )

        if not joined.strip():
            logger.warning("PaddleOCR returned empty/whitespace-only text for %s", image_path.name)

        return joined
