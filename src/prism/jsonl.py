"""JSONL message emitter — writes structured messages to stdout for Tauri IPC."""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel


def emit(msg: BaseModel) -> None:
    """Serialize a Pydantic model as a single JSON line to stdout."""
    line = msg.model_dump_json()
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
