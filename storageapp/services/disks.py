from __future__ import annotations
from typing import List, Optional, Dict, Any
from storageapp.domain.models import Disk
from storageapp.providers.base import DiskProvider
from storageapp.services.state import ActiveDiskState
from pathlib import Path
from datetime import date
import os
import re
from fastapi import UploadFile


class DiskService:
    def __init__(self, provider: DiskProvider, state: ActiveDiskState):
        self.provider = provider
        self.state = state

    def list_disks(self) -> List[Disk]:
        disks = self.provider.list_disks()
        active = self.state.get_active_id()

        # Marque “actif” via un champ calculé (on le renverra côté API)
        for d in disks:
            # on ajoute dynamiquement un attribut (FastAPI le serialise si on le met dans dict)
            pass
        return disks

    def get_active(self) -> Optional[Disk]:
        active = self.state.get_active_id()
        if not active:
            return None
        for d in self.provider.list_disks():
            if self._matches_id(d, active):
                return d
        return None

    def _matches_id(self, disk: Disk, disk_id: str) -> bool:
        return disk_id in {disk.dev, disk.uuid, disk.partuuid}

    def set_active(self, disk_id: str) -> Disk:
        disks = self.provider.list_disks()
        match = next((d for d in disks if self._matches_id(d, disk_id)), None)
        if not match:
            raise ValueError("Disk not found")
        if not match.supported:
            raise ValueError("Disk filesystem not supported")

        # 🔒 Sécurisation: montage + writable (automatique)
        mp, ok = self.provider.ensure_writable(match.dev, match.fstype)
        if mp:
            match.mountpoint = mp
        match.writable = ok

        if not ok:
            raise ValueError("Disk is not writable by the service (check filesystem or permissions)")

        stable_id = match.uuid or match.partuuid or match.dev
        self.state.set_active_id(stable_id)
        return match

    def _safe_filename(self, name: str) -> str:
        """
        Nettoie le nom de fichier pour éviter:
        - path traversal (../)
        - caractères bizarres
        - noms vides
        """
        name = os.path.basename(name or "")
        name = name.strip()

        # Remplace tout ce qui n'est pas alphanum, point, tiret, underscore
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)

        if not name or name in {".", ".."}:
            name = "file"

        # Limite raisonnable
        return name[:180]

    def save_uploads(self, files: List[UploadFile], max_total_bytes: Optional[int] = None) -> Dict[str, Any]:
        """
        Sauvegarde les uploads dans:
          <mountpoint>/uploads/YYYY-MM-DD/<filename>
        """
        active = self.get_active()
        if not active:
            raise ValueError("No active disk selected")

        if not active.mountpoint:
            raise ValueError("Active disk has no mountpoint")

        # Par sécurité, on refuse si pas writable (normalement garanti par set_active)
        if not active.writable:
            raise ValueError("Active disk is not writable")

        base_dir = Path(active.mountpoint) / "uploads" / date.today().isoformat()
        base_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        errors = []
        total_written = 0
        limit_exceeded = False

        for f in files:
            dest = None
            try:
                original = f.filename or "file"
                safe = self._safe_filename(original)

                dest = base_dir / safe

                # Évite d'écraser silencieusement: si existe, on suffixe
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    i = 1
                    while True:
                        candidate = base_dir / f"{stem}_{i}{suffix}"
                        if not candidate.exists():
                            dest = candidate
                            break
                        i += 1

                # Streaming vers disque (important pour vidéos)
                with dest.open("wb") as out:
                    while True:
                        chunk = f.file.read(1024 * 1024)
                        if not chunk:
                            break

                        if max_total_bytes is not None and (total_written + len(chunk)) > max_total_bytes:
                            limit_exceeded = True
                            raise ValueError("Upload size limit exceeded")

                        out.write(chunk)
                        total_written += len(chunk)

                size = dest.stat().st_size
                saved.append({
                    "filename": original,
                    "saved_as": dest.name,
                    "path": str(dest),
                    "bytes": size,
                    "content_type": f.content_type,
                })

            except Exception as e:
                if dest and dest.exists():
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                errors.append({
                    "filename": getattr(f, "filename", None),
                    "error": str(e),
                })
            finally:
                try:
                    f.file.close()
                except Exception:
                    pass

            if limit_exceeded:
                break

        return {
            "active_dev": active.dev,
            "mountpoint": active.mountpoint,
            "target_dir": str(base_dir),
            "saved": saved,
            "errors": errors,
        }
