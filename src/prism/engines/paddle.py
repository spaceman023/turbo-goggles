"""PaddleOCR engine adapter — supports both v2.x and v3.x API."""

from __future__ import annotations

import logging
import os
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
            # Skip slow connectivity check on init
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            t0 = time.monotonic()
            from paddleocr import PaddleOCR

            try:
                self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            except (TypeError, ValueError):
                # PaddleOCR v3.x removed show_log and renamed use_angle_cls
                try:
                    self._ocr = PaddleOCR(lang=lang)
                except Exception:
                    self._ocr = PaddleOCR()
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
        lines: list[str] = []

        # Try new predict() API first (PaddleOCR v3.x), fall back to ocr() (v2.x)
        try:
            result = ocr.predict(str(image_path))
            for page_result in result:
                # v3.x predict returns objects with rec_texts or similar attributes
                if hasattr(page_result, "rec_texts"):
                    lines.extend(page_result.rec_texts)
                elif hasattr(page_result, "text"):
                    lines.append(page_result.text)
                elif isinstance(page_result, dict):
                    if "rec_text" in page_result:
                        lines.append(page_result["rec_text"])
                    elif "text" in page_result:
                        lines.append(page_result["text"])
                else:
                    # Try string representation as last resort
                    s = str(page_result)
                    if s and len(s) < 10000:
                        lines.append(s)
        except (AttributeError, TypeError):
            # Fall back to old ocr() API (v2.x)
            try:
                result = ocr.ocr(str(image_path), cls=True)
            except TypeError:
                result = ocr.ocr(str(image_path))

            if result and result[0]:
                for line_info in result[0]:
                    try:
                        if isinstance(line_info, dict):
                            text = line_info.get("text", line_info.get("rec_text", ""))
                        else:
                            text = line_info[1][0]
                        if text:
                            lines.append(text)
                    except (IndexError, KeyError, TypeError):
                        pass

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        joined = "\n".join(lines)

        logger.debug(
            "PaddleOCR finished: image=%s, elapsed=%dms, lines=%d, chars=%d",
            image_path.name,
            elapsed_ms,
            len(lines),
            len(joined),
        )

        if not joined.strip():
            logger.warning("PaddleOCR returned empty text for %s", image_path.name)

        return joined
