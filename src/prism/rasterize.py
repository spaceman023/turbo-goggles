"""PDF rasterization — convert PDF pages to PNG images at 300 DPI."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pdf2image import convert_from_path

logger = logging.getLogger(__name__)

DPI = 300


def rasterize_pdf(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Rasterize every page of *pdf_path* to PNG files in *output_dir*.

    Returns a sorted list of output image paths (page-001.png, page-002.png, …).
    """
    logger.debug("rasterize_pdf called: pdf_path=%s, output_dir=%s", pdf_path, output_dir)

    if not pdf_path.exists():
        logger.error("PDF file does not exist: %s", pdf_path)
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    pdf_size = pdf_path.stat().st_size
    logger.info(
        "Rasterizing %s (%.2f MB) at %d DPI → %s",
        pdf_path.name,
        pdf_size / (1024 * 1024),
        DPI,
        output_dir,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("Output directory ensured: %s", output_dir)

    logger.debug("Calling pdf2image.convert_from_path …")
    images = convert_from_path(str(pdf_path), dpi=DPI)
    logger.debug("pdf2image returned %d page image(s)", len(images))

    paths: list[Path] = []
    for i, img in enumerate(images, start=1):
        out = output_dir / f"page-{i:03d}.png"
        img.save(str(out), "PNG")
        file_size = out.stat().st_size
        logger.debug(
            "  page %d/%d → %s (%dx%d, %.1f KB)",
            i,
            len(images),
            out.name,
            img.width,
            img.height,
            file_size / 1024,
        )
        paths.append(out)

    total_size = sum(p.stat().st_size for p in paths)
    logger.info(
        "Rasterized %d pages (total %.2f MB on disk)",
        len(paths),
        total_size / (1024 * 1024),
    )
    return paths
