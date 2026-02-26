from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any
import os

# On cherche des montages probables (Pi OS + udisks)
CANDIDATE_ROOTS = [
    Path("/media"),
    Path("/run/media"),
]

# Signatures typiques (Insta360 / APN)
# Ordre de priorité pour choisir un chemin recommandé
SIGNATURE_DIRS_PRIORITY = [
    "INSTA360",  # Insta360
    "DCIM",      # APN/GoPro, etc.
    "PRIVATE",   # certains APN/caméscopes
]


def _candidate_mounts(root: Path) -> List[Path]:
    candidates: List[Path] = []
    for base in root.iterdir():
        if base.is_dir():
            candidates.append(base)
    for base in root.glob("*/*"):
        if base.is_dir():
            candidates.append(base)

    seen = set()
    uniq: List[Path] = []
    for base in candidates:
        b = str(base)
        if b in seen:
            continue
        seen.add(b)
        uniq.append(base)

    mounts = [p for p in uniq if os.path.ismount(p)]
    return mounts or uniq


def recommended_path_for(base: Path) -> tuple[str, List[str]]:
    found = []
    for sig in SIGNATURE_DIRS_PRIORITY:
        if (base / sig).is_dir():
            found.append(sig)
    recommended = str(base / found[0]) if found else str(base)
    return recommended, found


def find_media_sources(max_depth: int = 3) -> List[Dict[str, Any]]:
    """
    Retourne des chemins de supports montés (USB y compris),
    et si possible un chemin recommandé basé sur des signatures connues.
    """
    sources = []
    for root in CANDIDATE_ROOTS:
        if not root.exists():
            continue

        for base in _candidate_mounts(root):
            recommended, found = recommended_path_for(base)
            sources.append({
                "path": str(base),
                "signatures": found,
                "recommended_path": recommended,
            })
    return sorted(sources, key=lambda s: s.get("path", ""))
