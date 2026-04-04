"""PDF rasterization — convert PDF pages to PNG images at 300 DPI."""

from __future__ import annotations

import logging
from pathlib import Path

from pdf2image import convert_from_path

logger = logging.getLogger(__name__)

DPI = 300


def rasterize_pdf(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Rasterize every page of *pdf_path* to PNG files in *output_dir*.

    Returns a sorted list of output image paths (page-001.png, page-002.png, …).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Rasterizing %s at %d DPI …", pdf_path.name, DPI)
    images = convert_from_path(str(pdf_path), dpi=DPI)

    paths: list[Path] = []
    for i, img in enumerate(images, start=1):
        out = output_dir / f"page-{i:03d}.png"
        img.save(str(out), "PNG")
        paths.append(out)
        logger.debug("  → %s", out.name)

    logger.info("Rasterized %d pages.", len(paths))
    return paths
