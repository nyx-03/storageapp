from __future__ import annotations
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import os
import subprocess
import logging
import platform
import socket
import shutil
import time
import mimetypes
import hashlib
import re
from dataclasses import asdict

from storageapp.settings import APP_ENV, STATE_FILE, MAX_UPLOAD_MB
from storageapp.providers.mock import MockDiskProvider
from storageapp.providers.linux_lsblk import LinuxLsblkProvider
from storageapp.services.state import ActiveDiskState
from storageapp.services.disks import DiskService
from storageapp.services.import_jobs import (
    JobStore,
    JobRunner,
    JobError,
    new_copy_job,
    new_upload_job,
    merge_ranges,
    missing_ranges,
)
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


@app.on_event("startup")
def _start_jobs():
    job_runner.start()


@app.get("/system")
def system_page():
    return FileResponse(str(WEB_DIR / "system.html"))


@app.get("/files")
def files_page():
    return FileResponse(str(WEB_DIR / "files.html"))


@app.get("/upload")
def upload_page():
    return FileResponse(str(WEB_DIR / "upload.html"))

provider = LinuxLsblkProvider() if APP_ENV == "pi" else MockDiskProvider()
state = ActiveDiskState(STATE_FILE)
service = DiskService(provider=provider, state=state)

job_store = JobStore()
job_runner = JobRunner(
    store=job_store,
    resolve_disk=service.resolve_disk,
    ensure_mounted=provider.ensure_mounted,
    ensure_writable=provider.ensure_writable,
)


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
def api_import_sd(req: ImportRequest):
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

    # Resolve source disk UUID from mountpoint
    src_disk = None
    for d in service.list_disks():
        if d.mountpoint and str(src_path).startswith(str(Path(d.mountpoint).resolve())):
            src_disk = d
            break
    if not src_disk:
        raise HTTPException(status_code=400, detail="source disk not recognized")

    src_rel = str(src_path.relative_to(Path(src_disk.mountpoint)))
    dest_rel = str(Path("imports") / date.today().isoformat() / src_path.name)
    dst_id = active.uuid or active.partuuid or active.dev
    src_id = src_disk.uuid or src_disk.partuuid or src_disk.dev

    job = new_copy_job(src_id, src_rel, dst_id, dest_rel)
    job_store.create(job)

    return {"job": asdict(job)}


@app.get("/api/import-jobs")
def api_import_jobs():
    return {"jobs": [asdict(j) for j in job_store.list()]}


@app.get("/api/import-jobs/{job_id}")
def api_import_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": asdict(job)}


class CopyJobRequest(BaseModel):
    src_rel_path: str
    src_uuid: str
    dst_rel_path: str
    dst_uuid: str


@app.post("/api/import-jobs/copy")
def api_create_copy_job(req: CopyJobRequest):
    job = new_copy_job(req.src_uuid, req.src_rel_path, req.dst_uuid, req.dst_rel_path)
    job_store.create(job)
    return {"job": asdict(job)}


@app.post("/api/import-jobs/{job_id}/retry")
def api_retry_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.state = "retrying"
    job.retry_at = time.time()
    job_store.update(job)
    return {"job": asdict(job)}


@app.post("/api/import-jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.state = "paused"
    job.last_error = JobError(code="CANCELLED", message="Cancelled by user", detail=None)
    job_store.update(job)
    return {"job": asdict(job)}


@app.post("/api/import-jobs/{job_id}/resume")
def api_resume_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state == "paused":
        job.state = "queued"
        job_store.update(job)
    return {"job": asdict(job)}


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

    # Try to unmount all mounted non-system disks before shutdown
    disks = service.list_disks()
    unmount_errors = []
    for d in disks:
        if not d.mountpoint:
            continue
        if d.is_system:
            continue
        ok = provider.unmount(d.dev)
        if not ok:
            unmount_errors.append(d.dev)

    if unmount_errors:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to unmount: {', '.join(unmount_errors)}",
        )

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


class UploadInitRequest(BaseModel):
    filename: str
    size: int
    sha256: str | None = None
    dir: str | None = None


CHUNK_SIZE = 4 * 1024 * 1024


def _safe_rel_path(path: str) -> str:
    rel = path.replace("\\", "/").strip().lstrip("/")
    if ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    return rel


def _active_disk_or_400():
    active = service.get_active()
    if not active:
        raise HTTPException(status_code=400, detail="No active disk selected")
    return active


def _resolve_job_disk_or_400(job) -> tuple[object, str]:
    disk_id = job.dest.mount_uuid
    if not disk_id:
        raise HTTPException(status_code=400, detail="Upload has no destination disk id")
    disk = service.resolve_disk(disk_id)
    if not disk:
        raise HTTPException(status_code=400, detail="Destination disk not found")
    mp = disk.mountpoint
    if not mp:
        try:
            mp, ok = provider.ensure_writable(disk.dev, disk.fstype)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not mp or not ok:
            raise HTTPException(status_code=400, detail="Destination disk not writable")
    return disk, mp


@app.post("/api/uploads/init")
def api_upload_init(req: UploadInitRequest):
    active = _active_disk_or_400()
    if not active.uuid and not active.partuuid:
        raise HTTPException(status_code=400, detail="Active disk has no stable identifier")

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    if req.size > max_bytes:
        raise HTTPException(status_code=413, detail="Upload too large")

    safe_name = DiskService._safe_filename(service, req.filename)
    dir_rel = _safe_rel_path(req.dir) if req.dir else ""
    rel = str(Path(dir_rel) / safe_name) if dir_rel else safe_name

    job = new_upload_job(active.uuid or active.partuuid or active.dev, rel, req.size, req.sha256)
    job_store.create(job)

    return {
        "upload_id": job.id,
        "chunk_size": CHUNK_SIZE,
        "received_ranges": job.progress.received_ranges or [],
    }


@app.get("/api/uploads/{upload_id}")
def api_upload_status(upload_id: str):
    job = job_store.get(upload_id)
    if not job or job.type != "upload":
        raise HTTPException(status_code=404, detail="Upload not found")
    total = job.progress.total or 0
    ranges = job.progress.received_ranges or []
    return {
        "job": asdict(job),
        "missing_ranges": missing_ranges(ranges, total),
    }


def _parse_content_range(value: str) -> tuple[int, int, int]:
    m = re.match(r"bytes\\s+(\\d+)-(\\d+)/(\\d+)", value)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid Content-Range")
    start, end, total = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if end < start:
        raise HTTPException(status_code=400, detail="Invalid Content-Range")
    if start < 0 or end < 0 or total <= 0 or end >= total:
        raise HTTPException(status_code=400, detail="Invalid Content-Range")
    return start, end, total


@app.put("/api/uploads/{upload_id}")
async def api_upload_chunk(upload_id: str, request: Request):
    job = job_store.get(upload_id)
    if not job or job.type != "upload":
        raise HTTPException(status_code=404, detail="Upload not found")
    if job.state in {"done", "failed"}:
        raise HTTPException(status_code=409, detail=f"Upload is {job.state}")

    content_range = request.headers.get("content-range")
    if not content_range:
        raise HTTPException(status_code=411, detail="Content-Range required")

    start, end, total = _parse_content_range(content_range)
    if job.progress.total is None:
        job.progress.total = total
    if total != job.progress.total:
        raise HTTPException(status_code=400, detail="Content-Range total mismatch")

    _, dest_mp = _resolve_job_disk_or_400(job)

    tmp_file = Path(dest_mp) / (job.dest.tmp_path or f".storageapp/tmp/{job.id}.part")
    tmp_file.parent.mkdir(parents=True, exist_ok=True)

    expected = end - start + 1
    written = 0
    start_time = time.time()
    with tmp_file.open("r+b" if tmp_file.exists() else "wb") as f:
        f.seek(start)
        remaining = end - start + 1
        async for chunk in request.stream():
            if not chunk:
                break
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            f.write(chunk)
            written += len(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                break
        f.flush()
        os.fsync(f.fileno())

    if written != expected:
        raise HTTPException(status_code=400, detail="Chunk size mismatch")

    ranges = job.progress.received_ranges or []
    ranges = merge_ranges(ranges, [start, end])
    job.progress.received_ranges = ranges
    job.progress.bytes_done = sum(r[1] - r[0] + 1 for r in ranges)
    elapsed = max(time.time() - start_time, 0.001)
    job.progress.speed = written / elapsed
    job_store.update(job)

    return {"received_ranges": ranges, "bytes_done": job.progress.bytes_done}


@app.post("/api/uploads/{upload_id}/finalize")
def api_upload_finalize(upload_id: str):
    job = job_store.get(upload_id)
    if not job or job.type != "upload":
        raise HTTPException(status_code=404, detail="Upload not found")
    if job.state == "done":
        return {"job": asdict(job)}
    if job.state == "failed":
        raise HTTPException(status_code=409, detail="Upload already failed")

    total = job.progress.total or 0
    ranges = job.progress.received_ranges or []
    missing = missing_ranges(ranges, total)
    if missing:
        raise HTTPException(status_code=400, detail="Upload incomplete")

    _, dest_mp = _resolve_job_disk_or_400(job)

    tmp_file = Path(dest_mp) / (job.dest.tmp_path or f".storageapp/tmp/{job.id}.part")
    final_file = Path(dest_mp) / (job.dest.relative_path or f"{job.id}")

    if not tmp_file.exists():
        raise HTTPException(status_code=404, detail="Temp file not found")

    job.state = "verifying"
    job_store.update(job)

    hasher = hashlib.sha256()
    with tmp_file.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    dest_hash = hasher.hexdigest()
    job.integrity.dest_sha256 = dest_hash

    if job.integrity.source_sha256 and job.integrity.source_sha256 != dest_hash:
        job.state = "failed"
        job.last_error = JobError(code="CHECKSUM_MISMATCH", message="Checksum mismatch", detail=None)
        job_store.update(job)
        raise HTTPException(status_code=400, detail="Checksum mismatch")

    if final_file.exists():
        job.state = "failed"
        job.last_error = JobError(code="EIO", message="Destination already exists", detail=None)
        job_store.update(job)
        raise HTTPException(status_code=400, detail="Destination already exists")

    final_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file.replace(final_file)
    job.integrity.verified = True
    job.state = "done"
    job.finished_at = time.time()
    job_store.update(job)

    return {"job": asdict(job)}
