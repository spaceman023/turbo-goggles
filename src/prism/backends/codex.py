"""OpenAI Codex CLI backend adapter."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time

from prism.backends.base import LLMBackend, LLMResponse

logger = logging.getLogger(__name__)


class CodexBackend(LLMBackend):
    name = "codex"

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}
        codex_path = shutil.which("codex")
        if codex_path is None:
            raise ValueError(
                "Codex CLI ('codex') not found on PATH. "
                "Install it to use the codex backend."
            )
        logger.debug("CodexBackend initialised: codex found at %s", codex_path)

    def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        if image is not None:
            logger.warning("Codex CLI does not support image input; image will be ignored.")

        logger.debug(
            "Codex request: prompt_chars=%d, prompt_words=%d",
            len(prompt),
            len(prompt.split()),
        )

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                ["codex", "exec", prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.error("Codex CLI timed out after 120s (elapsed=%dms)", duration_ms)
            raise RuntimeError("Codex CLI timed out after 120 seconds.") from None
        except OSError as exc:
            logger.error("Failed to execute codex CLI: %s", exc)
            raise RuntimeError(f"Failed to execute codex CLI: {exc}") from exc

        duration_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            logger.error(
                "Codex CLI exited with code %d: stderr=%s",
                result.returncode,
                result.stderr[:500] if result.stderr else "(empty)",
            )
            raise RuntimeError(
                f"Codex CLI failed (exit code {result.returncode}): "
                f"{result.stderr[:500] if result.stderr else '(no stderr)'}"
            )

        text = result.stdout.strip()
        logger.debug(
            "Codex response: response_chars=%d, response_words=%d, elapsed=%dms",
            len(text),
            len(text.split()),
            duration_ms,
        )

        if not text:
            logger.warning("Codex CLI returned empty output.")

        return LLMResponse(
            text=text,
            tokens_in=None,
            tokens_out=None,
            cost=None,
            model="codex-cli",
            duration_ms=duration_ms,
        )
