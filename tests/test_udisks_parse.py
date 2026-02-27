import pytest

from storageapp.providers.linux_lsblk import _parse_mountpoint_from_udisksctl


def test_parse_mountpoint_media():
    out = "Mounted /dev/sda1 at /media/storageapp/MYLABEL."
    assert _parse_mountpoint_from_udisksctl(out) == "/media/storageapp/MYLABEL"


def test_parse_mountpoint_run_media():
    out = "Mounted /dev/sda1 at /run/media/storageapp/MYLABEL."
    assert _parse_mountpoint_from_udisksctl(out) == "/run/media/storageapp/MYLABEL"


def test_parse_mountpoint_with_quotes():
    out = "Mounted /dev/sda1 at \"/run/media/storageapp/My Drive\"."
    assert _parse_mountpoint_from_udisksctl(out) == "/run/media/storageapp/My Drive"


def test_parse_mountpoint_with_parenthesis_no_dot():
    out = "Mounted /dev/sda1 at /run/media/storageapp/My (USB) Drive"
    assert _parse_mountpoint_from_udisksctl(out) == "/run/media/storageapp/My (USB) Drive"


def test_parse_mountpoint_unexpected():
    out = "Something went wrong"
    with pytest.raises(RuntimeError) as exc:
        _parse_mountpoint_from_udisksctl(out)
    assert "Mountpoint not found" in str(exc.value)
