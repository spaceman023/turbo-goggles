"""Jinja2 prompt templating system."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader

logger = logging.getLogger(__name__)

_env = Environment(loader=BaseLoader(), keep_trailing_newline=True)


def render_prompt(
    template_path: Path,
    variables: dict[str, Any],
) -> str:
    """Load a Jinja2 template from *template_path* and render with *variables*."""
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    raw = template_path.read_text(encoding="utf-8")
    template = _env.from_string(raw)
    rendered = template.render(**variables)
    return rendered


def default_variables(
    text: str = "",
    page_numbers: list[int] | None = None,
    chunk_index: int = 0,
    total_chunks: int = 1,
    filename: str = "",
    layer_name: str = "",
    previous_layers: list[dict] | None = None,
    sources: list[dict] | None = None,
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standard variable dict for prompt rendering."""
    return {
        "text": text,
        "page_numbers": page_numbers or [],
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "filename": filename,
        "layer_name": layer_name,
        "previous_layers": previous_layers or [],
        "sources": sources or [],
        "custom": custom or {},
    }
