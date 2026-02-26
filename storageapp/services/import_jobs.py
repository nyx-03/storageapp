from __future__ import annotations

import uuid
import time
import json
import os
import subprocess
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any
import re
import threading


@dataclass
class ImportJob:
    id: str
    source: str
    dest: str
    status: str  # queued | running | done | failed
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    rsync_output_tail: str = ""  # dernier extrait des logs rsync
    ignore_existing: bool = False
    progress: Optional[float] = None  # 0.0 - 100.0
    bytes_done: Optional[int] = None
    bytes_total: Optional[int] = None


# Persistence
# Default location on Raspberry Pi. In dev, we can override with STORAGEAPP_JOBS_FILE.
DEFAULT_JOBS_FILE = Path("/var/lib/storageapp/import_jobs.json")
logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"queued", "running"}


class ImportBusyError(RuntimeError):
    pass


def _jobs_file_path() -> Path:
    env = os.environ.get("STORAGEAPP_JOBS_FILE")
    if env:
        return Path(env)
    return DEFAULT_JOBS_FILE


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_jobs_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        # If corrupted, ignore; we prefer app to keep running.
        return {}


class ImportJobStore:
    """Stockage des jobs d'import.

    - En mémoire pour l'exécution.
    - Persisté sur disque (JSON) pour conserver l'historique après reboot.

    Le chemin par défaut est `/var/lib/storageapp/import_jobs.json`.
    En dev, on peut surcharger via la variable d'env `STORAGEAPP_JOBS_FILE`.
    """

    def __init__(self):
        self.jobs: Dict[str, ImportJob] = {}
        self._lock = threading.Lock()
        self._jobs_file = _jobs_file_path()
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        payload = _load_jobs_file(self._jobs_file)
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            return

        loaded: Dict[str, ImportJob] = {}
        for item in jobs:
            if not isinstance(item, dict):
                continue
            jid = item.get("id")
            if not jid:
                continue

            # Restore job
            try:
                job = ImportJob(
                    id=str(item.get("id")),
                    source=str(item.get("source")),
                    dest=str(item.get("dest")),
                    status=str(item.get("status")),
                    created_at=float(item.get("created_at")),
                    started_at=item.get("started_at"),
                    finished_at=item.get("finished_at"),
                    error=item.get("error"),
                    rsync_output_tail=str(item.get("rsync_output_tail") or ""),
                    ignore_existing=bool(item.get("ignore_existing", False)),
                    progress=item.get("progress"),
                    bytes_done=item.get("bytes_done"),
                    bytes_total=item.get("bytes_total"),
                )

                # Normalize optional times
                if job.started_at is not None:
                    job.started_at = float(job.started_at)
                if job.finished_at is not None:
                    job.finished_at = float(job.finished_at)

                loaded[job.id] = job
            except Exception:
                continue

        with self._lock:
            self.jobs = loaded

    def _save_to_disk(self) -> None:
        # Persist only the latest N jobs to keep the file small
        with self._lock:
            latest = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)[:100]
            data = {"jobs": [asdict(j) for j in latest]}
        _atomic_write_json(self._jobs_file, data)

    def create(self, source: str, dest: str, ignore_existing: bool = False) -> ImportJob:
        jid = uuid.uuid4().hex
        job = ImportJob(
            id=jid,
            source=source,
            dest=dest,
            status="queued",
            created_at=time.time(),
            ignore_existing=bool(ignore_existing),
        )
        with self._lock:
            self.jobs[jid] = job
        try:
            self._save_to_disk()
        except Exception:
            with self._lock:
                self.jobs.pop(jid, None)
            raise
        return job

    def get(self, jid: str) -> Optional[ImportJob]:
        with self._lock:
            return self.jobs.get(jid)

    def update(self, job: ImportJob) -> None:
        # Store the job and persist
        with self._lock:
            self.jobs[job.id] = job
        try:
            self._save_to_disk()
        except Exception:
            logger.exception("Failed to persist import jobs")

    def list(self) -> list[ImportJob]:
        # Plus récent d'abord
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def has_running(self) -> bool:
        with self._lock:
            return any(j.status in ACTIVE_STATUSES for j in self.jobs.values())

    def create_if_available(self, source: str, dest: str, ignore_existing: bool = False) -> ImportJob:
        with self._lock:
            if any(j.status in ACTIVE_STATUSES for j in self.jobs.values()):
                raise ImportBusyError("An import job is already running or queued")

            jid = uuid.uuid4().hex
            job = ImportJob(
                id=jid,
                source=source,
                dest=dest,
                status="queued",
                created_at=time.time(),
                ignore_existing=bool(ignore_existing),
            )
            self.jobs[jid] = job

        try:
            self._save_to_disk()
        except Exception:
            with self._lock:
                self.jobs.pop(jid, None)
            raise

        return job


_PROGRESS_RE = re.compile(r"(?P<pct>\d+)%")


def _parse_progress(line: str) -> Optional[float]:
    """Extract percentage from rsync progress2 output if present."""
    m = _PROGRESS_RE.search(line)
    if not m:
        return None
    try:
        return float(m.group("pct"))
    except Exception:
        return None


def run_rsync_job(store: ImportJobStore, jid: str) -> None:
    job = store.get(jid)
    if not job:
        return

    job.status = "running"
    job.started_at = time.time()
    job.progress = 0.0
    store.update(job)

    src = Path(job.source)
    dst = Path(job.dest)

    cmd = [
        "rsync",
        "-a",
        "--info=stats2,progress2",
        "--human-readable",
        "--no-perms",
        "--no-owner",
        "--no-group",
        str(src) + "/",
        str(dst) + "/",
    ]

    if getattr(job, "ignore_existing", False):
        cmd.insert(2, "--ignore-existing")

    try:
        dst.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        # Stream output
        for line in proc.stdout or []:
            line = line.strip()
            if not line:
                continue

            # Keep tail for UI
            job.rsync_output_tail = (job.rsync_output_tail + "\n" + line)[-2000:]

            pct = _parse_progress(line)
            if pct is not None:
                job.progress = pct

            store.update(job)

        rc = proc.wait()
        if rc == 0:
            job.progress = 100.0
            job.status = "done"
            job.finished_at = time.time()
            store.update(job)
        else:
            job.status = "failed"
            job.error = f"rsync failed (code {rc})"
            job.finished_at = time.time()
            store.update(job)

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.finished_at = time.time()
        store.update(job)


def job_to_dict(job: ImportJob) -> Dict[str, Any]:
    return asdict(job)
