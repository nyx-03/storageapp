from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List, Tuple

logger = logging.getLogger(__name__)

# Persistence
DEFAULT_JOBS_FILE = Path("/var/lib/storageapp/import_jobs.json")

STATE_ACTIVE = {"queued", "copying", "verifying", "retrying"}


@dataclass
class JobSource:
    kind: str  # "disk" | "upload"
    path: Optional[str] = None  # relative path for disk jobs
    mount_uuid: Optional[str] = None
    size: Optional[int] = None
    sha256: Optional[str] = None


@dataclass
class JobDest:
    mount_uuid: Optional[str]
    relative_path: Optional[str]
    tmp_path: Optional[str]
    final_path: Optional[str]


@dataclass
class JobProgress:
    bytes_done: int = 0
    total: Optional[int] = None
    speed: Optional[float] = None
    received_ranges: Optional[List[List[int]]] = None


@dataclass
class JobIntegrity:
    algo: str = "sha256"
    source_sha256: Optional[str] = None
    dest_sha256: Optional[str] = None
    verified: bool = False


@dataclass
class JobError:
    code: str
    message: str
    detail: Optional[str] = None


@dataclass
class Job:
    id: str
    type: str  # "copy" | "upload"
    state: str  # queued/copying/verifying/done/failed/paused/retrying
    source: JobSource
    dest: JobDest
    progress: JobProgress
    integrity: JobIntegrity
    attempts: int
    last_error: Optional[JobError]
    created_at: float
    updated_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    retry_at: Optional[float] = None


class ImportBusyError(RuntimeError):
    pass


class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs_file = _jobs_file_path()
        self.jobs: Dict[str, Job] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        payload = _load_jobs_file(self._jobs_file)
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            return

        loaded: Dict[str, Job] = {}
        for item in jobs:
            try:
                job = _job_from_dict(item)
                loaded[job.id] = job
            except Exception:
                continue

        with self._lock:
            self.jobs = loaded

    def _save_to_disk(self) -> None:
        with self._lock:
            latest = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)[:200]
            data = {"jobs": [asdict(j) for j in latest]}
        _atomic_write_json(self._jobs_file, data)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get(self, jid: str) -> Optional[Job]:
        with self._lock:
            return self.jobs.get(jid)

    def create(self, job: Job) -> Job:
        with self._lock:
            self.jobs[job.id] = job
        try:
            self._save_to_disk()
        except Exception:
            with self._lock:
                self.jobs.pop(job.id, None)
            logger.exception("Failed to persist job creation")
            raise
        return job

    def update(self, job: Job) -> None:
        job.updated_at = time.time()
        with self._lock:
            self.jobs[job.id] = job
        try:
            self._save_to_disk()
        except Exception:
            logger.exception("Failed to persist jobs")

    def set_state(self, job: Job, state: str, error: Optional[JobError] = None) -> None:
        job.state = state
        job.last_error = error
        if state in {"copying", "verifying"} and job.started_at is None:
            job.started_at = time.time()
        if state in {"done", "failed", "paused"}:
            job.finished_at = time.time()
        self.update(job)

    def reset_incomplete(self) -> None:
        with self._lock:
            for job in self.jobs.values():
                if job.type == "copy" and job.state in {"copying", "verifying", "retrying"}:
                    job.state = "queued"
        self._save_to_disk()

    def next_runnable(self) -> Optional[Job]:
        now = time.time()
        with self._lock:
            for job in sorted(self.jobs.values(), key=lambda j: j.created_at):
                if job.type != "copy":
                    continue
                if job.state not in {"queued", "retrying"}:
                    continue
                if job.retry_at and job.retry_at > now:
                    continue
                return job
        return None


def _jobs_file_path() -> Path:
    env = os.environ.get("STORAGEAPP_JOBS_FILE")
    if env:
        return Path(env)
    try:
        DEFAULT_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        test_path = DEFAULT_JOBS_FILE.parent / ".storageapp_write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        return DEFAULT_JOBS_FILE
    except Exception:
        return Path("./.state/import_jobs.json")


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
        return {}


def _job_from_dict(item: Dict[str, Any]) -> Job:
    src = item.get("source") or {}
    dst = item.get("dest") or {}
    progress = item.get("progress") or {}
    integrity = item.get("integrity") or {}
    last_error = item.get("last_error")

    job = Job(
        id=str(item.get("id")),
        type=str(item.get("type")),
        state=str(item.get("state")),
        source=JobSource(**src),
        dest=JobDest(**dst),
        progress=JobProgress(**progress),
        integrity=JobIntegrity(**integrity),
        attempts=int(item.get("attempts", 0)),
        last_error=JobError(**last_error) if isinstance(last_error, dict) else None,
        created_at=float(item.get("created_at")),
        updated_at=float(item.get("updated_at", item.get("created_at", time.time()))),
        started_at=item.get("started_at"),
        finished_at=item.get("finished_at"),
        retry_at=item.get("retry_at"),
    )

    if job.started_at is not None:
        job.started_at = float(job.started_at)
    if job.finished_at is not None:
        job.finished_at = float(job.finished_at)
    if job.retry_at is not None:
        job.retry_at = float(job.retry_at)

    return job


class JobRunner:
    def __init__(
        self,
        store: JobStore,
        resolve_disk: Callable[[str], Optional[Any]],
        ensure_mounted: Callable[[str, Optional[str], bool], Tuple[Optional[str], bool]],
        ensure_writable: Callable[[str, Optional[str]], Tuple[Optional[str], bool]],
    ):
        self.store = store
        self.resolve_disk = resolve_disk
        self.ensure_mounted = ensure_mounted
        self.ensure_writable = ensure_writable
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.reset_incomplete()
        self._thread = threading.Thread(target=self._loop, name="storageapp-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_runnable()
            if not job:
                time.sleep(1.0)
                continue
            self._run_copy_job(job)

    def _run_copy_job(self, job: Job) -> None:
        job.state = "copying"
        job.started_at = time.time()
        job.retry_at = None
        self.store.update(job)

        try:
            src_disk = self._resolve_disk(job.source.mount_uuid)
            dst_disk = self._resolve_disk(job.dest.mount_uuid)
            if not src_disk or not dst_disk:
                raise _JobError("DISK_GONE", "Source or destination disk not found")

            src_mp = src_disk.mountpoint
            if not src_mp:
                src_mp, ok = self.ensure_mounted(src_disk.dev, src_disk.fstype, True)
                if not src_mp or not ok:
                    raise _JobError("DISK_GONE", "Source disk not mounted or not readable")

            dst_mp = dst_disk.mountpoint
            if not dst_mp:
                dst_mp, ok = self.ensure_writable(dst_disk.dev, dst_disk.fstype)
                if not dst_mp or not ok:
                    raise _JobError("PERM", "Destination disk not writable")

            src_root = Path(src_mp) / (job.source.path or "")
            if not src_root.exists():
                raise _JobError("DISK_GONE", "Source path not found")

            if src_root.is_dir():
                src_files = _collect_files(src_root)
                total = sum(size for _, _, size in src_files)
                job.progress.total = total
                self.store.update(job)

                tmp_dir = Path(dst_mp) / (job.dest.tmp_path or f".storageapp/tmp/{job.id}.tmp")
                final_dir = Path(dst_mp) / (job.dest.relative_path or src_root.name)

                src_hash = _copy_tree_with_hash(self.store, job, src_root, tmp_dir)
                job.integrity.source_sha256 = src_hash
                job.state = "verifying"
                self.store.update(job)

                dest_hash = _hash_tree(tmp_dir)
                job.integrity.dest_sha256 = dest_hash
                if src_hash and src_hash != dest_hash:
                    raise _JobError("CHECKSUM_MISMATCH", "Checksum mismatch")

                if final_dir.exists():
                    raise _JobError("EIO", "Destination already exists")
                final_dir.parent.mkdir(parents=True, exist_ok=True)
                tmp_dir.replace(final_dir)
                job.integrity.verified = True

            else:
                total = src_root.stat().st_size
                job.progress.total = total
                self.store.update(job)

                tmp_file = Path(dst_mp) / (job.dest.tmp_path or f".storageapp/tmp/{job.id}.tmp")
                final_file = Path(dst_mp) / (job.dest.relative_path or src_root.name)

                src_hash = _copy_file_with_hash(self.store, job, src_root, tmp_file)
                job.integrity.source_sha256 = src_hash
                job.state = "verifying"
                self.store.update(job)

                dest_hash = _hash_file(tmp_file)
                job.integrity.dest_sha256 = dest_hash
                if src_hash and src_hash != dest_hash:
                    raise _JobError("CHECKSUM_MISMATCH", "Checksum mismatch")

                if final_file.exists():
                    raise _JobError("EIO", "Destination already exists")
                final_file.parent.mkdir(parents=True, exist_ok=True)
                tmp_file.replace(final_file)
                job.integrity.verified = True

            job.state = "done"
            job.finished_at = time.time()
            self.store.update(job)

        except _JobError as e:
            self._handle_failure(job, e.code, e.message, e.detail)
        except OSError as e:
            code = _map_os_error(e)
            self._handle_failure(job, code, "Copy failed", str(e))
        except Exception as e:
            self._handle_failure(job, "EIO", "Copy failed", str(e))

    def _resolve_disk(self, disk_id: Optional[str]) -> Optional[Any]:
        if not disk_id:
            return None
        return self.resolve_disk(disk_id)

    def _handle_failure(self, job: Job, code: str, message: str, detail: Optional[str]) -> None:
        job.attempts += 1
        error = JobError(code=code, message=message, detail=detail)

        if code == "DISK_GONE":
            job.state = "paused"
            job.last_error = error
            self.store.update(job)
            return

        if code == "CHECKSUM_MISMATCH":
            job.state = "failed"
            job.last_error = error
            job.finished_at = time.time()
            self.store.update(job)
            return

        if job.attempts >= 3:
            job.state = "failed"
            job.last_error = error
            job.finished_at = time.time()
            self.store.update(job)
            return

        backoff = [2, 10, 60][min(job.attempts - 1, 2)]
        job.state = "retrying"
        job.retry_at = time.time() + backoff
        job.last_error = error
        self.store.update(job)


class _JobError(Exception):
    def __init__(self, code: str, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def _collect_files(root: Path) -> list[tuple[str, Path, int]]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            size = path.stat().st_size
            files.append((rel, path, size))
    return files


def _copy_file_with_hash(store: JobStore, job: Job, src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    bytes_done = 0
    start = time.time()

    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        while True:
            chunk = fsrc.read(1024 * 1024)
            if not chunk:
                break
            fdst.write(chunk)
            hasher.update(chunk)
            bytes_done += len(chunk)
            job.progress.bytes_done = bytes_done
            job.progress.speed = bytes_done / max(time.time() - start, 0.001)
            store.update(job)
        fdst.flush()
        os.fsync(fdst.fileno())

    job.integrity.dest_sha256 = hasher.hexdigest()
    return job.integrity.dest_sha256


def _copy_tree_with_hash(store: JobStore, job: Job, src_root: Path, dst_root: Path) -> str:
    dst_root.mkdir(parents=True, exist_ok=True)
    tree_hasher = hashlib.sha256()
    bytes_done = 0
    start = time.time()

    for rel, src, size in _collect_files(src_root):
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        file_hasher = hashlib.sha256()

        with src.open("rb") as fsrc, dst.open("wb") as fdst:
            while True:
                chunk = fsrc.read(1024 * 1024)
                if not chunk:
                    break
                fdst.write(chunk)
                file_hasher.update(chunk)
                bytes_done += len(chunk)
                job.progress.bytes_done = bytes_done
                job.progress.speed = bytes_done / max(time.time() - start, 0.001)
                store.update(job)
            fdst.flush()
            os.fsync(fdst.fileno())

        tree_hasher.update(rel.encode("utf-8"))
        tree_hasher.update(b"\0")
        tree_hasher.update(str(size).encode("utf-8"))
        tree_hasher.update(b"\0")
        tree_hasher.update(file_hasher.digest())

    job.integrity.dest_sha256 = tree_hasher.hexdigest()
    return job.integrity.dest_sha256


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _hash_tree(root: Path) -> str:
    tree_hasher = hashlib.sha256()
    for rel, path, size in _collect_files(root):
        file_hash = _hash_file(path)
        tree_hasher.update(rel.encode("utf-8"))
        tree_hasher.update(b"\0")
        tree_hasher.update(str(size).encode("utf-8"))
        tree_hasher.update(b"\0")
        tree_hasher.update(bytes.fromhex(file_hash))
    return tree_hasher.hexdigest()


def new_copy_job(src_uuid: str, src_rel: str, dst_uuid: str, dst_rel: str) -> Job:
    jid = uuid.uuid4().hex
    now = time.time()
    return Job(
        id=jid,
        type="copy",
        state="queued",
        source=JobSource(kind="disk", path=src_rel, mount_uuid=src_uuid),
        dest=JobDest(
            mount_uuid=dst_uuid,
            relative_path=dst_rel,
            tmp_path=f".storageapp/tmp/{jid}.tmp",
            final_path=dst_rel,
        ),
        progress=JobProgress(bytes_done=0, total=None, speed=None, received_ranges=None),
        integrity=JobIntegrity(),
        attempts=0,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def new_upload_job(dst_uuid: str, dst_rel: str, size: int, sha256: Optional[str]) -> Job:
    jid = uuid.uuid4().hex
    now = time.time()
    return Job(
        id=jid,
        type="upload",
        state="copying",
        source=JobSource(kind="upload", path=None, mount_uuid=None, size=size, sha256=sha256),
        dest=JobDest(
            mount_uuid=dst_uuid,
            relative_path=dst_rel,
            tmp_path=f".storageapp/tmp/{jid}.part",
            final_path=dst_rel,
        ),
        progress=JobProgress(bytes_done=0, total=size, speed=None, received_ranges=[]),
        integrity=JobIntegrity(source_sha256=sha256),
        attempts=0,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def merge_ranges(ranges: List[List[int]], new_range: List[int]) -> List[List[int]]:
    if not ranges:
        return [new_range]
    merged = sorted(ranges + [new_range], key=lambda r: r[0])
    out = [merged[0]]
    for start, end in merged[1:]:
        last = out[-1]
        if start <= last[1] + 1:
            last[1] = max(last[1], end)
        else:
            out.append([start, end])
    return out


def missing_ranges(ranges: List[List[int]], total: int) -> List[List[int]]:
    if not ranges:
        return [[0, total - 1]] if total > 0 else []
    ranges = sorted(ranges, key=lambda r: r[0])
    missing = []
    cursor = 0
    for start, end in ranges:
        if start > cursor:
            missing.append([cursor, start - 1])
        cursor = max(cursor, end + 1)
    if cursor < total:
        missing.append([cursor, total - 1])
    return missing


def _map_os_error(err: OSError) -> str:
    if err.errno is None:
        return "EIO"
    if err.errno in {28}:  # ENOSPC
        return "ENOSPC"
    if err.errno in {13}:  # EACCES
        return "PERM"
    if err.errno in {2}:  # ENOENT
        return "DISK_GONE"
    return "EIO"
