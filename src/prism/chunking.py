"""Chunking strategies — split pages into processing units."""

from __future__ import annotations

import logging
from pathlib import Path

from prism.models import ChunkState, ChunkingConfig, ChunkingStrategy

logger = logging.getLogger(__name__)


def build_chunks(
    page_paths: list[Path],
    config: ChunkingConfig,
) -> list[ChunkState]:
    """Create chunk descriptors from page paths and chunking config."""
    total = len(page_paths)
    logger.debug(
        "build_chunks: total_pages=%d, strategy=%s, chunk_size=%d, overlap=%d",
        total,
        config.strategy.value,
        config.chunk_size,
        config.overlap,
    )

    if config.strategy == ChunkingStrategy.PAGE:
        chunks = [
            ChunkState(id=f"chunk-{i + 1:03d}", pages=[i + 1])
            for i in range(total)
        ]
        logger.info("Page chunking: %d chunks (1 page each)", len(chunks))
        for c in chunks:
            logger.debug("  %s → pages %s", c.id, c.pages)
        return chunks

    if config.strategy == ChunkingStrategy.SLIDING_WINDOW:
        chunks: list[ChunkState] = []
        step = max(1, config.chunk_size - config.overlap)
        logger.debug("Sliding window: step=%d (chunk_size=%d - overlap=%d)", step, config.chunk_size, config.overlap)
        idx = 0
        chunk_num = 1
        while idx < total:
            end = min(idx + config.chunk_size, total)
            pages = list(range(idx + 1, end + 1))
            chunks.append(ChunkState(id=f"chunk-{chunk_num:03d}", pages=pages))
            logger.debug("  chunk-%03d → pages %s", chunk_num, pages)
            chunk_num += 1
            idx += step
        logger.info("Sliding window chunking: %d chunks, %d pages overlap", len(chunks), config.overlap)
        return chunks

    if config.strategy == ChunkingStrategy.WHOLE_DOCUMENT:
        pages = list(range(1, total + 1))
        chunks = [ChunkState(id="chunk-001", pages=pages)]
        logger.info("Whole document chunking: 1 chunk with %d pages", total)
        return chunks

    logger.error("Unsupported chunking strategy: %s", config.strategy)
    raise ValueError(f"Unsupported chunking strategy: {config.strategy}")
