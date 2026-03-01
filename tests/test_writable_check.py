from storageapp.providers import linux_lsblk


def test_writable_check_failure(monkeypatch):
    monkeypatch.setattr(linux_lsblk, "_udisks_mount", lambda _dev, options=None: "/media/test")
    monkeypatch.setattr(linux_lsblk, "_test_writable", lambda _mp: False)

    mp, ok = linux_lsblk._ensure_mounted_and_writable("/dev/sda1", "ext4")
    assert mp == "/media/test"
    assert ok is False
