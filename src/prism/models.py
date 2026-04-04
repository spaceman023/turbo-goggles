"""Pydantic models for pipeline configuration, job state, and JSONL messages."""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Layer config models ──────────────────────────────────────────────────────


class LayerType(str, Enum):
    PREPROCESS = "preprocess"
    OCR = "ocr"
    LLM = "llm"
    FUSION = "fusion"
    OUTPUT = "output"


class LayerConfig(BaseModel):
    id: str
    type: LayerType
    engine: str | None = None
    backend: str | None = None
    model: str | None = None
    prompt_file: str | None = None
    include_image: bool = False
    source_layers: list[str] | None = None
    format: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


# ── Chunking config ──────────────────────────────────────────────────────────


class ChunkingStrategy(str, Enum):
    PAGE = "page"
    SLIDING_WINDOW = "sliding_window"
    SEMANTIC = "semantic"
    WHOLE_DOCUMENT = "whole_document"


class MergeStrategy(str, Enum):
    PREFER_LATER = "prefer_later"
    PREFER_EARLIER = "prefer_earlier"
    MIDDLE_PRIORITY = "middle_priority"


class ChunkingConfig(BaseModel):
    strategy: ChunkingStrategy = ChunkingStrategy.PAGE
    chunk_size: int = 1
    overlap: int = 0
    max_tokens: int = 8000
    merge_strategy: MergeStrategy = MergeStrategy.PREFER_LATER


# ── Pipeline config ──────────────────────────────────────────────────────────


class PipelineConfig(BaseModel):
    name: str = "Untitled Pipeline"
    description: str = ""
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    layers: list[LayerConfig]


# ── Job / manifest models ────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    HALTED = "halted"
    COMPLETE = "complete"
    FAILED = "failed"


class ChunkStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    HALTED = "halted"
    COMPLETE = "complete"


class ChunkState(BaseModel):
    id: str
    pages: list[int]
    layers_completed: list[int] = Field(default_factory=list)
    status: ChunkStatus = ChunkStatus.PENDING
    error: str | None = None
    halted_at_layer: int | None = None


class JobManifest(BaseModel):
    job_id: str
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    status: JobStatus = JobStatus.PENDING
    input_file: str
    total_pages: int = 0
    pipeline: PipelineConfig
    chunking: ChunkingConfig
    chunks: list[ChunkState] = Field(default_factory=list)


# ── JSONL message types (stdout communication) ──────────────────────────────


class MessageType(str, Enum):
    PROGRESS = "progress"
    LAYER_COMPLETE = "layer_complete"
    ERROR = "error"
    COMPLETE = "complete"


class ProgressMessage(BaseModel):
    type: MessageType = MessageType.PROGRESS
    job_id: str
    chunk: int
    layer: int
    status: str
    message: str


class LayerCompleteMessage(BaseModel):
    type: MessageType = MessageType.LAYER_COMPLETE
    job_id: str
    chunk: int
    layer: int
    output_file: str


class ErrorMessage(BaseModel):
    type: MessageType = MessageType.ERROR
    job_id: str
    chunk: int
    layer: int
    error: str
    halted: bool = True


class CompleteMessage(BaseModel):
    type: MessageType = MessageType.COMPLETE
    job_id: str
    output_file: str
