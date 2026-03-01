from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional
from storageapp.domain.models import Disk


class DiskProvider(ABC):
    @abstractmethod
    def list_disks(self) -> List[Disk]:
        raise NotImplementedError

    def ensure_writable(self, dev: str, fstype: str | None) -> Tuple[Optional[str], bool]:
        # Par défaut : ne fait rien (utile pour le provider mock)
        return None, False

    def ensure_mounted(self, dev: str, fstype: str | None, readonly: bool = False) -> Tuple[Optional[str], bool]:
        # Par défaut : ne fait rien (utile pour le provider mock)
        return None, False

    def unmount(self, dev: str) -> bool:
        # Par défaut : noop
        return True
