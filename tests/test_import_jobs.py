import pathlib

import pytest

from storageapp.services import import_jobs


def test_has_running(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store = import_jobs.ImportJobStore()
    job = store.create("src", "dst")
    assert store.has_running() is False

    job.status = "running"
    store.update(job)
    assert store.has_running() is True


def test_create_rolls_back_on_save_error(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store = import_jobs.ImportJobStore()

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(import_jobs, "_atomic_write_json", boom)

    with pytest.raises(OSError):
        store.create("src", "dst")

    assert store.list() == []


def test_run_rsync_job_marks_failed_on_mkdir_error(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store = import_jobs.ImportJobStore()
    job = store.create(str(tmp_path / "src"), str(tmp_path / "dst"))

    def boom(*_args, **_kwargs):
        raise OSError("mkdir failed")

    monkeypatch.setattr(pathlib.Path, "mkdir", boom)

    import_jobs.run_rsync_job(store, job.id)
    updated = store.get(job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert "mkdir failed" in (updated.error or "")


def test_run_rsync_job_marks_failed_on_popen_error(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    store = import_jobs.ImportJobStore()
    job = store.create(str(tmp_path / "src"), str(tmp_path / "dst"))

    def boom(*_args, **_kwargs):
        raise FileNotFoundError("rsync missing")

    monkeypatch.setattr(import_jobs.subprocess, "Popen", boom)

    import_jobs.run_rsync_job(store, job.id)
    updated = store.get(job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert "rsync missing" in (updated.error or "")
