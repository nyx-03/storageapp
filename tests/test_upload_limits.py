import io

class DummyUpload:
    def __init__(self, filename, file, content_type):
        self.filename = filename
        self.file = file
        self.content_type = content_type

from storageapp.domain.models import Disk
from storageapp.providers.base import DiskProvider
from storageapp.services.disks import DiskService
from storageapp.services.state import ActiveDiskState


class StaticProvider(DiskProvider):
    def __init__(self, disk):
        self._disk = disk

    def list_disks(self):
        return [self._disk]


def test_upload_limit_enforced(tmp_path):
    disk = Disk(
        dev="dev1",
        label="disk",
        mountpoint=str(tmp_path),
        supported=True,
        writable=True,
    )
    provider = StaticProvider(disk)
    state = ActiveDiskState(tmp_path / "state.json")
    state.set_active_dev("dev1")
    service = DiskService(provider=provider, state=state)

    data = io.BytesIO(b"0123456789")
    upload = DummyUpload(filename="test.txt", file=data, content_type="text/plain")

    result = service.save_uploads([upload], max_total_bytes=5)
    assert result["saved"] == []
    assert len(result["errors"]) == 1
    assert "Upload size limit exceeded" in result["errors"][0]["error"]
