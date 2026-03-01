# StorageApp

StorageApp est une application **FastAPI** conçue pour un **Raspberry Pi**.  
Elle permet de détecter automatiquement les disques USB, de monter un disque à la demande, puis de transférer des fichiers depuis une carte SD ou un mobile, avec **vérification d’intégrité SHA-256** et **reprise après redémarrage**.

---

## 1) Présentation du projet
StorageApp est une application web locale (LAN) pour :
- sélectionner un disque actif (UUID-based, pas /dev/sdX),
- importer des fichiers depuis une carte SD,
- uploader depuis un mobile via Wi‑Fi,
- garantir l’intégrité via hash + rename atomique.

L’UI est intégrée (HTML/CSS/JS vanilla) et l’API est exposée en LAN.

---

## 2) Fonctionnalités principales
- Détection automatique des disques USB (UUID/PARTUUID).
- Montage dynamique via `udisksctl`.
- Sélection d’un disque actif.
- Import SD → disque actif.
- Upload mobile → disque actif.
- Upload résumable par chunks (`Content-Range`).
- Vérification SHA‑256 + rename atomique.
- Jobs persistants (JSON) avec reprise après reboot.
- Retry automatique avec backoff.
- Protection anti path traversal.
- API key simple via variable d’environnement.

---

## 3) Architecture technique

### Backend FastAPI
- API REST en LAN.
- Montages gérés par `udisksctl` (user `storageapp`).

### UUID-based disk resolution
- Les disques sont identifiés par `UUID`/`PARTUUID`.
- Les chemins `/dev/sdX` ne sont **pas** persistants et ne sont pas stockés.

### Job system JSON persistence
- Chaque transfert est un job persistant.
- États possibles : `queued / copying / verifying / done / failed / paused / retrying`.
- Le fichier de jobs est conservé dans `STORAGEAPP_JOBS_FILE`.
- Reprise automatique au redémarrage.

### Upload résumable
- Init → upload par chunks → finalize.
- `Content-Range` obligatoire pour les chunks.
- Hash SHA‑256 côté serveur.

---

## 4) Prérequis
- Raspberry Pi OS (Bullseye/Bookworm recommandé)
- Python 3.11+
- `udisks2`
- `ntfs-3g` (NTFS)
- `exfatprogs` (exFAT)

---

## 5) Installation locale
```bash
git clone <repo>
cd storageapp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Variables d’environnement (exemple) :
```bash
export STORAGEAPP_ENV=dev
export STORAGEAPP_STATE_FILE=./.state/state.json
export STORAGEAPP_JOBS_FILE=./.state/import_jobs.json
export STORAGEAPP_API_KEY=changeme
```

Lancement :
```bash
uvicorn storageapp.main:app --host 0.0.0.0 --port 8000
```

---

## 6) Déploiement sur Raspberry Pi

### 6.1 Création utilisateur
```bash
sudo adduser --system --group --home /opt/storageapp storageapp
```

### 6.2 Installation du projet
```bash
sudo mkdir -p /opt/storageapp
sudo chown -R storageapp:storageapp /opt/storageapp
sudo -u storageapp -H git clone <repo> /opt/storageapp
```

### 6.3 Exemple de service systemd
Créer `/etc/systemd/system/storageapp.service` :
```ini
[Unit]
Description=StorageApp API
After=network.target

[Service]
User=storageapp
Group=storageapp
WorkingDirectory=/opt/storageapp
Environment="STORAGEAPP_ENV=pi"
Environment="STORAGEAPP_STATE_FILE=/var/lib/storageapp/state.json"
Environment="STORAGEAPP_JOBS_FILE=/var/lib/storageapp/import_jobs.json"
Environment="STORAGEAPP_API_KEY=changeme"
ExecStart=/opt/storageapp/.venv/bin/uvicorn storageapp.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Puis :
```bash
sudo systemctl daemon-reload
sudo systemctl enable storageapp
sudo systemctl start storageapp
```

---

## 7) Permissions disque
- L’utilisateur `storageapp` doit avoir accès à `udisksctl`.
- Certaines actions peuvent nécessiter polkit si les disques sont montés via un autre seat.
- Exemple (polkit) : autoriser `storageapp` à monter sans prompt.

---

## 8) Variables d’environnement
- `STORAGEAPP_ENV` : `dev` ou `pi`
- `STORAGEAPP_STATE_FILE` : fichier d’état disque actif
- `STORAGEAPP_JOBS_FILE` : fichier de jobs persistants
- `STORAGEAPP_API_KEY` : clé simple d’API (si activée)

---

## 9) Structure des dossiers sur disque actif
- `.storageapp/tmp` : fichiers temporaires (uploads, copies)
- `.storageapp/meta` : métadonnées (optionnel)
- `imports/` : imports SD → disque

---

## 10) API principale

### Disques
- `GET /api/disks`
- `POST /api/disks/active`

### Jobs
- `GET /api/import-jobs`
- `POST /api/import-jobs/copy`
- `POST /api/import-jobs/{id}/retry`
- `POST /api/import-jobs/{id}/cancel`
- `POST /api/import-jobs/{id}/resume`

### Upload résumable
- `POST /api/uploads/init`
- `PUT /api/uploads/{upload_id}` (avec `Content-Range`)
- `GET /api/uploads/{upload_id}`
- `POST /api/uploads/{upload_id}/finalize`

---

## 11) Système d’intégrité (technique)
1. Copie vers un fichier temporaire dans `.storageapp/tmp`
2. Calcul SHA‑256 sur le flux copié
3. Relecture + vérification SHA‑256
4. Rename atomique vers le fichier final

---

## 12) Gestion des erreurs
Exemples :
- `DISK_GONE` : disque retiré ou introuvable
- `PERM` : permissions insuffisantes
- `ENOSPC` : disque plein
- `CHECKSUM_MISMATCH` : fichier corrompu

Les jobs passent en `retrying`, `paused` ou `failed` selon l’erreur.

---

## 13) Sécurité (limitations actuelles)
- Accès LAN uniquement.
- Authentification simple (API key).
- Pas de chiffrement des données en transit (prévoir HTTPS si nécessaire).

---

## 14) Roadmap future
- Gestion multi-utilisateurs
- UI avancée pour jobs et médias
- Support WebDAV / SFTP
- Monitoring + métriques Prometheus

---

## 15) Licence
MIT (ou à définir)
