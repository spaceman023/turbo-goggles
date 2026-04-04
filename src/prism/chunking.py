"""Chunking strategies — split pages into processing units."""

from __future__ import annotations

from pathlib import Path

from prism.models import ChunkState, ChunkingConfig, ChunkingStrategy


def build_chunks(
    page_paths: list[Path],
    config: ChunkingConfig,
) -> list[ChunkState]:
    """Create chunk descriptors from page paths and chunking config.

    MVP supports ``page`` strategy only (each page = one chunk).
    """
    total = len(page_paths)

    if config.strategy == ChunkingStrategy.PAGE:
        return [
            ChunkState(
                id=f"chunk-{i + 1:03d}",
                pages=[i + 1],
            )
            for i in range(total)
        ]

    if config.strategy == ChunkingStrategy.SLIDING_WINDOW:
        chunks: list[ChunkState] = []
        step = max(1, config.chunk_size - config.overlap)
        idx = 0
        chunk_num = 1
        while idx < total:
            end = min(idx + config.chunk_size, total)
            pages = list(range(idx + 1, end + 1))
            chunks.append(ChunkState(id=f"chunk-{chunk_num:03d}", pages=pages))
            chunk_num += 1
            idx += step
        return chunks

    if config.strategy == ChunkingStrategy.WHOLE_DOCUMENT:
        return [
            ChunkState(
                id="chunk-001",
                pages=list(range(1, total + 1)),
            )
        ]

    raise ValueError(f"Unsupported chunking strategy: {config.strategy}")
