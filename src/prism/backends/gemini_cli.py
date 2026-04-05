"""Google Gemini CLI backend adapter."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from prism.backends.base import LLMBackend, LLMResponse

logger = logging.getLogger(__name__)


class GeminiCLIBackend(LLMBackend):
    name = "gemini"

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}
        gemini_path = shutil.which("gemini")
        if gemini_path is None:
            raise ValueError(
                "Gemini CLI ('gemini') not found on PATH. "
                "Install it to use the gemini backend."
            )
        logger.debug("GeminiCLIBackend initialised: gemini found at %s", gemini_path)

    def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        cmd = ["gemini", "-o", "text", prompt]

        logger.debug(
            "Gemini request: prompt_chars=%d, prompt_words=%d, has_image=%s",
            len(prompt),
            len(prompt.split()),
            image is not None,
        )

        temp_image_path: Path | None = None
        try:
            if image is not None:
                # Write image to a temp file so gemini CLI can read it
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False, prefix="prism_gemini_"
                )
                tmp.write(image)
                tmp.close()
                temp_image_path = Path(tmp.name)
                cmd = ["gemini", "-o", "text", "-f", str(temp_image_path), prompt]
                logger.debug(
                    "  Image written to temp file: %s (%.1f KB)",
                    temp_image_path,
                    len(image) / 1024,
                )

            t0 = time.monotonic()
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.error("Gemini CLI timed out after 120s (elapsed=%dms)", duration_ms)
                raise RuntimeError("Gemini CLI timed out after 120 seconds.") from None
            except OSError as exc:
                logger.error("Failed to execute gemini CLI: %s", exc)
                raise RuntimeError(f"Failed to execute gemini CLI: {exc}") from exc

            duration_ms = int((time.monotonic() - t0) * 1000)
        finally:
            # Clean up temp file
            if temp_image_path is not None:
                try:
                    temp_image_path.unlink()
                    logger.debug("  Cleaned up temp image file: %s", temp_image_path)
                except OSError:
                    logger.warning("  Failed to clean up temp image file: %s", temp_image_path)

        if result.returncode != 0:
            logger.error(
                "Gemini CLI exited with code %d: stderr=%s",
                result.returncode,
                result.stderr[:500] if result.stderr else "(empty)",
            )
            raise RuntimeError(
                f"Gemini CLI failed (exit code {result.returncode}): "
                f"{result.stderr[:500] if result.stderr else '(no stderr)'}"
            )

        text = result.stdout.strip()
        logger.debug(
            "Gemini response: response_chars=%d, response_words=%d, elapsed=%dms",
            len(text),
            len(text.split()),
            duration_ms,
        )

        if not text:
            logger.warning("Gemini CLI returned empty output.")

        return LLMResponse(
            text=text,
            tokens_in=None,
            tokens_out=None,
            cost=None,
            model="gemini-cli",
            duration_ms=duration_ms,
        )
