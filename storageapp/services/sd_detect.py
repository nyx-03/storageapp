from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any

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


def find_media_sources(max_depth: int = 3) -> List[Dict[str, Any]]:
    """
    Retourne des chemins "probables" de cartes SD / supports amovibles,
    en se basant sur la présence de dossiers signatures.
    """
    sources = []
    for root in CANDIDATE_ROOTS:
        if not root.exists():
            continue

        # /media/<user>/<label>
        # Certaines cartes ont un sous-dossier avant DCIM/INSTA360, on regarde donc aussi un niveau plus bas.
        candidates = []
        for base in root.glob("*/*"):
            if not base.is_dir():
                continue
            candidates.append(base)
            # Un niveau plus bas (rapide)
            for child in base.iterdir():
                if child.is_dir():
                    candidates.append(child)

        seen = set()
        for base in candidates:
            b = str(base)
            if b in seen:
                continue
            seen.add(b)

            found = []
            for sig in SIGNATURE_DIRS_PRIORITY:
                if (base / sig).is_dir():
                    found.append(sig)

            if not found:
                continue

            # Chemin recommandé : on prend la signature la plus prioritaire
            recommended = str(base / found[0])

            sources.append({
                "path": str(base),
                "signatures": found,
                "recommended_path": recommended,
            })
    return sorted(sources, key=lambda s: s.get("path", ""))