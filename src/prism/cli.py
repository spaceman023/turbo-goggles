"""PRISM CLI — command-line interface for the pipeline engine."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from prism.models import JobStatus, PipelineConfig

logger = logging.getLogger(__name__)

console = Console(stderr=True)  # Rich output goes to stderr; JSONL goes to stdout


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Quiet noisy third-party loggers even in verbose mode
    if verbose:
        for noisy in ("urllib3", "PIL", "paddleocr", "ppocr"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


@click.group()
@click.version_option(package_name="prism-ocr")
def main() -> None:
    """PRISM — Pipeline for Recognition, Interpretation, and Structured Markup."""


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, path_type=Path), help="Pipeline config JSON file.")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, path_type=Path), help="Input PDF file.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None, help="Output directory (default: ~/.prism/jobs/).")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def run(config_path: Path, input_path: Path, output_dir: Path | None, verbose: bool) -> None:
    """Run a pipeline on a PDF document."""
    _setup_logging(verbose)

    from prism.engine import PipelineEngine, create_job

    logger.debug("CLI run: config=%s, input=%s, output_dir=%s", config_path, input_path, output_dir)

    # Load pipeline config
    raw_text = config_path.read_text(encoding="utf-8")
    logger.debug("Loaded config file: %d bytes", len(raw_text))
    raw = json.loads(raw_text)
    pipeline = PipelineConfig.model_validate(raw)
    logger.debug("Pipeline validated: name=%s, layers=%d", pipeline.name, len(pipeline.layers))

    console.print(f"[bold]PRISM[/bold] — {pipeline.name}")
    console.print(f"  Input:  {input_path}")
    console.print(f"  Layers: {len(pipeline.layers)}")
    console.print()

    # Create job
    with console.status("Rasterizing PDF…"):
        job_dir, manifest = create_job(input_path, pipeline, output_dir)

    console.print(f"  Job:    {job_dir}")
    console.print(f"  Pages:  {manifest.total_pages}")
    console.print(f"  Chunks: {len(manifest.chunks)}")
    console.print()

    # Run pipeline
    logger.info("Starting pipeline execution …")
    engine = PipelineEngine(job_dir, manifest)
    engine.run()

    if manifest.status == JobStatus.COMPLETE:
        console.print(f"\n[bold green]✓ Complete![/bold green] Output: {job_dir / 'final'}")
    elif manifest.status == JobStatus.HALTED:
        console.print(f"\n[bold yellow]⏸ Halted.[/bold yellow] Resume with: prism resume --job {job_dir}")
    else:
        console.print(f"\n[bold red]✗ Failed.[/bold red] Status: {manifest.status.value}")


@main.command()
@click.option("--job", "job_path", required=True, type=click.Path(exists=True, path_type=Path), help="Path to job directory.")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def resume(job_path: Path, verbose: bool) -> None:
    """Resume a halted job."""
    _setup_logging(verbose)

    from prism.engine import PipelineEngine, load_job

    logger.debug("CLI resume: job=%s", job_path)

    job_dir, manifest = load_job(job_path)

    if manifest.status == JobStatus.COMPLETE:
        console.print("[green]Job is already complete.[/green]")
        logger.info("Job %s is already complete — nothing to do", manifest.job_id)
        return

    if manifest.status not in (JobStatus.HALTED, JobStatus.FAILED):
        console.print(f"[yellow]Job status is '{manifest.status.value}' — attempting resume anyway.[/yellow]")
        logger.warning("Job %s has status '%s' (expected halted/failed), resuming anyway", manifest.job_id, manifest.status.value)

    completed = sum(1 for c in manifest.chunks if c.status.value == "complete")
    console.print(f"[bold]PRISM[/bold] — Resuming {manifest.job_id}")
    console.print(f"  Chunks completed: {completed}/{len(manifest.chunks)}")
    console.print()

    logger.info("Resuming pipeline execution for %s …", manifest.job_id)
    engine = PipelineEngine(job_dir, manifest)
    engine.run()

    if manifest.status == JobStatus.COMPLETE:
        console.print(f"\n[bold green]✓ Complete![/bold green] Output: {job_dir / 'final'}")
    elif manifest.status == JobStatus.HALTED:
        console.print(f"\n[bold yellow]⏸ Halted again.[/bold yellow] Resume with: prism resume --job {job_dir}")


@main.command()
@click.option("--job", "job_path", required=True, type=click.Path(exists=True, path_type=Path), help="Path to job directory.")
def status(job_path: Path) -> None:
    """Check the status of a job."""
    from prism.engine import load_job

    _, manifest = load_job(job_path)

    table = Table(title=f"Job: {manifest.job_id}")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Status", manifest.status.value)
    table.add_row("Input", manifest.input_file)
    table.add_row("Pages", str(manifest.total_pages))
    table.add_row("Chunks", str(len(manifest.chunks)))

    completed = sum(1 for c in manifest.chunks if c.status.value == "complete")
    table.add_row("Completed", f"{completed}/{len(manifest.chunks)}")
    table.add_row("Created", manifest.created_at)
    table.add_row("Updated", manifest.updated_at)

    # Show per-chunk detail
    for chunk in manifest.chunks:
        if chunk.error:
            table.add_row(f"Error ({chunk.id})", chunk.error)

    console.print(table)


@main.command()
def engines() -> None:
    """List available OCR engines."""
    from prism.engines.registry import list_engines

    table = Table(title="Available OCR Engines")
    table.add_column("Engine", style="bold")
    table.add_column("Status")

    for name in list_engines():
        try:
            from prism.engines.registry import get_engine
            get_engine(name)
            status = "[green]available[/green]"
        except Exception as e:
            status = f"[red]unavailable[/red] ({e})"
        table.add_row(name, status)

    console.print(table)


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, path_type=Path), help="Pipeline config JSON file.")
def validate(config_path: Path) -> None:
    """Validate a pipeline config file."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        pipeline = PipelineConfig.model_validate(raw)
        console.print(f"[bold green]✓ Valid pipeline:[/bold green] {pipeline.name}")
        console.print(f"  Layers: {len(pipeline.layers)}")
        for i, layer in enumerate(pipeline.layers):
            engine_info = layer.engine or layer.backend or layer.format or ""
            console.print(f"    [{i}] {layer.type.value}: {engine_info} (id={layer.id})")
    except Exception as e:
        console.print(f"[bold red]✗ Invalid pipeline:[/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
