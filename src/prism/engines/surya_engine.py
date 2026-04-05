"""Surya OCR engine adapter."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class SuryaEngine(OCREngine):
    name = "surya"

    def __init__(self) -> None:
        self._det_model = None
        self._det_processor = None
        self._rec_model = None
        self._rec_processor = None

    def _load_models(self):
        """Lazy-load Surya detection and recognition models (once)."""
        if self._det_model is not None:
            return

        logger.debug("Surya: first use — importing and loading models …")
        t0 = time.monotonic()

        try:
            from surya.model.detection.model import load_det_model, load_det_processor
            from surya.model.recognition.model import load_rec_model
            from surya.model.recognition.processor import load_rec_processor
        except ImportError:
            raise ImportError(
                "Surya OCR is not installed. Install it with: pip install surya-ocr"
            )

        self._det_model = load_det_model()
        self._det_processor = load_det_processor()
        self._rec_model = load_rec_model()
        self._rec_processor = load_rec_processor()

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.debug("Surya: models loaded in %dms", elapsed_ms)

    def run(self, image_path: Path, options: dict) -> str:
        languages = options.get("languages", ["en"])
        if isinstance(languages, str):
            languages = [languages]

        logger.debug(
            "Surya starting: image=%s, languages=%s, image_size=%.1f KB",
            image_path.name,
            languages,
            image_path.stat().st_size / 1024 if image_path.exists() else 0,
        )

        try:
            from surya.ocr import run_ocr
        except ImportError:
            raise ImportError(
                "Surya OCR is not installed. Install it with: pip install surya-ocr"
            )

        from PIL import Image

        self._load_models()

        image = Image.open(image_path)

        t0 = time.monotonic()
        results = run_ocr(
            [image],
            [languages],
            self._det_model,
            self._det_processor,
            self._rec_model,
            self._rec_processor,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Extract text from Surya result structure
        lines: list[str] = []
        if results:
            for text_line in results[0].text_lines:
                lines.append(text_line.text)
                logger.debug(
                    "  Surya detection: conf=%.3f text=%r",
                    text_line.confidence,
                    text_line.text[:80] + ("…" if len(text_line.text) > 80 else ""),
                )

        text = "\n".join(lines)
        word_count = len(text.split()) if text else 0

        logger.debug(
            "Surya finished: image=%s, elapsed=%dms, lines=%d, words=%d, chars=%d",
            image_path.name,
            elapsed_ms,
            len(lines),
            word_count,
            len(text),
        )

        if not text.strip():
            logger.warning(
                "Surya returned empty/whitespace-only text for %s", image_path.name
            )

        return text
