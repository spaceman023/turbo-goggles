"""Plain text output formatter."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
    use_page_breaks = options.get("preserve_page_breaks", False)
    separator = "\f\n" if use_page_breaks else "\n\n"

    non_empty = [t.strip() for t in chunk_texts if t.strip()]
    empty_count = len(chunk_texts) - len(non_empty)

    logger.debug(
        "format_plain_text: %d chunks (%d non-empty, %d empty/blank), "
        "preserve_page_breaks=%s, output=%s",
        len(chunk_texts),
        len(non_empty),
        empty_count,
        use_page_breaks,
        output_path,
    )

    if empty_count > 0:
        logger.warning(
            "%d of %d chunks were empty/whitespace-only and will be skipped",
            empty_count,
            len(chunk_texts),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged = separator.join(non_empty)
    output_path.write_text(merged, encoding="utf-8")

    logger.debug(
        "  Wrote %d chars (%.1f KB) to %s",
        len(merged),
        len(merged.encode("utf-8")) / 1024,
        output_path.name,
    )

    return output_path
