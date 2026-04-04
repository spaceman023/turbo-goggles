"""Abstract base for LLM backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: float | None = None
    model: str = ""
    duration_ms: int = 0


class LLMBackend(ABC):
    name: str

    @abstractmethod
    def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        """Send prompt (and optional image) to the LLM. Return response."""
        ...
