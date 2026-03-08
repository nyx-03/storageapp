from __future__ import annotations
import json
import os
import subprocess
import logging
import re
import shutil
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
    _run(["udisksctl", "unmount", "-b", dev])


def _udisks_mount(dev: str, options: str | None = None) -> str:
    cmd = ["udisksctl", "mount", "-b", dev]
    if options:
        cmd += ["-o", options]
    out = _run(cmd)
    return _parse_mountpoint_from_udisksctl(out)


def _ensure_mounted_and_writable(dev: str, fstype: str | None) -> tuple[str | None, bool]:
    """
    Essaie de garantir que la partition est montée et écrivable par l'utilisateur du process
    (ici: storageapp via systemd). Pas d'options de montage imposées.
    """
    try:
        mp = _udisks_mount(dev)
    except Exception as e:
        if _is_already_mounted_error(e):
            mp = _lsblk_mountpoint(dev) or _mountpoint_from_error(e)
            if mp:
                if _test_writable(mp):
                    logger.info("device already mounted (using existing mountpoint): %s -> %s", dev, mp)
                    return mp, True
                logger.warning("device already mounted but not writable, attempting remount: %s", dev)
                try:
                    _udisks_unmount(dev)
                    mp = _udisks_mount(dev)
                    return mp, _test_writable(mp)
                except Exception as e2:
                    logger.warning("remount failed for %s: %s", dev, e2)
                    return mp, False
        if _is_polkit_error(e):
            logger.warning("udisksctl mount requires polkit authorization for %s", dev)
            raise RuntimeError(_polkit_message())
        logger.warning("udisksctl mount failed (%s) for %s", e, dev)
        return None, False

    return mp, _test_writable(mp)


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        logger.error(
            "Command failed: %s rc=%s stdout=%r stderr=%r",
            cmd,
            e.returncode,
            e.stdout,
            e.stderr,
        )
        raise
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


def _writable_hint(mountpoint: str) -> bool:
    return os.access(mountpoint, os.W_OK)


def _test_readable(mountpoint: str) -> bool:
    try:
        next(Path(mountpoint).iterdir(), None)
        return True
    except Exception:
        return False


def _error_text(err: Exception) -> str:
    text = []
    if isinstance(err, subprocess.CalledProcessError):
        if err.stdout:
            text.append(str(err.stdout))
        if err.stderr:
            text.append(str(err.stderr))
    text.append(str(err))
    return "\n".join([t for t in text if t])


def _is_already_mounted_error(err: Exception) -> bool:
    msg = _error_text(err)
    return "AlreadyMounted" in msg or "already mounted" in msg


def _is_polkit_error(err: Exception) -> bool:
    msg = _error_text(err)
    markers = [
        "NotAuthorized",
        "AuthenticationRequired",
        "not authorized",
        "authentication required",
        "filesystem-mount-other-seat",
    ]
    return any(m in msg for m in markers)


def _polkit_message() -> str:
    return "Mount requires polkit authorization; configure polkit for storageapp"


def _mountpoint_from_error(err: Exception) -> str | None:
    msg = _error_text(err)
    m = re.search(r"[`'\"](?P<path>/[^`'\"]+)[`'\"]", msg)
    if m:
        return m.group("path")
    return None


def _lsblk_mountpoint(dev: str) -> str | None:
    try:
        proc = subprocess.run(
            ["lsblk", "-no", "MOUNTPOINT", dev],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            logger.warning("lsblk mountpoint failed for %s: %s", dev, proc.stderr)
            return None
        out = (proc.stdout or "").strip()
        return out or None
    except Exception as e:
        logger.warning("lsblk mountpoint exception for %s: %s", dev, e)
        return None


def _belongs_to_disk(part_name: str, disk_name: str) -> bool:
    if not part_name.startswith(disk_name):
        return False
    suffix = part_name[len(disk_name):]
    if not suffix:
        return False
    if suffix.startswith("p"):
        suffix = suffix[1:]
    return suffix.isdigit()


def _select_usb_partitions(data: dict) -> list[Disk]:
    blockdevices = data.get("blockdevices", []) if isinstance(data, dict) else []
    logger.info("lsblk blockdevices=%s", len(blockdevices))

    usb_disks: dict[str, dict] = {}
    parts: list[Disk] = []
    ignored = 0

    def walk(node: dict, current_disk: Optional[dict] = None):
        nonlocal ignored
        node_type = node.get("type")
        if node_type in {"loop", "zram"}:
            ignored += 1
            return
        if node_type == "disk":
            current_disk = node
            if node.get("tran") == "usb":
                usb_disks[node.get("name")] = node
        elif node_type == "part":
            name = node.get("name")
            if not name:
                return
            fstype = (node.get("fstype") or "").lower() or None
            mp = node.get("mountpoint")
            parent_name = node.get("pkname") or (current_disk.get("name") if current_disk else None)
            is_usb = bool(parent_name and parent_name in usb_disks)
            if not is_usb:
                logger.info("lsblk ignore part=%s reason=parent_not_usb", name)
                ignored += 1
                return
            parent_disk = usb_disks.get(parent_name) if parent_name else None
            disk_src = current_disk or parent_disk

            disk = Disk(
                dev=f"/dev/{name}",
                label=node.get("label") or f"/dev/{name}",
                fstype=fstype,
                size=node.get("size"),
                mountpoint=mp,
                tran=disk_src.get("tran") if disk_src else None,
                rm=bool(disk_src.get("rm")) if disk_src and disk_src.get("rm") is not None else None,
            )
            disk.parent_dev = f"/dev/{parent_name}" if parent_name else None
            disk.is_usb = True
            disk.is_system = _is_system_mount(mp)
            disk.uuid = node.get("uuid")
            disk.partuuid = node.get("partuuid")
            disk.supported = (fstype in SUPPORTED_FS) if fstype else False
            disk.writable = _writable_hint(mp) if mp else None
            if disk.is_system:
                logger.info("lsblk ignore part=%s reason=system_mount", name)
                ignored += 1
                return
            parts.append(disk)

        for ch in node.get("children") or []:
            walk(ch, current_disk=current_disk)

    for dev in blockdevices:
        walk(dev, current_disk=None)

    # Fallback: if lsblk did not include children, match parts to usb disks by name.
    if usb_disks:
        part_names = {p.dev for p in parts}
        for dev in blockdevices:
            if dev.get("type") != "part":
                continue
            name = dev.get("name")
            if not name:
                continue
            if f"/dev/{name}" in part_names:
                continue
            parent_name = dev.get("pkname") or next((d for d in usb_disks.keys() if _belongs_to_disk(name, d)), None)
            if not parent_name or parent_name not in usb_disks:
                logger.info("lsblk ignore part=%s reason=parent_not_usb", name)
                ignored += 1
                continue
            fstype = (dev.get("fstype") or "").lower() or None
            mp = dev.get("mountpoint")
            disk = Disk(
                dev=f"/dev/{name}",
                label=dev.get("label") or f"/dev/{name}",
                fstype=fstype,
                size=dev.get("size"),
                mountpoint=mp,
                tran=usb_disks[parent_name].get("tran"),
                rm=bool(usb_disks[parent_name].get("rm")) if usb_disks[parent_name].get("rm") is not None else None,
            )
            disk.parent_dev = f"/dev/{parent_name}"
            disk.is_usb = True
            disk.is_system = _is_system_mount(mp)
            disk.uuid = dev.get("uuid")
            disk.partuuid = dev.get("partuuid")
            disk.supported = (fstype in SUPPORTED_FS) if fstype else False
            disk.writable = _writable_hint(mp) if mp else None
            if disk.is_system:
                logger.info("lsblk ignore part=%s reason=system_mount", name)
                ignored += 1
                continue
            parts.append(disk)

    logger.info("lsblk usb_disks=%s", len(usb_disks))
    logger.info("lsblk usb_partitions=%s ignored=%s", len(parts), ignored)

    if any(p.fstype == "ntfs" for p in parts):
        if shutil.which("ntfs-3g") is None:
            logger.warning("ntfs-3g not found: NTFS volumes may not mount")

    return parts


def _ensure_mounted(dev: str, fstype: str | None, readonly: bool = False) -> tuple[str | None, bool]:
    opts = []
    if readonly:
        opts.append("ro")
    options = ",".join(opts) if opts else None

    try:
        mp = _udisks_mount(dev, options=options)
    except Exception as e:
        if _is_already_mounted_error(e):
            mp = _lsblk_mountpoint(dev) or _mountpoint_from_error(e)
            if mp:
                logger.info("device already mounted (using existing mountpoint): %s -> %s", dev, mp)
                return mp, _test_readable(mp)
        if _is_polkit_error(e):
            logger.warning("udisksctl mount requires polkit authorization for %s", dev)
            raise RuntimeError(_polkit_message())
        logger.warning("udisksctl mount failed (%s) for %s (readonly=%s)", e, dev, readonly)
        try:
            mp = _udisks_mount(dev)
        except Exception as e2:
            if _is_already_mounted_error(e2):
                mp = _lsblk_mountpoint(dev) or _mountpoint_from_error(e2)
                if mp:
                    logger.info("device already mounted (using existing mountpoint): %s -> %s", dev, mp)
                    return mp, _test_readable(mp)
            if _is_polkit_error(e2):
                logger.warning("udisksctl mount requires polkit authorization for %s", dev)
                raise RuntimeError(_polkit_message())
            logger.warning("udisksctl mount fallback failed (%s) for %s (readonly=%s)", e2, dev, readonly)
            return None, False

    return mp, _test_readable(mp)


class LinuxLsblkProvider(DiskProvider):
    def list_disks(self) -> List[Disk]:
        try:
            out = _run(["lsblk", "--json", "-o", "NAME,TYPE,FSTYPE,LABEL,SIZE,MOUNTPOINT,TRAN,RM,UUID,PARTUUID,PKNAME"])
            data = json.loads(out)
        except Exception:
            logger.exception("Failed to list disks via lsblk")
            return []
        return _select_usb_partitions(data)

    def ensure_writable(self, dev: str, fstype: str | None) -> tuple[str | None, bool]:
        return _ensure_mounted_and_writable(dev, fstype)

    def ensure_mounted(self, dev: str, fstype: str | None, readonly: bool = False) -> tuple[str | None, bool]:
        return _ensure_mounted(dev, fstype, readonly=readonly)

    def unmount(self, dev: str) -> bool:
        try:
            _run(["udisksctl", "unmount", "-b", dev])
            return True
        except Exception:
            logger.exception("udisksctl unmount failed for %s", dev)
            return False
