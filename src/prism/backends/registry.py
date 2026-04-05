"""Registry of LLM backends."""

from __future__ import annotations

import logging
import os
import shutil

from prism.backends.base import LLMBackend

logger = logging.getLogger(__name__)

_ALL_BACKENDS = ("openrouter", "codex", "gemini", "claude")


def get_backend(name: str, model: str | None = None, options: dict | None = None) -> LLMBackend:
    """Return an instance of the named LLM backend."""
    logger.debug("get_backend: name=%s, model=%s, options=%s", name, model, options)

    if name == "openrouter":
        from prism.backends.openrouter import OpenRouterBackend

        if not model:
            logger.error("OpenRouter backend requested without a model")
            raise ValueError("OpenRouter backend requires a 'model' field.")
        return OpenRouterBackend(model=model, options=options)

    if name == "codex":
        from prism.backends.codex import CodexBackend

        return CodexBackend(options=options)

    if name == "gemini":
        from prism.backends.gemini_cli import GeminiCLIBackend

        return GeminiCLIBackend(options=options)

    if name == "claude":
        from prism.backends.claude_cli import ClaudeCLIBackend

        return ClaudeCLIBackend(options=options)

    logger.error("Unknown LLM backend: %s", name)
    raise ValueError(
        f"Unknown LLM backend '{name}'. Available: {', '.join(_ALL_BACKENDS)}"
    )


def check_backends() -> dict[str, dict]:
    """Return health status for all known backends.

    Returns a dict keyed by backend name, each value containing:
        ``available`` (bool) and ``detail`` (str).
    """
    status: dict[str, dict] = {}

    # openrouter — needs API key
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if api_key:
        status["openrouter"] = {"available": True, "detail": "OPENROUTER_API_KEY is set"}
    else:
        status["openrouter"] = {
            "available": False,
            "detail": "OPENROUTER_API_KEY environment variable not set",
        }

    # CLI-based backends — need the binary on PATH
    cli_backends = {
        "codex": "codex",
        "gemini": "gemini",
        "claude": "claude",
    }
    for backend_name, binary in cli_backends.items():
        path = shutil.which(binary)
        if path:
            status[backend_name] = {"available": True, "detail": f"found at {path}"}
        else:
            status[backend_name] = {
                "available": False,
                "detail": f"'{binary}' not found on PATH",
            }

    return status
