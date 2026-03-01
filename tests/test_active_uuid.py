from storageapp.domain.models import Disk
from storageapp.services.disks import DiskService
from storageapp.services.state import ActiveDiskState
from storageapp.providers.base import DiskProvider


class StaticProvider(DiskProvider):
    def __init__(self, disks):
        self._disks = disks

    def list_disks(self):
        return list(self._disks)

    def ensure_writable(self, dev: str, fstype: str | None):
        disk = next((d for d in self._disks if d.dev == dev), None)
        if not disk:
            return None, False
        return disk.mountpoint, True


def test_set_active_by_uuid(tmp_path):
    d1 = Disk(dev="/dev/sda1", label="USB", fstype="ext4", mountpoint="/media/usb", uuid="UUID-1", supported=True, writable=True)
    d2 = Disk(dev="/dev/sdb1", label="USB2", fstype="ext4", mountpoint="/media/usb2", uuid="UUID-2", supported=True, writable=True)
    provider = StaticProvider([d1, d2])
    state = ActiveDiskState(tmp_path / "state.json")
    service = DiskService(provider=provider, state=state)

    active = service.set_active("UUID-2")
    assert active.dev == "/dev/sdb1"
    assert state.get_active_id() == "UUID-2"

    resolved = service.get_active()
    assert resolved is not None
    assert resolved.dev == "/dev/sdb1"
