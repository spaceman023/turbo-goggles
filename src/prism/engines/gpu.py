"""GPU and Apple Silicon accelerator detection."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def detect_gpu() -> dict:
    """Detect available GPU/accelerator hardware.

    Returns a dict with keys:
        cuda (bool): NVIDIA CUDA is available
        mps (bool): Apple Silicon MPS is available
        device (str): best available device — "cuda", "mps", or "cpu"
        detail (str): human-readable description of the device
    """
    result: dict = {"cuda": False, "mps": False, "device": "cpu", "detail": ""}

    # Check CUDA (NVIDIA)
    try:
        import torch

        if torch.cuda.is_available():
            result["cuda"] = True
            result["device"] = "cuda"
            result["detail"] = torch.cuda.get_device_name(0)
            logger.debug("GPU detection: CUDA available — %s", result["detail"])
    except ImportError:
        logger.debug("GPU detection: torch not installed, skipping CUDA check")

    # Check MPS (Apple Silicon)
    try:
        import torch

        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            result["mps"] = True
            if not result["cuda"]:
                result["device"] = "mps"
                result["detail"] = "Apple Silicon"
            logger.debug("GPU detection: MPS (Apple Silicon) available")
    except ImportError:
        logger.debug("GPU detection: torch not installed, skipping MPS check")

    if result["device"] == "cpu":
        logger.debug("GPU detection: no accelerator found, using CPU")

    return result


def _gpu_available() -> bool:
    """Return True if any GPU accelerator is available."""
    info = detect_gpu()
    return info["device"] != "cpu"
