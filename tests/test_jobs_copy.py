import time
from dataclasses import dataclass
from pathlib import Path

from storageapp.services import import_jobs


@dataclass
class DummyDisk:
    dev: str
    mountpoint: str
    fstype: str = "ext4"


def _runner(tmp_path):
    store = import_jobs.JobStore()
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    src_root.mkdir()
    dst_root.mkdir()

    src_disk = DummyDisk(dev="/dev/src", mountpoint=str(src_root))
    dst_disk = DummyDisk(dev="/dev/dst", mountpoint=str(dst_root))

    def resolve_disk(disk_id: str):
        return {"src": src_disk, "dst": dst_disk}.get(disk_id)

    def ensure_mounted(dev, fstype, readonly=False):
        return src_disk.mountpoint, True

    def ensure_writable(dev, fstype):
        return dst_disk.mountpoint, True

    runner = import_jobs.JobRunner(
        store=store,
        resolve_disk=resolve_disk,
        ensure_mounted=ensure_mounted,
        ensure_writable=ensure_writable,
    )
    return store, runner, src_root, dst_root


def test_copy_file_integrity(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store, runner, src_root, dst_root = _runner(tmp_path)

    src_file = src_root / "hello.txt"
    src_file.write_text("hello world", encoding="utf-8")

    job = import_jobs.new_copy_job("src", "hello.txt", "dst", "hello.txt")
    store.create(job)

    runner._run_copy_job(job)
    updated = store.get(job.id)

    assert updated is not None
    assert updated.state == "done"
    assert updated.integrity.verified is True
    assert updated.integrity.source_sha256 == updated.integrity.dest_sha256

    final_file = dst_root / "hello.txt"
    assert final_file.exists()
    assert final_file.read_text(encoding="utf-8") == "hello world"


def test_copy_checksum_mismatch_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store, runner, src_root, _dst_root = _runner(tmp_path)

    src_file = src_root / "hello.txt"
    src_file.write_text("hello world", encoding="utf-8")

    job = import_jobs.new_copy_job("src", "hello.txt", "dst", "hello.txt")
    store.create(job)

    monkeypatch.setattr(import_jobs, "_hash_file", lambda _p: "deadbeef")

    runner._run_copy_job(job)
    updated = store.get(job.id)

    assert updated is not None
    assert updated.state == "failed"
    assert updated.last_error is not None
    assert updated.last_error.code == "CHECKSUM_MISMATCH"


def test_retry_backoff_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store, runner, _src_root, _dst_root = _runner(tmp_path)

    job = import_jobs.new_copy_job("src", "file.txt", "dst", "file.txt")
    store.create(job)

    before = time.time()
    runner._handle_failure(job, "EIO", "Copy failed", "boom")
    updated = store.get(job.id)

    assert updated is not None
    assert updated.state == "retrying"
    assert updated.retry_at is not None
    assert updated.retry_at >= before + 2
