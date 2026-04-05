"""JSONL message emitter — writes structured messages to stdout for Tauri IPC."""

from __future__ import annotations

import logging
import sys
import threading

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_emit_lock = threading.Lock()


def emit(msg: BaseModel) -> None:
    """Serialize a Pydantic model as a single JSON line to stdout."""
    line = msg.model_dump_json()
    logger.debug("JSONL emit: %s", line[:200] + ("…" if len(line) > 200 else ""))
    with _emit_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
