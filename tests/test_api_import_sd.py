import importlib

from fastapi.testclient import TestClient


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGEAPP_ENV", "dev")
    monkeypatch.setenv("STORAGEAPP_JOBS_FILE", str(tmp_path / "jobs.json"))
    import storageapp.main as main
    importlib.reload(main)
    return main


def test_import_sd_rejects_unknown_source(monkeypatch, tmp_path):
    main = _load_app(monkeypatch, tmp_path)
    allowed = tmp_path / "sd"
    allowed.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.setattr(main, "find_media_sources", lambda: [
        {"path": str(allowed), "recommended_path": str(allowed)},
    ])

    client = TestClient(main.app)
    r = client.post("/api/disks/active", json={"dev": "/dev/sda1"})
    assert r.status_code == 200

    r = client.post("/api/import-sd", json={"source_path": str(other), "ignore_existing": False})
    assert r.status_code == 400


def test_import_sd_rejects_when_running(monkeypatch, tmp_path):
    main = _load_app(monkeypatch, tmp_path)
    allowed = tmp_path / "sd"
    allowed.mkdir()

    monkeypatch.setattr(main, "find_media_sources", lambda: [
        {"path": str(allowed), "recommended_path": str(allowed)},
    ])

    def boom(*_args, **_kwargs):
        raise main.ImportBusyError("busy")

    monkeypatch.setattr(main.import_store, "create_if_available", boom)
    client = TestClient(main.app)
    r = client.post("/api/disks/active", json={"dev": "/dev/sda1"})
    assert r.status_code == 200
    r = client.post("/api/import-sd", json={"source_path": str(allowed), "ignore_existing": False})
    assert r.status_code == 409
