"""Claude Code CLI backend adapter."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time

from prism.backends.base import LLMBackend, LLMResponse

logger = logging.getLogger(__name__)


class ClaudeCLIBackend(LLMBackend):
    name = "claude"

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}
        claude_path = shutil.which("claude")
        if claude_path is None:
            raise ValueError(
                "Claude Code CLI ('claude') not found on PATH. "
                "Install it to use the claude backend."
            )
        logger.debug("ClaudeCLIBackend initialised: claude found at %s", claude_path)

    def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        if image is not None:
            logger.warning("Claude Code CLI does not support image input; image will be ignored.")

        logger.debug(
            "Claude request: prompt_chars=%d, prompt_words=%d",
            len(prompt),
            len(prompt.split()),
        )

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.error("Claude CLI timed out after 120s (elapsed=%dms)", duration_ms)
            raise RuntimeError("Claude Code CLI timed out after 120 seconds.") from None
        except OSError as exc:
            logger.error("Failed to execute claude CLI: %s", exc)
            raise RuntimeError(f"Failed to execute claude CLI: {exc}") from exc

        duration_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            logger.error(
                "Claude CLI exited with code %d: stderr=%s",
                result.returncode,
                result.stderr[:500] if result.stderr else "(empty)",
            )
            raise RuntimeError(
                f"Claude Code CLI failed (exit code {result.returncode}): "
                f"{result.stderr[:500] if result.stderr else '(no stderr)'}"
            )

        # Parse JSON response and extract the result field
        raw_output = result.stdout.strip()
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            logger.error(
                "Claude CLI returned invalid JSON: %s (raw output: %s)",
                exc,
                raw_output[:500],
            )
            raise RuntimeError(
                f"Claude Code CLI returned invalid JSON: {exc}"
            ) from exc

        text = data.get("result", "")
        if not isinstance(text, str):
            logger.warning(
                "Claude CLI 'result' field is not a string (type=%s); converting.",
                type(text).__name__,
            )
            text = str(text)

        logger.debug(
            "Claude response: response_chars=%d, response_words=%d, elapsed=%dms",
            len(text),
            len(text.split()),
            duration_ms,
        )

        if not text:
            logger.warning("Claude CLI returned empty result.")

        return LLMResponse(
            text=text,
            tokens_in=None,
            tokens_out=None,
            cost=None,
            model="claude-cli",
            duration_ms=duration_ms,
        )
