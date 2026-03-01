from __future__ import annotations
import json
from pathlib import Path
from typing import Optional


class ActiveDiskState:
    """
    Persistance très simple: un fichier JSON.
    Sur Pi, on le mettra dans /var/lib/storageapp/state.json.
    Sur Mac, on peut rester local.
    """
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_active_id(self) -> Optional[str]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data.get("active_id") or data.get("active_dev")
        except Exception:
            return None

    def set_active_id(self, active_id: str) -> None:
        self.path.write_text(json.dumps({"active_id": active_id}, indent=2), encoding="utf-8")

    def get_active_dev(self) -> Optional[str]:
        return self.get_active_id()

    def set_active_dev(self, dev: str) -> None:
        self.set_active_id(dev)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
