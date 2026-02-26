from storageapp.services.disks import DiskService
from storageapp.services.state import ActiveDiskState
from storageapp.providers.base import DiskProvider


class DummyProvider(DiskProvider):
    def list_disks(self):
        return []


def test_safe_filename_strips_traversal(tmp_path):
    service = DiskService(provider=DummyProvider(), state=ActiveDiskState(tmp_path / "state.json"))
    assert service._safe_filename("../etc/passwd") == "passwd"


def test_safe_filename_normalizes_chars(tmp_path):
    service = DiskService(provider=DummyProvider(), state=ActiveDiskState(tmp_path / "state.json"))
    assert service._safe_filename("weird name##.mp4") == "weird_name_.mp4"
