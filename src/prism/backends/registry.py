"""Registry of LLM backends."""

from __future__ import annotations

import logging

from prism.backends.base import LLMBackend
from prism.backends.openrouter import OpenRouterBackend

logger = logging.getLogger(__name__)


def get_backend(name: str, model: str | None = None, options: dict | None = None) -> LLMBackend:
    """Return an instance of the named LLM backend."""
    logger.debug("get_backend: name=%s, model=%s, options=%s", name, model, options)
    if name == "openrouter":
        if not model:
            logger.error("OpenRouter backend requested without a model")
            raise ValueError("OpenRouter backend requires a 'model' field.")
        return OpenRouterBackend(model=model, options=options)
    logger.error("Unknown LLM backend: %s", name)
    raise ValueError(f"Unknown LLM backend '{name}'. Available: openrouter")
