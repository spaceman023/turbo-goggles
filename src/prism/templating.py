"""Jinja2 prompt templating system."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader, TemplateSyntaxError

logger = logging.getLogger(__name__)

_env = Environment(loader=BaseLoader(), keep_trailing_newline=True)


def render_prompt(
    template_path: Path,
    variables: dict[str, Any],
) -> str:
    """Load a Jinja2 template from *template_path* and render with *variables*."""
    logger.debug("render_prompt: template=%s", template_path)

    if not template_path.exists():
        logger.error("Prompt template not found: %s", template_path)
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    raw = template_path.read_text(encoding="utf-8")
    logger.debug(
        "  Template loaded: %d chars, %d lines",
        len(raw),
        raw.count("\n") + 1,
    )

    # Log which variables are provided (keys + types, not full values)
    var_summary = {k: type(v).__name__ for k, v in variables.items()}
    logger.debug("  Template variables: %s", var_summary)

    try:
        template = _env.from_string(raw)
    except TemplateSyntaxError as e:
        logger.error("  Jinja2 syntax error in %s: %s (line %s)", template_path.name, e.message, e.lineno)
        raise

    rendered = template.render(**variables)

    logger.debug(
        "  Rendered prompt: %d chars, %d words, %d lines",
        len(rendered),
        len(rendered.split()),
        rendered.count("\n") + 1,
    )

    # Rough token estimate for LLM context planning
    estimated_tokens = int(len(rendered.split()) * 1.3)
    logger.debug("  Estimated token count: ~%d", estimated_tokens)

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
    result = {
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
    logger.debug(
        "default_variables: filename=%s, layer=%s, chunk=%d/%d, pages=%s, "
        "text_len=%d, sources_count=%d",
        filename,
        layer_name,
        chunk_index,
        total_chunks,
        page_numbers or [],
        len(text),
        len(sources or []),
    )
    return result
