"""Registry of LLM backends."""

from __future__ import annotations

from prism.backends.base import LLMBackend
from prism.backends.openrouter import OpenRouterBackend


def get_backend(name: str, model: str | None = None, options: dict | None = None) -> LLMBackend:
    """Return an instance of the named LLM backend."""
    if name == "openrouter":
        if not model:
            raise ValueError("OpenRouter backend requires a 'model' field.")
        return OpenRouterBackend(model=model, options=options)
    raise ValueError(f"Unknown LLM backend '{name}'. Available: openrouter")
