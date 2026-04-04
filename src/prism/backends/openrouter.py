"""OpenRouter API backend adapter."""

from __future__ import annotations

import base64
import logging
import os
import time

import requests

from prism.backends.base import LLMBackend, LLMResponse

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterBackend(LLMBackend):
    name = "openrouter"

    def __init__(self, model: str, options: dict | None = None) -> None:
        self.model = model
        self.options = options or {}
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY environment variable is required for the openrouter backend."
            )

    def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        content: list[dict] = [{"type": "text", "text": prompt}]

        if image is not None:
            b64 = base64.b64encode(image).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )

        messages = [{"role": "user", "content": content}]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.options.get("temperature", 0.1),
            "max_tokens": self.options.get("max_tokens", 4096),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Retry with exponential backoff for rate limits
        max_retries = 3
        for attempt in range(max_retries + 1):
            t0 = time.monotonic()
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
            duration_ms = int((time.monotonic() - t0) * 1000)

            if resp.status_code == 429 and attempt < max_retries:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited. Retrying in %ds …", wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            break

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return LLMResponse(
            text=text,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            cost=None,  # OpenRouter doesn't always return cost inline
            model=self.model,
            duration_ms=duration_ms,
        )
