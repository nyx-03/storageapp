import hashlib
import importlib

from fastapi.testclient import TestClient

from storageapp.domain.models import Disk
from storageapp.providers.base import DiskProvider
from storageapp.services.disks import DiskService
from storageapp.services.state import ActiveDiskState
from storageapp.services import import_jobs


class StaticProvider(DiskProvider):
    def __init__(self, disk):
        self._disk = disk

    def list_disks(self):
        return [self._disk]

    def ensure_writable(self, dev, fstype):
        if dev != self._disk.dev:
            return None, False
        return self._disk.mountpoint, True

    def ensure_mounted(self, dev, fstype, readonly=False):
        if dev != self._disk.dev:
            return None, False
        return self._disk.mountpoint, True


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGEAPP_ENV", "dev")
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setenv("STORAGEAPP_STATE_FILE", str(tmp_path / "state.json"))
    import storageapp.main as main
    importlib.reload(main)

    mnt = tmp_path / "mnt"
    mnt.mkdir()
    disk = Disk(
        dev="/dev/sda1",
        label="disk",
        fstype="ext4",
        size="10G",
        mountpoint=str(mnt),
        supported=True,
        writable=True,
        uuid="UUID-1",
        partuuid="PART-1",
    )
    provider = StaticProvider(disk)
    state = ActiveDiskState(tmp_path / "state.json")
    state.set_active_id(disk.uuid)
    service = DiskService(provider=provider, state=state)

    main.provider = provider
    main.state = state
    main.service = service
    main.job_store = import_jobs.JobStore()
    main.job_runner = import_jobs.JobRunner(
        store=main.job_store,
        resolve_disk=service.resolve_disk,
        ensure_mounted=provider.ensure_mounted,
        ensure_writable=provider.ensure_writable,
    )
    return main


def test_resumable_upload_success(monkeypatch, tmp_path):
    main = _load_app(monkeypatch, tmp_path)
    client = TestClient(main.app)

    content = b"hello world"
    total = len(content)
    sha = hashlib.sha256(content).hexdigest()

    r = client.post("/api/uploads/init", json={
        "filename": "hello.txt",
        "size": total,
        "sha256": sha,
        "dir": "uploads",
    })
    assert r.status_code == 200
    upload_id = r.json()["upload_id"]

    r = client.put(
        f"/api/uploads/{upload_id}",
        data=content[:5],
        headers={"Content-Range": f"bytes 0-4/{total}"},
    )
    assert r.status_code == 200

    r = client.get(f"/api/uploads/{upload_id}")
    assert r.status_code == 200
    assert r.json()["missing_ranges"] == [[5, total - 1]]

    r = client.put(
        f"/api/uploads/{upload_id}",
        data=content[5:],
        headers={"Content-Range": f"bytes 5-{total - 1}/{total}"},
    )
    assert r.status_code == 200

    r = client.post(f"/api/uploads/{upload_id}/finalize")
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["state"] == "done"
    assert job["integrity"]["verified"] is True

    final_file = tmp_path / "mnt" / "uploads" / "hello.txt"
    assert final_file.exists()
    assert final_file.read_bytes() == content


def test_resumable_upload_checksum_mismatch(monkeypatch, tmp_path):
    main = _load_app(monkeypatch, tmp_path)
    client = TestClient(main.app)

    content = b"hello world"
    total = len(content)
    bad_sha = hashlib.sha256(b"other").hexdigest()

    r = client.post("/api/uploads/init", json={
        "filename": "hello.txt",
        "size": total,
        "sha256": bad_sha,
        "dir": "uploads",
    })
    assert r.status_code == 200
    upload_id = r.json()["upload_id"]

    r = client.put(
        f"/api/uploads/{upload_id}",
        data=content,
        headers={"Content-Range": f"bytes 0-{total - 1}/{total}"},
    )
    assert r.status_code == 200

    r = client.post(f"/api/uploads/{upload_id}/finalize")
    assert r.status_code == 400

    status = client.get(f"/api/uploads/{upload_id}").json()["job"]
    assert status["state"] == "failed"
