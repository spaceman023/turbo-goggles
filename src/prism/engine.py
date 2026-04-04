"""Pipeline execution engine — sequential processing with checkpoint/resume."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from prism.backends.registry import get_backend
from prism.chunking import build_chunks
from prism.engines.registry import get_engine
from prism.formatters.plain_text import format_plain_text
from prism.jsonl import emit
from prism.models import (
    ChunkState,
    ChunkStatus,
    CompleteMessage,
    ErrorMessage,
    JobManifest,
    JobStatus,
    LayerCompleteMessage,
    LayerConfig,
    LayerType,
    PipelineConfig,
    ProgressMessage,
)
from prism.rasterize import rasterize_pdf
from prism.templating import default_variables, render_prompt

logger = logging.getLogger(__name__)


class PipelineEngine:
    """Runs a pipeline on a PDF document with checkpoint/resume support."""

    def __init__(self, job_dir: Path, manifest: JobManifest) -> None:
        self.job_dir = job_dir
        self.manifest = manifest
        self.pages_dir = job_dir / "pages"
        self.chunks_dir = job_dir / "chunks"
        self.final_dir = job_dir / "final"

    # ── public API ───────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the full pipeline from current state (supports resume)."""
        self.manifest.status = JobStatus.RUNNING
        self._save_manifest()

        pipeline = self.manifest.pipeline

        for ci, chunk in enumerate(self.manifest.chunks):
            if chunk.status == ChunkStatus.COMPLETE:
                continue

            chunk.status = ChunkStatus.RUNNING
            self._save_manifest()

            try:
                self._run_chunk(ci, chunk, pipeline)
            except Exception as exc:
                chunk.status = ChunkStatus.HALTED
                chunk.error = str(exc)
                self.manifest.status = JobStatus.HALTED
                self._save_manifest()
                emit(
                    ErrorMessage(
                        job_id=self.manifest.job_id,
                        chunk=ci,
                        layer=chunk.halted_at_layer or 0,
                        error=str(exc),
                        halted=True,
                    )
                )
                return

            chunk.status = ChunkStatus.COMPLETE
            self._save_manifest()

        # ── merge final output ───────────────────────────────────────
        output_file = self._merge_output(pipeline)

        self.manifest.status = JobStatus.COMPLETE
        self._save_manifest()

        emit(
            CompleteMessage(
                job_id=self.manifest.job_id,
                output_file=str(output_file.relative_to(self.job_dir)),
            )
        )

    # ── chunk processing ─────────────────────────────────────────────

    def _run_chunk(
        self, chunk_index: int, chunk: ChunkState, pipeline: PipelineConfig
    ) -> None:
        chunk_dir = self.chunks_dir / chunk.id
        chunk_dir.mkdir(parents=True, exist_ok=True)

        # Determine where to start (for resume)
        start_layer = 0
        if chunk.halted_at_layer is not None:
            start_layer = chunk.halted_at_layer
            chunk.halted_at_layer = None
            chunk.error = None

        accumulated_text = ""
        # Reload text from last completed layer if resuming
        if start_layer > 0 and chunk.layers_completed:
            last_done = max(chunk.layers_completed)
            last_layer = pipeline.layers[last_done]
            last_file = self._layer_output_path(chunk_dir, last_done, last_layer)
            if last_file.exists():
                accumulated_text = last_file.read_text(encoding="utf-8")

        for li in range(start_layer, len(pipeline.layers)):
            layer = pipeline.layers[li]

            emit(
                ProgressMessage(
                    job_id=self.manifest.job_id,
                    chunk=chunk_index,
                    layer=li,
                    status="running",
                    message=f"Running {layer.type.value}:{layer.engine or layer.backend or layer.format or layer.id} on {chunk.id}",
                )
            )

            try:
                accumulated_text = self._run_layer(
                    layer, li, chunk, chunk_index, chunk_dir, accumulated_text, pipeline
                )
            except Exception:
                chunk.halted_at_layer = li
                raise

            # Save output
            out_path = self._layer_output_path(chunk_dir, li, layer)
            out_path.write_text(accumulated_text, encoding="utf-8")

            if li not in chunk.layers_completed:
                chunk.layers_completed.append(li)
            self._save_manifest()

            emit(
                LayerCompleteMessage(
                    job_id=self.manifest.job_id,
                    chunk=chunk_index,
                    layer=li,
                    output_file=str(out_path.relative_to(self.job_dir)),
                )
            )

    def _run_layer(
        self,
        layer: LayerConfig,
        layer_index: int,
        chunk: ChunkState,
        chunk_index: int,
        chunk_dir: Path,
        text: str,
        pipeline: PipelineConfig,
    ) -> str:
        if layer.type == LayerType.OCR:
            return self._run_ocr(layer, chunk)

        if layer.type == LayerType.LLM:
            return self._run_llm(layer, chunk, chunk_index, text, pipeline)

        if layer.type == LayerType.FUSION:
            return self._run_fusion(layer, chunk, chunk_index, chunk_dir, pipeline)

        if layer.type == LayerType.OUTPUT:
            # Output layer in per-chunk context is a passthrough — merging happens later
            return text

        if layer.type == LayerType.PREPROCESS:
            # Preprocessing operates on images — MVP doesn't implement image filters
            logger.info("Preprocess layer '%s' is a no-op in MVP.", layer.id)
            return text

        raise ValueError(f"Unknown layer type: {layer.type}")

    # ── layer runners ────────────────────────────────────────────────

    def _run_ocr(self, layer: LayerConfig, chunk: ChunkState) -> str:
        engine = get_engine(layer.engine)
        texts: list[str] = []
        for page_num in chunk.pages:
            img = self.pages_dir / f"page-{page_num:03d}.png"
            if not img.exists():
                raise FileNotFoundError(f"Rasterized page not found: {img}")
            texts.append(engine.run(img, layer.options))
        return "\n\n".join(texts)

    def _run_llm(
        self,
        layer: LayerConfig,
        chunk: ChunkState,
        chunk_index: int,
        text: str,
        pipeline: PipelineConfig,
    ) -> str:
        backend = get_backend(
            layer.backend, model=layer.model, options=layer.options
        )

        prompt = text  # fallback: just send the text
        if layer.prompt_file:
            prompt_path = self._resolve_prompt(layer.prompt_file)
            variables = default_variables(
                text=text,
                page_numbers=chunk.pages,
                chunk_index=chunk_index,
                total_chunks=len(self.manifest.chunks),
                filename=Path(self.manifest.input_file).name,
                layer_name=layer.id,
            )
            prompt = render_prompt(prompt_path, variables)

        image_bytes = None
        if layer.include_image and chunk.pages:
            img_path = self.pages_dir / f"page-{chunk.pages[0]:03d}.png"
            if img_path.exists():
                image_bytes = img_path.read_bytes()

        resp = backend.process(prompt, image=image_bytes)
        return resp.text

    def _run_fusion(
        self,
        layer: LayerConfig,
        chunk: ChunkState,
        chunk_index: int,
        chunk_dir: Path,
        pipeline: PipelineConfig,
    ) -> str:
        if not layer.source_layers:
            raise ValueError(f"Fusion layer '{layer.id}' has no source_layers.")

        # Collect source outputs
        sources: list[dict] = []
        for src_id in layer.source_layers:
            src_layer, src_index = self._find_layer(pipeline, src_id)
            src_file = self._layer_output_path(chunk_dir, src_index, src_layer)
            if not src_file.exists():
                raise FileNotFoundError(
                    f"Source layer output not found: {src_file} (has layer '{src_id}' completed?)"
                )
            sources.append(
                {
                    "engine": src_layer.engine or src_layer.backend or src_id,
                    "text": src_file.read_text(encoding="utf-8"),
                }
            )

        backend = get_backend(
            layer.backend, model=layer.model, options=layer.options
        )

        # Build prompt
        prompt_text = "\n\n".join(
            f"=== Output from {s['engine']} ===\n{s['text']}" for s in sources
        )
        if layer.prompt_file:
            prompt_path = self._resolve_prompt(layer.prompt_file)
            variables = default_variables(
                text=prompt_text,
                page_numbers=chunk.pages,
                chunk_index=chunk_index,
                total_chunks=len(self.manifest.chunks),
                filename=Path(self.manifest.input_file).name,
                layer_name=layer.id,
                sources=sources,
            )
            prompt_text = render_prompt(prompt_path, variables)

        image_bytes = None
        if layer.include_image and chunk.pages:
            img_path = self.pages_dir / f"page-{chunk.pages[0]:03d}.png"
            if img_path.exists():
                image_bytes = img_path.read_bytes()

        resp = backend.process(prompt_text, image=image_bytes)
        return resp.text

    # ── merge final output ───────────────────────────────────────────

    def _merge_output(self, pipeline: PipelineConfig) -> Path:
        """Merge all chunk outputs from the last layer into a single file."""
        last_layer_index = len(pipeline.layers) - 1
        last_layer = pipeline.layers[last_layer_index]

        chunk_texts: list[str] = []
        for chunk in self.manifest.chunks:
            chunk_dir = self.chunks_dir / chunk.id
            out_file = self._layer_output_path(chunk_dir, last_layer_index, last_layer)
            if out_file.exists():
                chunk_texts.append(out_file.read_text(encoding="utf-8"))
            else:
                chunk_texts.append("")

        # Determine output format
        fmt = "txt"
        output_options: dict = {}
        if last_layer.type == LayerType.OUTPUT:
            fmt = last_layer.format or "txt"
            output_options = last_layer.options

        output_path = self.final_dir / f"merged-output.{fmt}"
        self.final_dir.mkdir(parents=True, exist_ok=True)

        # MVP: plain text only
        format_plain_text(chunk_texts, output_path, output_options)
        return output_path

    # ── helpers ──────────────────────────────────────────────────────

    def _layer_output_path(
        self, chunk_dir: Path, layer_index: int, layer: LayerConfig
    ) -> Path:
        engine_or_id = layer.engine or layer.backend or layer.format or layer.id
        return chunk_dir / f"layer-{layer_index}.{engine_or_id}.txt"

    def _find_layer(
        self, pipeline: PipelineConfig, layer_id: str
    ) -> tuple[LayerConfig, int]:
        for i, l in enumerate(pipeline.layers):
            if l.id == layer_id:
                return l, i
        raise ValueError(f"Layer '{layer_id}' not found in pipeline.")

    def _resolve_prompt(self, prompt_file: str) -> Path:
        """Resolve a prompt file path — try relative to job dir, then ~/.prism/."""
        p = Path(prompt_file)
        if p.is_absolute() and p.exists():
            return p
        # Relative to job dir
        candidate = self.job_dir / prompt_file
        if candidate.exists():
            return candidate
        # Relative to ~/.prism/
        candidate = Path.home() / ".prism" / prompt_file
        if candidate.exists():
            return candidate
        # Relative to cwd
        candidate = Path.cwd() / prompt_file
        if candidate.exists():
            return candidate
        raise FileNotFoundError(
            f"Prompt file '{prompt_file}' not found in job dir, ~/.prism/, or cwd."
        )

    def _save_manifest(self) -> None:
        self.manifest.updated_at = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        manifest_path = self.job_dir / "manifest.json"
        manifest_path.write_text(
            self.manifest.model_dump_json(indent=2), encoding="utf-8"
        )


# ── Top-level helpers ────────────────────────────────────────────────────────


def create_job(
    pdf_path: Path,
    pipeline_config: PipelineConfig,
    output_dir: Path | None = None,
) -> tuple[Path, JobManifest]:
    """Set up a new job directory, rasterize the PDF, build chunks, return (job_dir, manifest)."""
    import shutil

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    job_id = f"job-{timestamp}"

    base = output_dir or (Path.home() / ".prism" / "jobs")
    job_dir = base / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Copy input PDF
    input_dir = job_dir / "input"
    input_dir.mkdir(exist_ok=True)
    dest_pdf = input_dir / pdf_path.name
    shutil.copy2(pdf_path, dest_pdf)

    # Rasterize
    pages_dir = job_dir / "pages"
    page_paths = rasterize_pdf(pdf_path, pages_dir)

    # Build chunks
    chunks = build_chunks(page_paths, pipeline_config.chunking)

    manifest = JobManifest(
        job_id=job_id,
        input_file=f"input/{pdf_path.name}",
        total_pages=len(page_paths),
        pipeline=pipeline_config,
        chunking=pipeline_config.chunking,
        chunks=chunks,
    )

    # Save manifest
    (job_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    return job_dir, manifest


def load_job(job_dir: Path) -> tuple[Path, JobManifest]:
    """Load an existing job from its directory."""
    manifest_path = job_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json found in {job_dir}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = JobManifest.model_validate(data)
    return job_dir, manifest
