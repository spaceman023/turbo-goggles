"""Registry of available OCR engines."""

from __future__ import annotations

import logging

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)

_ENGINES: dict[str, type[OCREngine]] = {}


def _register_builtins() -> None:
    from prism.engines.tesseract import TesseractEngine
    from prism.engines.paddle import PaddleEngine

    _ENGINES["tesseract"] = TesseractEngine
    _ENGINES["paddleocr"] = PaddleEngine
    logger.debug("Registered built-in OCR engines: %s", list(_ENGINES.keys()))


def get_engine(name: str) -> OCREngine:
    """Return an instance of the named OCR engine."""
    if not _ENGINES:
        _register_builtins()
    cls = _ENGINES.get(name)
    if cls is None:
        logger.error("Unknown OCR engine '%s'. Available: %s", name, list(_ENGINES.keys()))
        raise ValueError(
            f"Unknown OCR engine '{name}'. Available: {', '.join(_ENGINES)}"
        )
    logger.debug("Instantiating OCR engine: %s (%s)", name, cls.__name__)
    return cls()


def list_engines() -> list[str]:
    if not _ENGINES:
        _register_builtins()
    return sorted(_ENGINES)
