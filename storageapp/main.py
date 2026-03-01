from __future__ import annotations
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi import BackgroundTasks
from pathlib import Path
import subprocess
import logging
import platform
import socket
import shutil
import time
import mimetypes

from storageapp.settings import APP_ENV, STATE_FILE, MAX_UPLOAD_MB
from storageapp.providers.mock import MockDiskProvider
from storageapp.providers.linux_lsblk import LinuxLsblkProvider
from storageapp.services.state import ActiveDiskState
from storageapp.services.disks import DiskService
from storageapp.services.import_jobs import ImportJobStore, run_rsync_job, job_to_dict, ImportBusyError
from storageapp.services.sd_detect import find_media_sources, recommended_path_for

from fastapi import UploadFile, File
from typing import List
from datetime import date


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="StorageApp", version="0.1.0")

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Static assets (CSS/JS)
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")

# Home page
@app.get("/")
def home():
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/system")
def system_page():
    return FileResponse(str(WEB_DIR / "system.html"))


@app.get("/files")
def files_page():
    return FileResponse(str(WEB_DIR / "files.html"))

provider = LinuxLsblkProvider() if APP_ENV == "pi" else MockDiskProvider()
state = ActiveDiskState(STATE_FILE)
service = DiskService(provider=provider, state=state)

import_store = ImportJobStore()


class SetActiveRequest(BaseModel):
    dev: str


class ImportRequest(BaseModel):
    source_path: str  # ex: /media/nyx-03/SDCARD/DCIM (or recommended_path from /api/sd/sources)
    ignore_existing: bool = False


@app.get("/api/disks")
def api_list_disks():
    disks = service.list_disks()
    active = service.get_active()
    active_dev = active.dev if active else None
    return {
        "active_dev": active_dev,
        "disks": [d.model_dump() | {"active": (d.dev == active_dev)} for d in disks],
    }


@app.get("/api/disks/active")
def api_get_active():
    d = service.get_active()
    if not d:
        return {"active": None}
    return {"active": d.model_dump()}


@app.post("/api/disks/active")
def api_set_active(req: SetActiveRequest):
    try:
        d = service.set_active(req.dev)
        return {"active": d.model_dump()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/upload")
def api_upload(request: Request, files: List[UploadFile] = File(...)):
    """
    Upload multi-fichiers vers le disque actif.
    Écrit en streaming (pas tout en RAM).
    """
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Upload too large")
        except ValueError:
            pass

    try:
        result = service.save_uploads(files, max_total_bytes=max_bytes)
        return result
    except ValueError as e:
        msg = str(e)
        if "Upload size limit exceeded" in msg:
            raise HTTPException(status_code=413, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


# SD import API

@app.get("/api/sd/sources")
def api_sd_sources():
    """Liste des supports montés qui ressemblent à une carte SD / caméra."""
    return {"sources": find_media_sources()}


@app.get("/api/sources")
def api_sources():
    """Liste des supports USB utilisables comme source de copie."""
    active = service.get_active()
    active_dev = active.dev if active else None
    sources = []
    for d in service.list_disks():
        if d.dev == active_dev:
            logger.info("source skip dev=%s label=%s reason=active", d.dev, d.label)
            continue

        mp = d.mountpoint
        recommended, sigs = recommended_path_for(Path(mp)) if mp else (None, [])
        logger.info("source candidate dev=%s label=%s mp=%s", d.dev, d.label, mp)
        sources.append({
            "dev": d.dev,
            "label": d.label,
            "fstype": d.fstype,
            "size": d.size,
            "mountpoint": mp,
            "recommended_path": recommended,
            "signatures": sigs,
            "uuid": d.uuid,
            "partuuid": d.partuuid,
        })

    return {"sources": sources}


@app.post("/api/import-sd")
def api_import_sd(req: ImportRequest, background: BackgroundTasks):
    """Lance un import rsync depuis la carte SD vers le disque actif."""
    active = service.get_active()
    if not active:
        raise HTTPException(status_code=400, detail="No active disk selected")
    if not active.mountpoint or not active.writable:
        raise HTTPException(status_code=400, detail="Active disk is not writable or has no mountpoint")

    source_value = req.source_path.strip()
    src_path: Path | None = None

    # If user passes a device id/uuid, resolve and mount read-only on demand.
    if source_value.startswith("/dev/") or len(source_value) >= 8:
        disks = service.list_disks()
        src_disk = next((d for d in disks if source_value in {d.dev, d.uuid, d.partuuid}), None)
        if src_disk:
            if src_disk.dev == active.dev:
                raise HTTPException(status_code=400, detail="source disk cannot be the active destination")
            mp = src_disk.mountpoint
            if not mp:
                try:
                    mp, ok = provider.ensure_mounted(src_disk.dev, src_disk.fstype, readonly=True)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=str(e))
                if not mp or not ok:
                    raise HTTPException(status_code=400, detail="source disk is not readable or could not be mounted")
            recommended, _ = recommended_path_for(Path(mp))
            src_path = Path(recommended)

    if src_path is None:
        # Fallback: strict whitelist from detected media sources
        sources = find_media_sources()
        allowed = set()
        for s in sources:
            if s.get("path"):
                allowed.add(str(Path(s["path"]).resolve()))
            if s.get("recommended_path"):
                allowed.add(str(Path(s["recommended_path"]).resolve()))

        src_path = Path(source_value).resolve()
        if str(src_path) not in allowed:
            raise HTTPException(status_code=400, detail="source_path is not an allowed SD source")

    if not src_path.exists() or not src_path.is_dir():
        raise HTTPException(status_code=400, detail="source_path is not a valid directory")

    dest = Path(active.mountpoint) / "imports" / date.today().isoformat() / src_path.name
    try:
        job = import_store.create_if_available(source=str(src_path), dest=str(dest), ignore_existing=req.ignore_existing)
    except ImportBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("Failed to create import job")
        raise HTTPException(status_code=500, detail=f"Failed to create import job: {e}")

    background.add_task(run_rsync_job, import_store, job.id)

    payload = job_to_dict(job)
    payload["ignore_existing"] = bool(req.ignore_existing)
    return {"job": payload}


@app.get("/api/import-jobs")
def api_import_jobs():
    return {"jobs": [job_to_dict(j) for j in import_store.list()]}


@app.get("/api/import-jobs/{job_id}")
def api_import_job(job_id: str):
    job = import_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job_to_dict(job)}


# System actions

@app.post("/api/system/shutdown")
def api_system_shutdown():
    """Éteint proprement le Raspberry Pi.

    Sur Pi, nécessite une règle sudoers autorisant l'utilisateur `storageapp` à exécuter
    `systemctl poweroff` (ou `shutdown -h now`) sans mot de passe.

    En environnement dev (non-pi), l'endpoint renvoie OK sans éteindre la machine.
    """
    if APP_ENV != "pi":
        return {"ok": True, "message": "(dev) Shutdown simulated"}

    try:
        # Try systemd poweroff first
        r = subprocess.run(
            ["sudo", "/bin/systemctl", "poweroff"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # Fallback to shutdown
            r2 = subprocess.run(
                ["sudo", "/sbin/shutdown", "-h", "now"],
                capture_output=True,
                text=True,
            )
            if r2.returncode != 0:
                err = ((r.stderr or "") + "\n" + (r2.stderr or "")).strip()
                raise RuntimeError(err or "shutdown failed")

        return {"ok": True, "message": "Extinction en cours…"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Shutdown failed: {e}")


def _read_first_line(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0]
    except Exception:
        return None


def _cpu_temp_c() -> float | None:
    raw = _read_first_line(Path("/sys/class/thermal/thermal_zone0/temp"))
    if not raw:
        return None
    try:
        return round(float(raw) / 1000.0, 1)
    except Exception:
        return None


def _uptime_seconds() -> int | None:
    raw = _read_first_line(Path("/proc/uptime"))
    if not raw:
        return None
    try:
        return int(float(raw.split()[0]))
    except Exception:
        return None


def _meminfo() -> dict:
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            val = val.strip().split()[0]
            mem[key] = int(val) * 1024
    except Exception:
        return {}
    return mem


def _primary_ip() -> str | None:
    try:
        proc = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
        if proc.returncode == 0:
            for part in proc.stdout.split():
                if part and not part.startswith("127."):
                    return part
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        return None
    return None


@app.get("/api/system/info")
def api_system_info():
    uptime = _uptime_seconds()
    mem = _meminfo()
    disk = shutil.disk_usage("/")
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "uptime_seconds": uptime,
        "boot_time": int(time.time() - uptime) if uptime is not None else None,
        "ip": _primary_ip(),
        "cpu_temp_c": _cpu_temp_c(),
        "memory": {
            "total": mem.get("MemTotal"),
            "available": mem.get("MemAvailable"),
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        },
    }


@app.get("/api/files")
def api_list_files(path: str | None = None):
    active = service.get_active()
    if not active or not active.mountpoint:
        raise HTTPException(status_code=400, detail="No active disk selected")

    base = Path(active.mountpoint).resolve()
    rel = (path or "").lstrip("/")
    target = (base / rel).resolve()

    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Path outside active disk")

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = []
    errors = []
    try:
        for entry in target.iterdir():
            try:
                stat = entry.stat()
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                })
            except Exception as e:
                errors.append({"name": entry.name, "error": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list directory: {e}")

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))

    return {
        "disk": {
            "dev": active.dev,
            "label": active.label,
            "mountpoint": active.mountpoint,
        },
        "cwd": str(target.relative_to(base)),
        "entries": entries,
        "errors": errors,
    }


@app.get("/api/file")
def api_get_file(path: str):
    active = service.get_active()
    if not active or not active.mountpoint:
        raise HTTPException(status_code=400, detail="No active disk selected")

    base = Path(active.mountpoint).resolve()
    rel = (path or "").lstrip("/")
    target = (base / rel).resolve()

    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Path outside active disk")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(str(target), media_type=mime or "application/octet-stream")
