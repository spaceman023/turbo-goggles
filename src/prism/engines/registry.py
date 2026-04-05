"""Registry of available OCR engines."""

from __future__ import annotations

import logging

from prism.engines.base import OCREngine

logger = logging.getLogger(__name__)

_ENGINES: dict[str, type[OCREngine]] = {}

# All known engine names for availability checking
_ALL_ENGINES = ("tesseract", "paddleocr", "easyocr", "surya", "doctr", "macos_vision")


def _register_builtins() -> None:
    import importlib

    # (engine name, adapter module, adapter class, library to probe)
    _DEFS = [
        ("tesseract", "prism.engines.tesseract", "TesseractEngine", "pytesseract"),
        ("paddleocr", "prism.engines.paddle", "PaddleEngine", "paddleocr"),
        ("easyocr", "prism.engines.easyocr_engine", "EasyOCREngine", "easyocr"),
        ("surya", "prism.engines.surya_engine", "SuryaEngine", "surya"),
        ("doctr", "prism.engines.doctr_engine", "DocTREngine", "doctr.models"),
        ("macos_vision", "prism.engines.macos_vision", "MacOSVisionEngine", "Vision"),
    ]

    for name, adapter_mod, cls_name, probe_mod in _DEFS:
        try:
            importlib.import_module(probe_mod)
            mod = importlib.import_module(adapter_mod)
            _ENGINES[name] = getattr(mod, cls_name)
        except Exception as exc:
            logger.debug("Skipping engine %s: %s", name, exc)

    logger.debug("Registered OCR engines: %s", list(_ENGINES.keys()))


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


def check_engines() -> dict[str, dict]:
    """Return availability status for all known OCR engines.

    Actually probes the underlying library import, not just whether
    the adapter file loaded.
    """
    # Map engine name → (import statement to probe, pip install hint)
    _PROBES: dict[str, tuple[str, str]] = {
        "tesseract": ("pytesseract", "pip install pytesseract"),
        "paddleocr": ("paddleocr", "pip install paddleocr paddlepaddle"),
        "easyocr": ("easyocr", "pip install easyocr"),
        "surya": ("surya", "pip install surya-ocr"),
        "doctr": ("doctr.models", "pip install python-doctr[torch]"),
        "macos_vision": ("Vision", "pip install pyobjc-framework-Vision (macOS only)"),
    }

    import importlib

    status: dict[str, dict] = {}
    for engine_name in _ALL_ENGINES:
        probe_mod, install_hint = _PROBES.get(engine_name, (None, ""))
        if probe_mod is None:
            status[engine_name] = {"available": False, "detail": "Unknown engine"}
            continue
        try:
            importlib.import_module(probe_mod)
            status[engine_name] = {"available": True, "detail": "Installed"}
        except ImportError:
            status[engine_name] = {
                "available": False,
                "detail": f"Not installed ({install_hint})",
            }

    return status
