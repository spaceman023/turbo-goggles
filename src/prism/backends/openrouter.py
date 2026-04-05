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
        logger.debug(
            "OpenRouterBackend initialised: model=%s, temperature=%s, max_tokens=%s, api_key=%s…%s",
            self.model,
            self.options.get("temperature", 0.1),
            self.options.get("max_tokens", 4096),
            self.api_key[:4],
            self.api_key[-4:] if len(self.api_key) > 8 else "****",
        )

    def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        prompt_len = len(prompt)
        prompt_word_count = len(prompt.split())
        logger.debug(
            "OpenRouter request: model=%s, prompt_chars=%d, prompt_words=%d, has_image=%s",
            self.model,
            prompt_len,
            prompt_word_count,
            image is not None,
        )
        if image is not None:
            logger.debug("  Image payload size: %.1f KB", len(image) / 1024)

        content: list[dict] = [{"type": "text", "text": prompt}]

        if image is not None:
            b64 = base64.b64encode(image).decode()
            logger.debug("  Base64 encoded image: %d chars", len(b64))
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )

        messages = [{"role": "user", "content": content}]

        temperature = self.options.get("temperature", 0.1)
        max_tokens = self.options.get("max_tokens", 4096)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "  POST %s — model=%s, temperature=%s, max_tokens=%d",
            OPENROUTER_URL,
            self.model,
            temperature,
            max_tokens,
        )

        # Retry with exponential backoff for rate limits
        max_retries = 3
        for attempt in range(max_retries + 1):
            logger.debug("  Attempt %d/%d …", attempt + 1, max_retries + 1)
            t0 = time.monotonic()
            try:
                resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
            except requests.exceptions.Timeout:
                logger.error("  Request timed out after 120s (attempt %d/%d)", attempt + 1, max_retries + 1)
                raise
            except requests.exceptions.ConnectionError as e:
                logger.error("  Connection error (attempt %d/%d): %s", attempt + 1, max_retries + 1, e)
                raise
            duration_ms = int((time.monotonic() - t0) * 1000)

            logger.debug(
                "  Response: status=%d, elapsed=%dms, body_size=%d bytes",
                resp.status_code,
                duration_ms,
                len(resp.content),
            )

            if resp.status_code == 429 and attempt < max_retries:
                wait = 2 ** (attempt + 1)
                retry_after = resp.headers.get("retry-after")
                logger.warning(
                    "  Rate limited (429). retry-after=%s, waiting %ds before retry …",
                    retry_after,
                    wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                logger.error(
                    "  HTTP error %d: %s",
                    resp.status_code,
                    resp.text[:500],
                )

            resp.raise_for_status()
            break

        data = resp.json()

        # Log response structure for debugging
        if "choices" not in data:
            logger.error("  Unexpected response structure (no 'choices'): %s", list(data.keys()))

        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")

        logger.debug(
            "OpenRouter response: model=%s, tokens_in=%s, tokens_out=%s, "
            "response_chars=%d, response_words=%d, elapsed=%dms",
            data.get("model", self.model),
            tokens_in,
            tokens_out,
            len(text),
            len(text.split()),
            duration_ms,
        )

        if not text.strip():
            logger.warning("OpenRouter returned empty/whitespace-only response for model=%s", self.model)

        return LLMResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=None,
            model=self.model,
            duration_ms=duration_ms,
        )
