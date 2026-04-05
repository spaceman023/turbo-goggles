"""macOS Vision Framework OCR engine adapter.

Uses Apple's Vision framework via pyobjc for on-device text recognition.
Only available on macOS with pyobjc-framework-Vision installed.
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)


class MacOSVisionEngine(OCREngine):
    name = "macos_vision"

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            raise ImportError(
                "macOS Vision OCR is only available on macOS. "
                f"Current platform: {platform.system()}"
            )

    def run(self, image_path: Path, options: dict) -> str:
        languages = options.get("languages", ["en-US"])
        if isinstance(languages, str):
            languages = [languages]

        logger.debug(
            "macOS Vision starting: image=%s, languages=%s, image_size=%.1f KB",
            image_path.name,
            languages,
            image_path.stat().st_size / 1024 if image_path.exists() else 0,
        )

        try:
            import Vision
            from Quartz import (
                CGImageSourceCreateWithURL,
                CGImageSourceCreateImageAtIndex,
            )
            from Foundation import NSURL
        except ImportError:
            raise ImportError(
                "pyobjc Vision framework is not installed. Install it with: "
                "pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
            )

        t0 = time.monotonic()

        # Load the image via CoreGraphics
        image_url = NSURL.fileURLWithPath_(str(image_path))
        image_source = CGImageSourceCreateWithURL(image_url, None)
        if image_source is None:
            logger.error("macOS Vision: failed to load image %s", image_path.name)
            return ""
        cg_image = CGImageSourceCreateImageAtIndex(image_source, 0, None)
        if cg_image is None:
            logger.error(
                "macOS Vision: failed to create CGImage from %s", image_path.name
            )
            return ""

        # Create and configure the text recognition request
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(
            Vision.VNRequestTextRecognitionLevelAccurate
        )
        request.setRecognitionLanguages_(languages)
        request.setUsesLanguageCorrection_(True)

        # Create a handler and perform the request
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )

        success = handler.performRequests_error_([request], None)
        if not success[0]:
            error = success[1]
            logger.error("macOS Vision: request failed — %s", error)
            return ""

        # Extract recognized text from results
        lines: list[str] = []
        observations = request.results()
        if observations:
            for observation in observations:
                # Each observation is a VNRecognizedTextObservation
                top_candidate = observation.topCandidates_(1)
                if top_candidate and len(top_candidate) > 0:
                    text_value = top_candidate[0].string()
                    confidence = observation.confidence()
                    lines.append(text_value)
                    logger.debug(
                        "  Vision detection: conf=%.3f text=%r",
                        confidence,
                        text_value[:80]
                        + ("…" if len(text_value) > 80 else ""),
                    )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        text = "\n".join(lines)
        word_count = len(text.split()) if text else 0

        logger.debug(
            "macOS Vision finished: image=%s, elapsed=%dms, lines=%d, words=%d, chars=%d",
            image_path.name,
            elapsed_ms,
            len(lines),
            word_count,
            len(text),
        )

        if not text.strip():
            logger.warning(
                "macOS Vision returned empty/whitespace-only text for %s",
                image_path.name,
            )

        return text
