from storageapp.providers.linux_lsblk import _select_usb_partitions


def test_usb_disk_children_without_tran_on_part():
    data = {
        "blockdevices": [
            {
                "name": "sda",
                "type": "disk",
                "tran": "usb",
                "rm": True,
                "children": [
                    {
                        "name": "sda1",
                        "type": "part",
                        "fstype": "ntfs",
                        "label": "TOSHIBA EXT",
                        "size": "931G",
                        "mountpoint": None,
                        "tran": None,
                        "rm": None,
                    }
                ],
            }
        ]
    }

    parts = _select_usb_partitions(data)
    assert len(parts) == 1
    assert parts[0].dev == "/dev/sda1"
    assert parts[0].parent_dev == "/dev/sda"
    assert parts[0].is_usb is True


def test_non_usb_disk_ignored():
    data = {
        "blockdevices": [
            {
                "name": "sdb",
                "type": "disk",
                "tran": "sata",
                "rm": False,
                "children": [
                    {
                        "name": "sdb1",
                        "type": "part",
                        "fstype": "ext4",
                        "label": "DATA",
                        "size": "100G",
                        "mountpoint": None,
                    }
                ],
            }
        ]
    }

    parts = _select_usb_partitions(data)
    assert parts == []


def test_usb_disk_part_without_fstype_included():
    data = {
        "blockdevices": [
            {
                "name": "sdc",
                "type": "disk",
                "tran": "usb",
                "rm": True,
                "children": [
                    {
                        "name": "sdc1",
                        "type": "part",
                        "fstype": None,
                        "label": None,
                        "size": "14G",
                        "mountpoint": None,
                    }
                ],
            }
        ]
    }

    parts = _select_usb_partitions(data)
    assert len(parts) == 1
    assert parts[0].dev == "/dev/sdc1"
    assert parts[0].supported is False
