from __future__ import annotations
from typing import List
from storageapp.domain.models import Disk
from storageapp.providers.base import DiskProvider


class MockDiskProvider(DiskProvider):
    def list_disks(self) -> List[Disk]:
        # Simule une clé USB FAT32 montée et un disque exFAT non monté
        return [
            Disk(
                dev="/dev/sda1",
                label="E0C9-6E4A",
                fstype="vfat",
                size="14.6G",
                mountpoint="/media/user/E0C9-6E4A",
                tran="usb",
                rm=True,
                supported=True,
                writable=True,
                is_system=False,
            ),
            Disk(
                dev="/dev/sdb1",
                label="TOSHIBA",
                fstype="exfat",
                size="931G",
                mountpoint=None,
                tran="usb",
                rm=False,
                supported=True,
                writable=False,
                is_system=False,
            ),
        ]

    def ensure_writable(self, dev: str, fstype: str | None):
        # Dev mode: on considère OK
        d = next((x for x in self.list_disks() if x.dev == dev), None)
        if not d:
            return None, False
        return d.mountpoint, True

    def ensure_mounted(self, dev: str, fstype: str | None, readonly: bool = False):
        d = next((x for x in self.list_disks() if x.dev == dev), None)
        if not d:
            return None, False
        return d.mountpoint, True

    def unmount(self, dev: str) -> bool:
        return True
