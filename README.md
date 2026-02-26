StorageApp
==========

Application web locale (LAN) pour:
- envoyer des fichiers via Wi‑Fi vers un Raspberry Pi,
- lancer un import depuis une carte SD vers un disque USB.

Prérequis
---------
- Python 3.11+
- Outils système sur le Pi: `lsblk`, `udisksctl`, `rsync`

Installation
------------
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Lancement
---------
```bash
uvicorn storageapp.main:app --host 0.0.0.0 --port 8000
```
Puis ouvrir `http://<ip-du-pi>:8000/` dans un navigateur.

Variables d’environnement
-------------------------
- `STORAGEAPP_ENV`: `dev` (par défaut) ou `pi`
- `STORAGEAPP_STATE_FILE`: chemin du fichier d’état (défaut `./.state/state.json`)
- `STORAGEAPP_JOBS_FILE`: chemin du fichier de jobs d’import (défaut `/var/lib/storageapp/import_jobs.json`)
- `STORAGEAPP_MAX_UPLOAD_MB`: taille max d’upload (défaut `4096`)
 
En production sur Raspberry Pi, définir `STORAGEAPP_ENV=pi` pour activer la détection réelle des disques USB.

Sudoers (arrêt du Pi)
---------------------
Pour autoriser l’endpoint d’extinction, ajouter une règle sudoers pour
l’utilisateur `storageapp`:
```
storageapp ALL=NOPASSWD:/bin/systemctl poweroff,/sbin/shutdown -h now
```

Tests
-----
```bash
pip install -r requirements-dev.txt
pytest
```

Dépannage
---------
- Si aucun disque n’apparaît: vérifier `lsblk` et que le disque est bien USB.
- Si montage impossible: vérifier `udisksctl` et les permissions.
- Si import échoue: vérifier que `rsync` est installé.
