from __future__ import annotations
import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


APP_ENV = os.getenv("STORAGEAPP_ENV", "dev")  # dev | pi
MAX_UPLOAD_MB = _env_int("STORAGEAPP_MAX_UPLOAD_MB", 4096)

# Où persister l’état
STATE_FILE = Path(os.getenv("STORAGEAPP_STATE_FILE", "./.state/state.json"))
