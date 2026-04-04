"""Plain text output formatter."""

from __future__ import annotations

from pathlib import Path


def format_plain_text(
    chunk_texts: list[str],
    output_path: Path,
    options: dict | None = None,
) -> Path:
    """Merge chunk texts into a single plain-text file.

    *chunk_texts* is ordered by chunk index. Chunks are joined by double
    newlines.  If ``preserve_page_breaks`` is set, a form-feed character
    is inserted between chunks instead.
    """
    options = options or {}
    separator = "\f\n" if options.get("preserve_page_breaks") else "\n\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged = separator.join(t.strip() for t in chunk_texts if t.strip())
    output_path.write_text(merged, encoding="utf-8")
    return output_path
