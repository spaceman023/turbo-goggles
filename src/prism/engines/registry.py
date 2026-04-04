"""Registry of available OCR engines."""

from __future__ import annotations

from prism.engines.base import OCREngine


_ENGINES: dict[str, type[OCREngine]] = {}


def _register_builtins() -> None:
    from prism.engines.tesseract import TesseractEngine
    from prism.engines.paddle import PaddleEngine

    _ENGINES["tesseract"] = TesseractEngine
    _ENGINES["paddleocr"] = PaddleEngine


def get_engine(name: str) -> OCREngine:
    """Return an instance of the named OCR engine."""
    if not _ENGINES:
        _register_builtins()
    cls = _ENGINES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown OCR engine '{name}'. Available: {', '.join(_ENGINES)}"
        )
    return cls()


def list_engines() -> list[str]:
    if not _ENGINES:
        _register_builtins()
    return sorted(_ENGINES)
