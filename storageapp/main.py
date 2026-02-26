from __future__ import annotations
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi import BackgroundTasks
from pathlib import Path
import subprocess
import logging

from storageapp.settings import APP_ENV, STATE_FILE, API_KEY, MAX_UPLOAD_MB
from storageapp.providers.mock import MockDiskProvider
from storageapp.providers.linux_lsblk import LinuxLsblkProvider
from storageapp.services.state import ActiveDiskState
from storageapp.services.disks import DiskService
from storageapp.services.import_jobs import ImportJobStore, run_rsync_job, job_to_dict
from storageapp.services.sd_detect import find_media_sources

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

provider = LinuxLsblkProvider() if APP_ENV == "pi" else MockDiskProvider()
state = ActiveDiskState(STATE_FILE)
service = DiskService(provider=provider, state=state)

import_store = ImportJobStore()


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/api/"):
        if request.headers.get("X-API-Key") != API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


class SetActiveRequest(BaseModel):
    dev: str


class ImportRequest(BaseModel):
    source_path: str  # ex: /media/nyx-03/SDCARD/DCIM (or recommended_path from /api/sd/sources)
    ignore_existing: bool = False


@app.get("/api/disks")
def api_list_disks():
    disks = service.list_disks()
    active_dev = state.get_active_dev()
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


@app.post("/api/import-sd")
def api_import_sd(req: ImportRequest, background: BackgroundTasks):
    """Lance un import rsync depuis la carte SD vers le disque actif."""
    if import_store.has_running():
        raise HTTPException(status_code=409, detail="An import job is already running")

    active = service.get_active()
    if not active:
        raise HTTPException(status_code=400, detail="No active disk selected")
    if not active.mountpoint or not active.writable:
        raise HTTPException(status_code=400, detail="Active disk is not writable or has no mountpoint")

    sources = find_media_sources()
    allowed = set()
    for s in sources:
        if s.get("path"):
            allowed.add(str(Path(s["path"]).resolve()))
        if s.get("recommended_path"):
            allowed.add(str(Path(s["recommended_path"]).resolve()))

    src = Path(req.source_path).resolve()
    if str(src) not in allowed:
        raise HTTPException(status_code=400, detail="source_path is not an allowed SD source")

    if not src.exists() or not src.is_dir():
        raise HTTPException(status_code=400, detail="source_path is not a valid directory")

    dest = Path(active.mountpoint) / "imports" / date.today().isoformat() / src.name
    try:
        job = import_store.create(source=str(src), dest=str(dest), ignore_existing=req.ignore_existing)
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
