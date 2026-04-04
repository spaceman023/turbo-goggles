"""Pipeline execution engine — sequential processing with checkpoint/resume."""

from __future__ import annotations

import datetime
import json
import logging
import time
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
        logger.debug(
            "PipelineEngine init: job_id=%s, job_dir=%s, pages_dir=%s",
            manifest.job_id,
            job_dir,
            self.pages_dir,
        )
        logger.debug(
            "  Pipeline: %s (%d layers), chunks: %d, pages: %d",
            manifest.pipeline.name,
            len(manifest.pipeline.layers),
            len(manifest.chunks),
            manifest.total_pages,
        )
        for i, layer in enumerate(manifest.pipeline.layers):
            logger.debug(
                "  Layer[%d]: id=%s, type=%s, engine=%s, backend=%s",
                i,
                layer.id,
                layer.type.value,
                layer.engine,
                layer.backend,
            )

    # ── public API ───────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the full pipeline from current state (supports resume)."""
        logger.info("Pipeline run starting: job_id=%s, status=%s", self.manifest.job_id, self.manifest.status.value)
        job_t0 = time.monotonic()

        self.manifest.status = JobStatus.RUNNING
        self._save_manifest()

        pipeline = self.manifest.pipeline

        completed_before = sum(1 for c in self.manifest.chunks if c.status == ChunkStatus.COMPLETE)
        logger.debug("Chunks already complete: %d/%d", completed_before, len(self.manifest.chunks))

        for ci, chunk in enumerate(self.manifest.chunks):
            if chunk.status == ChunkStatus.COMPLETE:
                logger.debug("Skipping chunk %d/%d (%s) — already complete", ci + 1, len(self.manifest.chunks), chunk.id)
                continue

            logger.info(
                "Processing chunk %d/%d (%s): pages=%s",
                ci + 1,
                len(self.manifest.chunks),
                chunk.id,
                chunk.pages,
            )
            chunk_t0 = time.monotonic()

            chunk.status = ChunkStatus.RUNNING
            self._save_manifest()

            try:
                self._run_chunk(ci, chunk, pipeline)
            except Exception as exc:
                chunk.status = ChunkStatus.HALTED
                chunk.error = str(exc)
                self.manifest.status = JobStatus.HALTED
                self._save_manifest()
                logger.error(
                    "Chunk %s halted with error at layer %s: %s",
                    chunk.id,
                    chunk.halted_at_layer,
                    exc,
                    exc_info=True,
                )
                emit(
                    ErrorMessage(
                        job_id=self.manifest.job_id,
                        chunk=ci,
                        layer=chunk.halted_at_layer or 0,
                        error=str(exc),
                        halted=True,
                    )
                )
                job_elapsed = int((time.monotonic() - job_t0) * 1000)
                logger.info("Pipeline halted after %dms", job_elapsed)
                return

            chunk.status = ChunkStatus.COMPLETE
            self._save_manifest()
            chunk_elapsed = int((time.monotonic() - chunk_t0) * 1000)
            logger.info("Chunk %s complete in %dms", chunk.id, chunk_elapsed)

        # ── merge final output ───────────────────────────────────────
        logger.info("All chunks complete. Merging final output …")
        merge_t0 = time.monotonic()
        output_file = self._merge_output(pipeline)
        merge_elapsed = int((time.monotonic() - merge_t0) * 1000)
        logger.info("Final output merged in %dms: %s", merge_elapsed, output_file)

        self.manifest.status = JobStatus.COMPLETE
        self._save_manifest()

        job_elapsed = int((time.monotonic() - job_t0) * 1000)
        logger.info(
            "Pipeline complete: job_id=%s, total_elapsed=%dms, chunks=%d",
            self.manifest.job_id,
            job_elapsed,
            len(self.manifest.chunks),
        )

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
        logger.debug("Chunk dir: %s", chunk_dir)

        # Determine where to start (for resume)
        start_layer = 0
        if chunk.halted_at_layer is not None:
            start_layer = chunk.halted_at_layer
            logger.info(
                "Resuming %s from layer %d (was halted: %s)",
                chunk.id,
                start_layer,
                chunk.error,
            )
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
                logger.debug(
                    "Loaded accumulated text from layer %d (%s): %d chars",
                    last_done,
                    last_file.name,
                    len(accumulated_text),
                )
            else:
                logger.warning(
                    "Expected output file for layer %d not found: %s",
                    last_done,
                    last_file,
                )

        total_layers = len(pipeline.layers)
        for li in range(start_layer, total_layers):
            layer = pipeline.layers[li]
            layer_desc = f"{layer.type.value}:{layer.engine or layer.backend or layer.format or layer.id}"

            logger.info(
                "  Layer %d/%d (%s) on %s — starting",
                li + 1,
                total_layers,
                layer_desc,
                chunk.id,
            )
            layer_t0 = time.monotonic()

            emit(
                ProgressMessage(
                    job_id=self.manifest.job_id,
                    chunk=chunk_index,
                    layer=li,
                    status="running",
                    message=f"Running {layer_desc} on {chunk.id}",
                )
            )

            try:
                accumulated_text = self._run_layer(
                    layer, li, chunk, chunk_index, chunk_dir, accumulated_text, pipeline
                )
            except Exception:
                chunk.halted_at_layer = li
                logger.error("  Layer %d (%s) failed on %s", li, layer_desc, chunk.id, exc_info=True)
                raise

            layer_elapsed = int((time.monotonic() - layer_t0) * 1000)

            # Save output
            out_path = self._layer_output_path(chunk_dir, li, layer)
            out_path.write_text(accumulated_text, encoding="utf-8")
            logger.debug(
                "  Layer %d output saved: %s (%d chars, %.1f KB)",
                li,
                out_path.name,
                len(accumulated_text),
                len(accumulated_text.encode("utf-8")) / 1024,
            )

            if li not in chunk.layers_completed:
                chunk.layers_completed.append(li)
            self._save_manifest()

            logger.info(
                "  Layer %d/%d (%s) on %s — done in %dms, output=%d chars",
                li + 1,
                total_layers,
                layer_desc,
                chunk.id,
                layer_elapsed,
                len(accumulated_text),
            )

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
        logger.debug(
            "  _run_layer: type=%s, id=%s, input_text_len=%d",
            layer.type.value,
            layer.id,
            len(text),
        )

        if layer.type == LayerType.OCR:
            return self._run_ocr(layer, chunk)

        if layer.type == LayerType.LLM:
            return self._run_llm(layer, chunk, chunk_index, text, pipeline)

        if layer.type == LayerType.FUSION:
            return self._run_fusion(layer, chunk, chunk_index, chunk_dir, pipeline)

        if layer.type == LayerType.OUTPUT:
            logger.debug("  Output layer — passthrough (merging happens at end)")
            return text

        if layer.type == LayerType.PREPROCESS:
            logger.info("  Preprocess layer '%s' is a no-op in MVP", layer.id)
            return text

        logger.error("  Unknown layer type: %s", layer.type)
        raise ValueError(f"Unknown layer type: {layer.type}")

    # ── layer runners ────────────────────────────────────────────────

    def _run_ocr(self, layer: LayerConfig, chunk: ChunkState) -> str:
        logger.debug("  _run_ocr: engine=%s, pages=%s", layer.engine, chunk.pages)
        engine = get_engine(layer.engine)
        texts: list[str] = []
        for page_num in chunk.pages:
            img = self.pages_dir / f"page-{page_num:03d}.png"
            if not img.exists():
                logger.error("  Rasterized page not found: %s", img)
                raise FileNotFoundError(f"Rasterized page not found: {img}")
            logger.debug("  OCR page %d: %s (%.1f KB)", page_num, img.name, img.stat().st_size / 1024)
            page_text = engine.run(img, layer.options)
            logger.debug("  OCR page %d result: %d chars, %d words", page_num, len(page_text), len(page_text.split()))
            texts.append(page_text)

        combined = "\n\n".join(texts)
        logger.debug("  OCR combined: %d pages → %d chars total", len(texts), len(combined))
        return combined

    def _run_llm(
        self,
        layer: LayerConfig,
        chunk: ChunkState,
        chunk_index: int,
        text: str,
        pipeline: PipelineConfig,
    ) -> str:
        logger.debug(
            "  _run_llm: backend=%s, model=%s, prompt_file=%s, include_image=%s, input_text_len=%d",
            layer.backend,
            layer.model,
            layer.prompt_file,
            layer.include_image,
            len(text),
        )
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
            logger.debug("  Rendered prompt: %d chars (from template %s)", len(prompt), prompt_path.name)
        else:
            logger.debug("  No prompt_file — sending raw text (%d chars) as prompt", len(text))

        image_bytes = None
        if layer.include_image and chunk.pages:
            img_path = self.pages_dir / f"page-{chunk.pages[0]:03d}.png"
            if img_path.exists():
                image_bytes = img_path.read_bytes()
                logger.debug("  Attaching image: %s (%.1f KB)", img_path.name, len(image_bytes) / 1024)
            else:
                logger.warning("  include_image=True but image not found: %s", img_path)

        logger.debug("  Sending to LLM backend …")
        t0 = time.monotonic()
        resp = backend.process(prompt, image=image_bytes)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        logger.debug(
            "  LLM response: %d chars, %d words, tokens_in=%s, tokens_out=%s, elapsed=%dms",
            len(resp.text),
            len(resp.text.split()),
            resp.tokens_in,
            resp.tokens_out,
            elapsed_ms,
        )
        return resp.text

    def _run_fusion(
        self,
        layer: LayerConfig,
        chunk: ChunkState,
        chunk_index: int,
        chunk_dir: Path,
        pipeline: PipelineConfig,
    ) -> str:
        logger.debug(
            "  _run_fusion: id=%s, source_layers=%s, backend=%s, model=%s",
            layer.id,
            layer.source_layers,
            layer.backend,
            layer.model,
        )

        if not layer.source_layers:
            logger.error("  Fusion layer '%s' has no source_layers", layer.id)
            raise ValueError(f"Fusion layer '{layer.id}' has no source_layers.")

        # Collect source outputs
        sources: list[dict] = []
        for src_id in layer.source_layers:
            src_layer, src_index = self._find_layer(pipeline, src_id)
            src_file = self._layer_output_path(chunk_dir, src_index, src_layer)
            if not src_file.exists():
                logger.error(
                    "  Source layer output not found: %s (layer '%s' may not have completed)",
                    src_file,
                    src_id,
                )
                raise FileNotFoundError(
                    f"Source layer output not found: {src_file} (has layer '{src_id}' completed?)"
                )
            src_text = src_file.read_text(encoding="utf-8")
            logger.debug(
                "  Fusion source '%s': %d chars, %d words (from %s)",
                src_id,
                len(src_text),
                len(src_text.split()),
                src_file.name,
            )
            sources.append(
                {
                    "engine": src_layer.engine or src_layer.backend or src_id,
                    "text": src_text,
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
            logger.debug("  Fusion prompt rendered: %d chars (from %s)", len(prompt_text), prompt_path.name)
        else:
            logger.debug("  Fusion prompt (raw concat): %d chars", len(prompt_text))

        image_bytes = None
        if layer.include_image and chunk.pages:
            img_path = self.pages_dir / f"page-{chunk.pages[0]:03d}.png"
            if img_path.exists():
                image_bytes = img_path.read_bytes()
                logger.debug("  Fusion attaching image: %s (%.1f KB)", img_path.name, len(image_bytes) / 1024)
            else:
                logger.warning("  include_image=True but image not found: %s", img_path)

        logger.debug("  Sending fusion prompt to LLM backend …")
        t0 = time.monotonic()
        resp = backend.process(prompt_text, image=image_bytes)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        logger.debug(
            "  Fusion response: %d chars, %d words, tokens_in=%s, tokens_out=%s, elapsed=%dms",
            len(resp.text),
            len(resp.text.split()),
            resp.tokens_in,
            resp.tokens_out,
            elapsed_ms,
        )
        return resp.text

    # ── merge final output ───────────────────────────────────────────

    def _merge_output(self, pipeline: PipelineConfig) -> Path:
        """Merge all chunk outputs from the last layer into a single file."""
        last_layer_index = len(pipeline.layers) - 1
        last_layer = pipeline.layers[last_layer_index]
        logger.debug(
            "Merging output: last_layer[%d]=%s (type=%s, format=%s)",
            last_layer_index,
            last_layer.id,
            last_layer.type.value,
            last_layer.format,
        )

        chunk_texts: list[str] = []
        for chunk in self.manifest.chunks:
            chunk_dir = self.chunks_dir / chunk.id
            out_file = self._layer_output_path(chunk_dir, last_layer_index, last_layer)
            if out_file.exists():
                text = out_file.read_text(encoding="utf-8")
                chunk_texts.append(text)
                logger.debug("  %s: %d chars from %s", chunk.id, len(text), out_file.name)
            else:
                chunk_texts.append("")
                logger.warning("  %s: output file missing — %s", chunk.id, out_file)

        # Determine output format
        fmt = "txt"
        output_options: dict = {}
        if last_layer.type == LayerType.OUTPUT:
            fmt = last_layer.format or "txt"
            output_options = last_layer.options

        output_path = self.final_dir / f"merged-output.{fmt}"
        self.final_dir.mkdir(parents=True, exist_ok=True)

        logger.debug("  Output format: %s, options: %s", fmt, output_options)

        # MVP: plain text only
        format_plain_text(chunk_texts, output_path, output_options)

        output_size = output_path.stat().st_size
        logger.info(
            "Merged output: %s (%.2f KB, %d chunks merged)",
            output_path,
            output_size / 1024,
            len(chunk_texts),
        )
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
        logger.error("Layer '%s' not found in pipeline (available: %s)", layer_id, [l.id for l in pipeline.layers])
        raise ValueError(f"Layer '{layer_id}' not found in pipeline.")

    def _resolve_prompt(self, prompt_file: str) -> Path:
        """Resolve a prompt file path — try relative to job dir, then ~/.prism/."""
        logger.debug("Resolving prompt file: %s", prompt_file)
        p = Path(prompt_file)
        if p.is_absolute() and p.exists():
            logger.debug("  Found (absolute): %s", p)
            return p
        # Relative to job dir
        candidate = self.job_dir / prompt_file
        if candidate.exists():
            logger.debug("  Found (job dir): %s", candidate)
            return candidate
        # Relative to ~/.prism/
        candidate = Path.home() / ".prism" / prompt_file
        if candidate.exists():
            logger.debug("  Found (~/.prism/): %s", candidate)
            return candidate
        # Relative to cwd
        candidate = Path.cwd() / prompt_file
        if candidate.exists():
            logger.debug("  Found (cwd): %s", candidate)
            return candidate
        logger.error(
            "Prompt file '%s' not found. Searched: job_dir=%s, ~/.prism/, cwd=%s",
            prompt_file,
            self.job_dir,
            Path.cwd(),
        )
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
        logger.debug("Manifest saved: %s (status=%s)", manifest_path, self.manifest.status.value)


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

    logger.info("Creating job %s in %s", job_id, job_dir)
    logger.debug(
        "  pdf_path=%s, pipeline=%s, output_dir=%s",
        pdf_path,
        pipeline_config.name,
        output_dir,
    )

    # Copy input PDF
    input_dir = job_dir / "input"
    input_dir.mkdir(exist_ok=True)
    dest_pdf = input_dir / pdf_path.name
    shutil.copy2(pdf_path, dest_pdf)
    logger.debug("  Copied PDF to %s", dest_pdf)

    # Rasterize
    pages_dir = job_dir / "pages"
    logger.debug("  Rasterizing into %s …", pages_dir)
    page_paths = rasterize_pdf(pdf_path, pages_dir)

    # Build chunks
    logger.debug(
        "  Building chunks: strategy=%s, chunk_size=%d, overlap=%d",
        pipeline_config.chunking.strategy.value,
        pipeline_config.chunking.chunk_size,
        pipeline_config.chunking.overlap,
    )
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
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    logger.info(
        "Job created: id=%s, pages=%d, chunks=%d, manifest=%s",
        job_id,
        len(page_paths),
        len(chunks),
        manifest_path,
    )

    return job_dir, manifest


def load_job(job_dir: Path) -> tuple[Path, JobManifest]:
    """Load an existing job from its directory."""
    manifest_path = job_dir / "manifest.json"
    logger.debug("Loading job from %s", manifest_path)

    if not manifest_path.exists():
        logger.error("No manifest.json found in %s", job_dir)
        raise FileNotFoundError(f"No manifest.json found in {job_dir}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = JobManifest.model_validate(data)

    completed = sum(1 for c in manifest.chunks if c.status.value == "complete")
    halted = [c for c in manifest.chunks if c.error]
    logger.info(
        "Job loaded: id=%s, status=%s, pages=%d, chunks=%d (%d complete, %d halted)",
        manifest.job_id,
        manifest.status.value,
        manifest.total_pages,
        len(manifest.chunks),
        completed,
        len(halted),
    )
    for h in halted:
        logger.debug("  Halted chunk %s at layer %s: %s", h.id, h.halted_at_layer, h.error)

    return job_dir, manifest
