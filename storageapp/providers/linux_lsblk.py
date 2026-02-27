from __future__ import annotations
import json
import os
import subprocess
import logging
import re
from typing import List, Optional
from pathlib import Path
import pwd
import grp

from storageapp.domain.models import Disk
from storageapp.providers.base import DiskProvider

SUPPORTED_FS = {"exfat", "vfat", "ntfs", "ext4"}
logger = logging.getLogger(__name__)

def _uid_gid_for_user(username: str) -> tuple[int, int]:
    pw = pwd.getpwnam(username)
    return pw.pw_uid, pw.pw_gid

def _parse_mountpoint_from_udisksctl(output: str) -> str:
    # Exemple: "Mounted /dev/sda1 at /media/storageapp/E0C9-6E4A."
    # Exemple: "Mounted /dev/sda1 at /run/media/storageapp/MYLABEL."
    # Exemple: "Mounted /dev/sda1 at \"/run/media/storageapp/My Drive\"."
    mount_re = re.compile(r"\bat\s+(?P<path>\"[^\"]+\"|'[^']+'|/.*?)(?:\.\s*|$)")
    m = mount_re.search(output)
    if not m:
        raise RuntimeError(f"Mountpoint not found in: {output}")
    path = m.group("path").strip().strip("'\"").rstrip(".").strip()
    if not path.startswith("/"):
        raise RuntimeError(f"Mountpoint not absolute in: {output}")
    if not (path.startswith("/media/") or path.startswith("/run/media/")):
        logger.warning("Mountpoint outside expected roots: %s", path)
    return path


def _udisks_unmount(dev: str) -> None:
    subprocess.run(["udisksctl", "unmount", "-b", dev], capture_output=True, text=True)


def _udisks_mount(dev: str, options: str | None = None) -> str:
    cmd = ["udisksctl", "mount", "-b", dev]
    if options:
        cmd += ["-o", options]
    out = _run(cmd)
    return _parse_mountpoint_from_udisksctl(out)


def _ensure_mounted_and_writable(dev: str, fstype: str | None) -> tuple[str | None, bool]:
    """
    Essaie de garantir que la partition est montée et écrivable par l'utilisateur du process
    (ici: storageapp via systemd).
    """
    fs = (fstype or "").lower()

    # Pour les FS non-POSIX : on force uid/gid/umask
    # vfat = FAT32, exfat, ntfs
    if fs in {"vfat", "exfat", "ntfs"}:
        uid = os.getuid()
        gid = os.getgid()
        opts = f"uid={uid},gid={gid},umask=0022"

        # Si déjà monté ailleurs (ex: /media/nyx-03/...), on démonte puis on remonte proprement.
        _udisks_unmount(dev)

        try:
            mp = _udisks_mount(dev, options=opts)
        except Exception:
            # fallback: tentative sans options
            try:
                mp = _udisks_mount(dev)
            except Exception:
                return None, False

        return mp, _test_writable(mp)

    # FS POSIX (ext4, etc.)
    # On ne force pas les permissions via options (ça ne marche pas comme FAT).
    # On tente juste de monter si besoin, puis test writable.
    try:
        mp = _udisks_mount(dev)
    except Exception:
        mp = None

    if mp:
        return mp, _test_writable(mp)
    return None, False


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except Exception:
        logger.exception("Command failed: %s", cmd)
        raise


def _is_system_mount(mp: Optional[str]) -> bool:
    return mp in ("/", "/boot", "/boot/firmware")


def _test_writable(mountpoint: str) -> bool:
    try:
        p = Path(mountpoint) / ".storageapp_write_test"
        p.write_text("ok", encoding="utf-8")
        p.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _test_readable(mountpoint: str) -> bool:
    try:
        next(Path(mountpoint).iterdir(), None)
        return True
    except Exception:
        return False


def _ensure_mounted(dev: str, fstype: str | None, readonly: bool = False) -> tuple[str | None, bool]:
    fs = (fstype or "").lower()
    opts = []

    if readonly:
        opts.append("ro")

    if fs in {"vfat", "exfat", "ntfs"}:
        uid = os.getuid()
        gid = os.getgid()
        opts.append(f"uid={uid},gid={gid},umask=0022")

    options = ",".join(opts) if opts else None

    try:
        mp = _udisks_mount(dev, options=options)
    except Exception as e:
        logger.warning("udisksctl mount failed (%s) for %s (readonly=%s)", e, dev, readonly)
        try:
            mp = _udisks_mount(dev)
        except Exception as e2:
            logger.warning("udisksctl mount fallback failed (%s) for %s (readonly=%s)", e2, dev, readonly)
            return None, False

    return mp, _test_readable(mp)


class LinuxLsblkProvider(DiskProvider):
    def list_disks(self) -> List[Disk]:
        try:
            out = _run(["lsblk", "--json", "-o", "NAME,TYPE,FSTYPE,LABEL,SIZE,MOUNTPOINT,TRAN,RM"])
            data = json.loads(out)
        except Exception:
            logger.exception("Failed to list disks via lsblk")
            return []

        parts: list[Disk] = []

        def walk(node: dict, inherited_tran=None, inherited_rm=None):
            current_tran = node.get("tran") or inherited_tran
            current_rm = node.get("rm")
            if current_rm is None:
                current_rm = inherited_rm

            # On ne s’intéresse qu’aux partitions
            if node.get("type") == "part":
                name = node.get("name")
                fstype = (node.get("fstype") or "").lower() or None
                mp = node.get("mountpoint")

                disk = Disk(
                    dev=f"/dev/{name}",
                    label=node.get("label") or f"/dev/{name}",
                    fstype=fstype,
                    size=node.get("size"),
                    mountpoint=mp,
                    tran=current_tran,
                    rm=bool(current_rm) if current_rm is not None else None,
                )
                disk.is_system = _is_system_mount(mp)
                disk.supported = (fstype in SUPPORTED_FS)
                disk.writable = bool(mp) and _test_writable(mp)
                parts.append(disk)

            for ch in node.get("children") or []:
                walk(ch, inherited_tran=current_tran, inherited_rm=current_rm)

        for dev in data.get("blockdevices", []):
            walk(dev)

        # Filtrage : on garde uniquement les supports USB “utiles”
        # (disques + clés USB montés automatiquement via udisks2)
        usb = [d for d in parts if (d.tran == "usb") and (not d.is_system)]
        return usb

    def ensure_writable(self, dev: str, fstype: str | None) -> tuple[str | None, bool]:
        return _ensure_mounted_and_writable(dev, fstype)

    def ensure_mounted(self, dev: str, fstype: str | None, readonly: bool = False) -> tuple[str | None, bool]:
        return _ensure_mounted(dev, fstype, readonly=readonly)
