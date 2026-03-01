import subprocess

from storageapp.providers import linux_lsblk


def test_already_mounted_uses_lsblk(monkeypatch):
    dev = "/dev/sda1"

    def boom(_cmd):
        raise subprocess.CalledProcessError(
            1,
            ["udisksctl", "mount", "-b", dev],
            output="",
            stderr="GDBus.Error:org.freedesktop.UDisks2.Error.AlreadyMounted: Device "
                   "/dev/sda1 is already mounted at `/media/root/08f4bb02-1234`",
        )

    monkeypatch.setattr(linux_lsblk, "_run", boom)
    monkeypatch.setattr(linux_lsblk, "_lsblk_mountpoint", lambda _dev: "/media/root/08f4bb02-1234")

    mp, ok = linux_lsblk._ensure_mounted(dev, "vfat", readonly=True)
    assert mp == "/media/root/08f4bb02-1234"
    assert ok is True


def test_already_mounted_uses_error_mountpoint(monkeypatch):
    dev = "/dev/sda1"

    def boom(_cmd):
        raise subprocess.CalledProcessError(
            1,
            ["udisksctl", "mount", "-b", dev],
            output="",
            stderr="GDBus.Error:org.freedesktop.UDisks2.Error.AlreadyMounted: Device "
                   "/dev/sda1 is already mounted at `/media/root/AA-BB`",
        )

    monkeypatch.setattr(linux_lsblk, "_run", boom)
    monkeypatch.setattr(linux_lsblk, "_lsblk_mountpoint", lambda _dev: None)

    mp, ok = linux_lsblk._ensure_mounted(dev, "vfat", readonly=True)
    assert mp == "/media/root/AA-BB"
    assert ok is True


def test_already_mounted_writable(monkeypatch):
    dev = "/dev/sda1"

    def boom(_cmd):
        raise subprocess.CalledProcessError(
            1,
            ["udisksctl", "mount", "-b", dev],
            output="",
            stderr="GDBus.Error:org.freedesktop.UDisks2.Error.AlreadyMounted: Device "
                   "/dev/sda1 is already mounted at `/media/root/ZZ-YY`",
        )

    monkeypatch.setattr(linux_lsblk, "_run", boom)
    monkeypatch.setattr(linux_lsblk, "_lsblk_mountpoint", lambda _dev: "/media/root/ZZ-YY")
    monkeypatch.setattr(linux_lsblk, "_test_writable", lambda _mp: True)

    mp, ok = linux_lsblk._ensure_mounted_and_writable(dev, "ext4")
    assert mp == "/media/root/ZZ-YY"
    assert ok is True


def test_polkit_error_raises(monkeypatch):
    dev = "/dev/sda1"

    def boom(_cmd):
        raise subprocess.CalledProcessError(
            1,
            ["udisksctl", "mount", "-b", dev],
            output="",
            stderr="GDBus.Error:org.freedesktop.UDisks2.Error.NotAuthorized: "
                   "Not authorized to perform operation",
        )

    monkeypatch.setattr(linux_lsblk, "_run", boom)

    try:
        linux_lsblk._ensure_mounted_and_writable(dev, "ext4")
    except RuntimeError as e:
        assert "polkit" in str(e).lower()
    else:
        assert False, "Expected RuntimeError for polkit error"
