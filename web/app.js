// Front minimal — vanilla JS
// Assumes FastAPI at same origin (serving static) OR change API_BASE accordingly.

const API_BASE = ""; // "" => same origin. If needed: "http://10.42.0.1"
const $ = (sel) => document.querySelector(sel);

const statusDot = $("#statusDot");
const statusText = $("#statusText");
const refreshBtn = $("#refreshBtn");
const apiBaseLabel = $("#apiBaseLabel");

const diskList = $("#diskList");
const emptyState = $("#emptyState");

const destSelect = $("#destSelect");
const destSetBtn = $("#destSetBtn");
const destInfo = $("#destInfo");

const fileInput = $("#fileInput");
const uploadBtn = $("#uploadBtn");
const uploadStatus = $("#uploadStatus");

const sourceSelect = $("#sourceSelect");
const sourceInfo = $("#sourceInfo");
const importBtn = $("#importBtn");
const sdStatus = $("#sdStatus");
const sdIgnoreExisting = $("#sdIgnoreExisting");
const importJobsEl = $("#importJobs");

let hasActiveDisk = false;
let hasRunningImport = false;
let activeDev = null;
let disksCache = [];
let sourcesCache = [];

apiBaseLabel.textContent = API_BASE ? `API: ${API_BASE}` : `API: (same origin)`;

function isActiveSelection(value) {
  if (!value) return false;
  const current = (disksCache || []).find(d => d.dev === activeDev);
  if (!current) return false;
  return value === current.dev || value === current.uuid || value === current.partuuid;
}

function setStatus(kind, text) {
  statusText.textContent = text;
  statusDot.style.background =
    kind === "ok" ? "var(--ok)" :
    kind === "danger" ? "var(--danger)" :
    "var(--warn)";
}

function updateActionLocks() {
  const lockNoDisk = !hasActiveDisk;
  const lockImportRunning = hasRunningImport;
  const hasSource = !!(sourceSelect && sourceSelect.value);

  if (uploadBtn) uploadBtn.disabled = lockNoDisk || lockImportRunning;
  if (fileInput) fileInput.disabled = lockNoDisk || lockImportRunning;
  if (importBtn) importBtn.disabled = lockNoDisk || lockImportRunning || !hasSource;
  if (destSetBtn) destSetBtn.disabled = !destSelect?.value || isActiveSelection(destSelect.value);

  if (lockNoDisk) {
    if (uploadStatus) uploadStatus.textContent = "Aucun disque actif disponible.";
    if (sdStatus) sdStatus.textContent = "Sélectionne un disque actif pour importer.";
  } else if (lockImportRunning) {
    if (sdStatus) sdStatus.textContent = "Import en cours… actions temporairement désactivées.";
  }
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

async function apiPostForm(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

class Sha256 {
  constructor() {
    this._h = [
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    this._buf = new Uint8Array(64);
    this._bufLen = 0;
    this._bytes = 0;
  }

  update(data) {
    let i = 0;
    this._bytes += data.length;
    while (i < data.length) {
      const space = 64 - this._bufLen;
      const take = Math.min(space, data.length - i);
      this._buf.set(data.subarray(i, i + take), this._bufLen);
      this._bufLen += take;
      i += take;
      if (this._bufLen === 64) {
        this._transform(this._buf);
        this._bufLen = 0;
      }
    }
    return this;
  }

  _transform(chunk) {
    const K = Sha256.K;
    const w = new Uint32Array(64);
    for (let i = 0; i < 16; i++) {
      w[i] = (chunk[i * 4] << 24) | (chunk[i * 4 + 1] << 16) | (chunk[i * 4 + 2] << 8) | (chunk[i * 4 + 3]);
    }
    for (let i = 16; i < 64; i++) {
      const s0 = Sha256._rotr(w[i - 15], 7) ^ Sha256._rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = Sha256._rotr(w[i - 2], 17) ^ Sha256._rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }

    let a = this._h[0];
    let b = this._h[1];
    let c = this._h[2];
    let d = this._h[3];
    let e = this._h[4];
    let f = this._h[5];
    let g = this._h[6];
    let h = this._h[7];

    for (let i = 0; i < 64; i++) {
      const S1 = Sha256._rotr(e, 6) ^ Sha256._rotr(e, 11) ^ Sha256._rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = Sha256._rotr(a, 2) ^ Sha256._rotr(a, 13) ^ Sha256._rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    this._h[0] = (this._h[0] + a) >>> 0;
    this._h[1] = (this._h[1] + b) >>> 0;
    this._h[2] = (this._h[2] + c) >>> 0;
    this._h[3] = (this._h[3] + d) >>> 0;
    this._h[4] = (this._h[4] + e) >>> 0;
    this._h[5] = (this._h[5] + f) >>> 0;
    this._h[6] = (this._h[6] + g) >>> 0;
    this._h[7] = (this._h[7] + h) >>> 0;
  }

  digest() {
    const len = this._bytes;
    const bitLenHi = Math.floor(len / 0x20000000);
    const bitLenLo = (len << 3) >>> 0;
    const padLen = this._bufLen < 56 ? 56 - this._bufLen : 120 - this._bufLen;
    const pad = new Uint8Array(padLen + 8);
    pad[0] = 0x80;
    pad[padLen + 0] = (bitLenHi >>> 24) & 0xff;
    pad[padLen + 1] = (bitLenHi >>> 16) & 0xff;
    pad[padLen + 2] = (bitLenHi >>> 8) & 0xff;
    pad[padLen + 3] = bitLenHi & 0xff;
    pad[padLen + 4] = (bitLenLo >>> 24) & 0xff;
    pad[padLen + 5] = (bitLenLo >>> 16) & 0xff;
    pad[padLen + 6] = (bitLenLo >>> 8) & 0xff;
    pad[padLen + 7] = bitLenLo & 0xff;
    this.update(pad);

    const out = new Uint8Array(32);
    for (let i = 0; i < 8; i++) {
      out[i * 4] = (this._h[i] >>> 24) & 0xff;
      out[i * 4 + 1] = (this._h[i] >>> 16) & 0xff;
      out[i * 4 + 2] = (this._h[i] >>> 8) & 0xff;
      out[i * 4 + 3] = this._h[i] & 0xff;
    }
    return out;
  }

  hex() {
    const out = this.digest();
    return Array.from(out).map(b => b.toString(16).padStart(2, "0")).join("");
  }

  static _rotr(x, n) {
    return (x >>> n) | (x << (32 - n));
  }
}

Sha256.K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
  0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
  0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
  0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

async function sha256Hex(file, onProgress) {
  // Streaming SHA-256 (HTTP / vieux navigateurs) avec progression
  const hasher = new Sha256();
  const chunkSize = 4 * 1024 * 1024;
  let offset = 0;
  let lastPct = -1;
  while (offset < file.size) {
    const slice = file.slice(offset, offset + chunkSize);
    const buf = await slice.arrayBuffer();
    hasher.update(new Uint8Array(buf));
    offset += chunkSize;
    if (onProgress) {
      const pct = Math.min(100, Math.floor((offset / file.size) * 100));
      if (pct !== lastPct) {
        lastPct = pct;
        onProgress(offset, file.size, pct);
      }
    }
  }
  return hasher.hex();
}

function describeDisk(d) {
  const label = d.label || d.dev;
  const size = d.size || "?";
  const fs = d.fstype || "?";
  return `${label} · ${size} · ${fs}`;
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

function badge(text, cls) {
  const el = document.createElement("span");
  el.className = `badge ${cls || ""}`.trim();
  el.textContent = text;
  return el;
}

function renderDiskList() {
  if (!diskList) return;
  diskList.innerHTML = "";

  const list = disksCache || [];
  if (!list.length) {
    if (emptyState) emptyState.hidden = false;
    return;
  }

  if (emptyState) emptyState.hidden = true;

  for (const d of list) {
    const row = document.createElement("div");
    row.className = "row";

    const left = document.createElement("div");
    const title = document.createElement("div");
    title.className = "row__title";

    const label = document.createElement("strong");
    label.textContent = d.label || d.dev;
    title.appendChild(label);
    title.appendChild(badge(d.dev, ""));
    if (d.dev === activeDev) title.appendChild(badge("ACTIF", "badge--active"));

    if (!d.supported) title.appendChild(badge("FS non supporté", "badge--danger"));
    else title.appendChild(badge("Supporté", "badge--ok"));

    if (d.mountpoint) title.appendChild(badge("Monté", "badge--ok"));
    else title.appendChild(badge("Non monté", "badge--warn"));

    if (d.writable === true) title.appendChild(badge("Écriture OK", "badge--ok"));
    else if (d.writable === false) title.appendChild(badge("Écriture KO", "badge--warn"));
    else title.appendChild(badge("Écriture ?", "badge--warn"));

    left.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "meta";
    const used = d.used_bytes != null && d.total_bytes != null
      ? `${formatBytes(d.used_bytes)} / ${formatBytes(d.total_bytes)}`
      : "—";
    meta.innerHTML = `
      <div><strong>Type</strong> ${d.rm ? "Amovible" : "Fixe"} · <strong>Transport</strong> ${d.tran || "?"}</div>
      <div><strong>FS</strong> ${d.fstype || "?"} · <strong>Taille</strong> ${d.size || "?"}</div>
      <div><strong>Occupation</strong> ${used}</div>
      <div><strong>Chemin</strong> <code>${d.mountpoint || "—"}</code></div>
    `;
    left.appendChild(meta);

    const right = document.createElement("div");
    right.className = "actions";
    const btn = document.createElement("button");
    btn.className = "btn btn--primary";
    btn.textContent = d.dev === activeDev ? "Disque actif" : "Utiliser ce disque";
    btn.disabled = !d.supported || d.dev === activeDev;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await setActive(d.dev);
        await loadDisks();
      } catch (e) {
        console.error(e);
        alert(`Impossible de définir ce disque actif: ${e.message || e}`);
        btn.disabled = false;
      }
    });
    right.appendChild(btn);

    row.appendChild(left);
    row.appendChild(right);
    diskList.appendChild(row);
  }
}

function renderDestinations() {
  if (!destSelect) return;
  destSelect.innerHTML = "";

  const supported = (disksCache || []).filter(d => d.supported);
  if (!supported.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "— Aucun disque supporté détecté —";
    destSelect.appendChild(opt);
    hasActiveDisk = false;
    if (destInfo) destInfo.textContent = "Aucun disque actif disponible.";
    updateActionLocks();
    return;
  }

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "— Choisir un disque —";
  destSelect.appendChild(placeholder);

  for (const d of supported) {
    const opt = document.createElement("option");
    opt.value = d.uuid || d.partuuid || d.dev;
    const writableHint = d.mountpoint && d.writable === false ? " · lecture seule" : "";
    const mountHint = !d.mountpoint ? " · non monté" : "";
    opt.textContent = describeDisk(d) + writableHint + mountHint;
    if (d.mountpoint && d.writable === false) opt.disabled = true;
    destSelect.appendChild(opt);
  }

  if (destInfo) {
    const current = supported.find(d => d.dev === activeDev);
    destInfo.textContent = current
      ? `Disque actif actuel: ${current.label || current.dev} (${current.mountpoint || "—"})`
      : "Aucun disque actif.";
  }
}

async function loadDisks() {
  try {
    setStatus("warn", "Chargement…");
    const data = await apiGet("/api/disks");
    disksCache = data.disks || [];
    activeDev = data.active_dev || null;
    hasActiveDisk = !!activeDev;
    renderDiskList();
    renderDestinations();
    updateActionLocks();
    setStatus("ok", `Connecté (${disksCache.length} disque${disksCache.length > 1 ? "s" : ""})`);
  } catch (e) {
    setStatus("danger", "API indisponible");
    console.error(e);
  }
}

async function setActive(dev) {
  await apiPost("/api/disks/active", { dev });
}

async function loadSources() {
  if (!sourceSelect) return;
  sourceSelect.innerHTML = "";

  try {
    const data = await apiGet("/api/sources");
    sourcesCache = data.sources || [];

    if (!sourcesCache.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "— Aucun disque source détecté —";
      sourceSelect.appendChild(opt);
      if (sourceInfo) sourceInfo.textContent = "Branche une carte SD (USB) pour la copier.";
      updateActionLocks();
      return;
    }

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "— Choisir un disque source —";
    sourceSelect.appendChild(placeholder);

    for (const s of sourcesCache) {
      const opt = document.createElement("option");
      opt.value = s.uuid || s.partuuid || s.dev || "";
      opt.textContent = `${s.label || s.dev} · ${s.size || "?"} · ${s.fstype || "?"}`;
      opt.dataset.mountpoint = s.mountpoint || "";
      sourceSelect.appendChild(opt);
    }

    if (sourceInfo) sourceInfo.textContent = "Sélectionne un disque source.";
  } catch (e) {
    console.error(e);
    if (sourceInfo) sourceInfo.textContent = "Impossible de charger les sources.";
  } finally {
    updateActionLocks();
  }
}

function setSdStatus(text) {
  if (!sdStatus) return;
  sdStatus.textContent = text || "";
}

async function startImport(sourcePath) {
  if (!sourcePath) {
    setSdStatus("Sélectionne une source.");
    return;
  }

  setSdStatus("Démarrage de l'import…");
  if (importBtn) importBtn.disabled = true;

  try {
    const res = await apiPost("/api/import-sd", {
      source_path: sourcePath,
      ignore_existing: !!sdIgnoreExisting?.checked,
    });
    const jobId = res?.job?.id;
    setSdStatus(jobId ? `Import lancé (job: ${jobId}).` : "Import lancé.");
    await refreshImportJobs();
  } catch (e) {
    console.error(e);
    setSdStatus(`❌ ${e?.message || e}`);
  } finally {
    if (importBtn) importBtn.disabled = false;
  }
}

function renderImportJobs(jobs) {
  if (!importJobsEl) return;
  const list = jobs || [];

  if (!list.length) {
    importJobsEl.innerHTML = "";
    return;
  }

  const html = list.slice(0, 5).map((j) => {
    const state = j.state || "?";
    const icon = state === "done" ? "✅" : state === "failed" ? "❌" : state === "verifying" ? "🔎" : state === "copying" ? "⏳" : "🕘";
    const progress = j.progress || {};
    const pct = progress.total ? Math.round((progress.bytes_done / progress.total) * 100) : null;
    const phase = state === "verifying" ? "Vérification" : "Copie";
    const verified = j.integrity?.verified ? " · vérifié" : "";
    const err = j.last_error?.message ? `<div class="muted">${j.last_error.message}</div>` : "";
    const actions = [];
    if (state === "failed") actions.push(`<button class="btn btn--ghost job-action" data-action="retry" data-id="${j.id}">Retry</button>`);
    if (state === "paused") actions.push(`<button class="btn btn--ghost job-action" data-action="resume" data-id="${j.id}">Reprendre</button>`);
    if (["queued", "copying", "verifying", "retrying"].includes(state)) {
      actions.push(`<button class="btn btn--ghost job-action" data-action="cancel" data-id="${j.id}">Pause</button>`);
    }
    if (state === "done") actions.push(`<button class="btn btn--ghost job-action" data-action="delete" data-id="${j.id}">Retirer</button>`);
    return `
      <div class="row" style="grid-template-columns: 1fr;">
        <div class="row__title" style="justify-content: space-between;">
          <strong>${icon} Import</strong>
          <span class="badge ${state === "done" ? "badge--ok" : state === "failed" ? "badge--danger" : "badge--warn"}">${state}${verified}</span>
        </div>
        ${pct != null ? `<div class="muted">${phase}: ${pct}%</div>` : ""}
        ${err}
        ${actions.length ? `<div class="actions">${actions.join("")}</div>` : ""}
      </div>
    `;
  }).join("");

  importJobsEl.innerHTML = html;
  importJobsEl.querySelectorAll(".job-action").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      if (!id || !action) return;
      try {
        await apiPost(`/api/import-jobs/${id}/${action}`, {});
        await refreshImportJobs();
      } catch (e) {
        console.error(e);
        alert(`Action impossible: ${e.message || e}`);
      }
    });
  });
}

async function refreshImportJobs() {
  try {
    const data = await apiGet("/api/import-jobs");
    const jobs = (data.jobs || []).filter(j => j.type === "copy");
    hasRunningImport = jobs.some(j => ["queued", "copying", "verifying", "retrying"].includes(j.state));
    updateActionLocks();
    renderImportJobs(jobs);
  } catch (e) {
    console.error(e);
  }
}

let importPollTimer = null;
function startImportPolling() {
  if (importPollTimer) return;
  importPollTimer = setInterval(refreshImportJobs, 2000);
}

async function uploadFiles(files) {
  const hashes = [];
  uploadStatus.textContent = "Calcul du SHA-256…";
  let idx = 0;
  for (const f of files) {
    idx += 1;
    uploadStatus.textContent = `Hash ${idx}/${files.length}: 0%`;
    hashes.push(await sha256Hex(f, (_done, _total, pct) => {
      uploadStatus.textContent = `Hash ${idx}/${files.length}: ${pct}%`;
    }));
  }

  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("sha256s", JSON.stringify(hashes));

  uploadStatus.textContent = "Envoi en cours…";
  uploadBtn.disabled = true;

  try {
    const result = await apiPostForm("/api/upload", fd);
    const okCount = (result.saved || []).length;
    const errCount = (result.errors || []).length;

    uploadStatus.textContent =
      `✅ ${okCount} fichier(s) envoyé(s)` + (errCount ? ` — ⚠️ ${errCount} erreur(s)` : "");

    await loadDisks();
  } finally {
    uploadBtn.disabled = false;
  }
}

destSelect?.addEventListener("change", () => {
  const current = (disksCache || []).find(d => [d.dev, d.uuid, d.partuuid].includes(destSelect.value));
  if (destInfo && destSelect.value) {
    destInfo.textContent = current ? `Monté sur ${current.mountpoint || "—"}` : "";
  }
  updateActionLocks();
});

destSetBtn?.addEventListener("click", async () => {
  const dev = destSelect?.value;
  if (!dev) return;
  try {
    await setActive(dev);
    await loadDisks();
  } catch (e) {
    console.error(e);
    alert(`Impossible de définir ce disque actif: ${e.message || e}`);
  }
});

sourceSelect?.addEventListener("change", () => {
  const opt = sourceSelect.options[sourceSelect.selectedIndex];
  const mp = opt?.dataset?.mountpoint;
  if (sourceInfo) sourceInfo.textContent = mp ? `Monté sur ${mp}` : "";
  updateActionLocks();
});

importBtn?.addEventListener("click", async () => {
  const sourcePath = sourceSelect?.value || "";
  await startImport(sourcePath);
});

uploadBtn?.addEventListener("click", async () => {
  const files = fileInput?.files;
  if (!files || files.length === 0) {
    if (uploadStatus) uploadStatus.textContent = "Choisis au moins un fichier.";
    return;
  }
  try {
    await uploadFiles(files);
  } catch (e) {
    console.error(e);
    if (uploadStatus) uploadStatus.textContent = `❌ ${e?.message || e}`;
  }
});

fileInput?.addEventListener("change", () => {
  if (!uploadStatus) return;
  const count = fileInput.files ? fileInput.files.length : 0;
  uploadStatus.textContent = count ? `${count} fichier(s) sélectionné(s).` : "";
});

refreshBtn?.addEventListener("click", async () => {
  await loadDisks();
  await loadSources();
  await refreshImportJobs();
});

// Initial load
loadDisks();
loadSources();
refreshImportJobs();
startImportPolling();
