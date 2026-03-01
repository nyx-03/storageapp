const API_BASE = "";
const $ = (sel) => document.querySelector(sel);

const statusDot = $("#uploadStatusDot");
const statusText = $("#uploadStatusText");
const diskLabel = $("#uploadDiskLabel");
const diskMount = $("#uploadDiskMount");
const dropzone = $("#dropzone");
const uploadInput = $("#uploadInput");
const uploadList = $("#uploadList");
const uploadDir = $("#uploadDir");

function setStatus(kind, text) {
  statusText.textContent = text;
  statusDot.style.background =
    kind === "ok" ? "var(--ok)" :
    kind === "danger" ? "var(--danger)" :
    "var(--warn)";
}

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function formatBytes(bytes) {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let val = bytes;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return `${val.toFixed(val >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
}

async function loadActiveDisk() {
  try {
    const data = await apiGet("/api/disks/active");
    const disk = data.active;
    if (!disk) {
      diskLabel.textContent = "—";
      diskMount.textContent = "—";
      setStatus("warn", "Sélectionne un disque actif");
      return;
    }
    diskLabel.textContent = disk.label || disk.dev;
    diskMount.textContent = disk.mountpoint || "—";
    setStatus("ok", "Prêt");
  } catch (e) {
    console.error(e);
    setStatus("danger", "API indisponible");
  }
}

async function sha256Hex(file) {
  if (!crypto || !crypto.subtle) return null;
  const buf = await file.arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, "0")).join("");
}

function createItem(file) {
  const el = document.createElement("div");
  el.className = "upload-item";
  el.innerHTML = `
    <div class="upload-item__head">
      <strong>${file.name}</strong>
      <span class="muted">${formatBytes(file.size)}</span>
    </div>
    <div class="progress"><div class="progress__bar"></div></div>
    <div class="upload-item__meta muted">En attente…</div>
  `;
  uploadList.prepend(el);
  return el;
}

async function uploadFile(file) {
  const item = createItem(file);
  const bar = item.querySelector(".progress__bar");
  const meta = item.querySelector(".upload-item__meta");

  try {
    meta.textContent = "Calcul du SHA-256…";
    const sha = await sha256Hex(file);
    meta.textContent = sha ? "Initialisation…" : "Initialisation… (sans SHA-256)";
    const init = await apiPost("/api/uploads/init", {
      filename: file.name,
      size: file.size,
      sha256: sha,
      dir: uploadDir.value || null,
    });

    const uploadId = init.upload_id;
    const chunkSize = init.chunk_size || (4 * 1024 * 1024);

    const status = await apiGet(`/api/uploads/${uploadId}`);
    const missing = status.missing_ranges || [[0, file.size - 1]];
    let sent = 0;

    for (const [start, end] of missing) {
      let offset = start;
      while (offset <= end) {
        const chunkEnd = Math.min(offset + chunkSize - 1, end);
        const chunk = file.slice(offset, chunkEnd + 1);
        meta.textContent = `Upload ${formatBytes(offset)} → ${formatBytes(chunkEnd + 1)}…`;
        const res = await fetch(`${API_BASE}/api/uploads/${uploadId}`, {
          method: "PUT",
          headers: {
            "Content-Range": `bytes ${offset}-${chunkEnd}/${file.size}`,
          },
          body: chunk,
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail || `HTTP ${res.status}`);
        }
        sent = chunkEnd + 1;
        const pct = Math.min(100, Math.round((sent / file.size) * 100));
        bar.style.width = `${pct}%`;
        offset = chunkEnd + 1;
      }
    }

    meta.textContent = "Vérification…";
    const finalize = await apiPost(`/api/uploads/${uploadId}/finalize`, {});
    bar.style.width = "100%";
    meta.textContent = `Terminé · ${formatBytes(file.size)}`;
    item.classList.add("upload-item--done");
    console.log("upload done", finalize);
  } catch (e) {
    console.error(e);
    meta.textContent = `Erreur: ${e.message || e}`;
    item.classList.add("upload-item--error");
  }
}

function handleFiles(fileList) {
  if (!fileList || !fileList.length) return;
  [...fileList].forEach(file => uploadFile(file));
}

dropzone?.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dropzone--active");
});

dropzone?.addEventListener("dragleave", () => {
  dropzone.classList.remove("dropzone--active");
});

dropzone?.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dropzone--active");
  handleFiles(e.dataTransfer.files);
});

uploadInput?.addEventListener("change", () => {
  handleFiles(uploadInput.files);
});

loadActiveDisk();
