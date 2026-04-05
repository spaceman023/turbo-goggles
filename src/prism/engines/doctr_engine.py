"""docTR OCR engine adapter."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class DocTREngine(OCREngine):
    name = "doctr"

    def __init__(self) -> None:
        self._predictor = None  # lazy-init (heavy import)

    def _get_predictor(self):
        if self._predictor is not None:
            return self._predictor

        logger.debug("docTR: first use — importing and initialising predictor …")
        t0 = time.monotonic()

        try:
            from doctr.models import ocr_predictor
        except ImportError:
            raise ImportError(
                "docTR is not installed. Install it with: "
                "pip install python-doctr[torch]  (or python-doctr[tf])"
            )

        self._predictor = ocr_predictor(pretrained=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.debug("docTR: predictor initialised in %dms", elapsed_ms)
        return self._predictor

    def run(self, image_path: Path, options: dict) -> str:
        logger.debug(
            "docTR starting: image=%s, image_size=%.1f KB",
            image_path.name,
            image_path.stat().st_size / 1024 if image_path.exists() else 0,
        )

        try:
            from doctr.io import DocumentFile
        except ImportError:
            raise ImportError(
                "docTR is not installed. Install it with: "
                "pip install python-doctr[torch]  (or python-doctr[tf])"
            )

        predictor = self._get_predictor()

        t0 = time.monotonic()
        doc = DocumentFile.from_images([str(image_path)])
        result = predictor(doc)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Extract text from docTR result: pages → blocks → lines → words
        lines: list[str] = []
        word_count = 0
        for page in result.pages:
            for block in page.blocks:
                for line in block.lines:
                    words = [word.value for word in line.words]
                    word_count += len(words)
                    line_text = " ".join(words)
                    lines.append(line_text)
                    logger.debug("  docTR line: %r", line_text[:80])

        text = "\n".join(lines)

        logger.debug(
            "docTR finished: image=%s, elapsed=%dms, lines=%d, words=%d, chars=%d",
            image_path.name,
            elapsed_ms,
            len(lines),
            word_count,
            len(text),
        )

        if not text.strip():
            logger.warning(
                "docTR returned empty/whitespace-only text for %s", image_path.name
            )

        return text
