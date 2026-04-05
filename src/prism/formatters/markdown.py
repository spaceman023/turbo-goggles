"""Markdown output formatter — merges chunk markdown into a single document."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def format_markdown(
    chunk_texts: list[str],
    output_path: Path,
    options: dict | None = None,
) -> Path:
    """Merge chunk markdown texts into a single .md file.

    Each chunk is expected to already contain valid markdown (produced by an
    LLM layer using the markdown_format prompt).  This formatter:

    1. Strips leading/trailing whitespace from each chunk.
    2. Normalises heading levels so the final document has a coherent
       hierarchy (no duplicate H1s, sections don't restart at H1 per chunk).
    3. Joins chunks with thematic breaks (``---``) unless ``seamless`` is set,
       in which case chunks are joined with a single blank line.
    """
    options = options or {}
    seamless = options.get("seamless", False)
    title = options.get("title", "")

    non_empty = [t.strip() for t in chunk_texts if t.strip()]
    empty_count = len(chunk_texts) - len(non_empty)

    logger.debug(
        "format_markdown: %d chunks (%d non-empty, %d empty/blank), "
        "seamless=%s, title=%r, output=%s",
        len(chunk_texts),
        len(non_empty),
        empty_count,
        seamless,
        title,
        output_path,
    )

    if empty_count > 0:
        logger.warning(
            "%d of %d chunks were empty/whitespace-only and will be skipped",
            empty_count,
            len(chunk_texts),
        )

    normalised = _normalise_headings(non_empty)

    parts: list[str] = []
    if title:
        parts.append(f"# {title}\n")

    separator = "\n\n" if seamless else "\n\n---\n\n"
    parts.append(separator.join(normalised))

    merged = "\n".join(parts)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(merged, encoding="utf-8")

    logger.debug(
        "  Wrote %d chars (%.1f KB) to %s",
        len(merged),
        len(merged.encode("utf-8")) / 1024,
        output_path.name,
    )

    return output_path


# ── heading normalisation ───────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)


def _normalise_headings(chunks: list[str]) -> list[str]:
    """Shift heading levels so at most one H1 exists across all chunks.

    If more than one chunk contains an H1 (``# …``), demote every chunk's
    headings by the minimum amount needed so that H1 only appears in the
    first chunk that has one.
    """
    # Find the minimum heading level in each chunk
    h1_chunks: list[int] = []
    for i, text in enumerate(chunks):
        levels = [len(m.group(1)) for m in _HEADING_RE.finditer(text)]
        if levels and min(levels) == 1:
            h1_chunks.append(i)

    if len(h1_chunks) <= 1:
        return chunks  # already fine

    # Demote all chunks after the first H1 chunk by 1 level
    first_h1 = h1_chunks[0]
    result = list(chunks)
    for i in range(len(result)):
        if i == first_h1:
            continue
        levels = [len(m.group(1)) for m in _HEADING_RE.finditer(result[i])]
        if levels and min(levels) == 1:
            result[i] = _shift_headings(result[i], 1)

    return result


def _shift_headings(text: str, shift: int) -> str:
    """Add *shift* extra ``#`` characters to every heading in *text*."""
    def _bump(m: re.Match) -> str:
        hashes = m.group(1)
        new_level = min(len(hashes) + shift, 6)
        return "#" * new_level + " "

    return _HEADING_RE.sub(_bump, text)
