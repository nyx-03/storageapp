from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class Disk(BaseModel):
    dev: str                  # ex: /dev/sda1
    label: str
    fstype: Optional[str] = None
    size: Optional[str] = None
    mountpoint: Optional[str] = None
    tran: Optional[str] = None       # usb, mmc, sata...
    rm: Optional[bool] = None        # removable
    supported: bool = False
    writable: Optional[bool] = None
    is_system: bool = False
    parent_dev: Optional[str] = None
    is_usb: Optional[bool] = None
    uuid: Optional[str] = None
    partuuid: Optional[str] = None
    total_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
