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

function describeDisk(d) {
  const label = d.label || d.dev;
  const size = d.size || "?";
  const fs = d.fstype || "?";
  return `${label} · ${size} · ${fs}`;
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
    meta.innerHTML = `
      <div><strong>Type</strong> ${d.rm ? "Amovible" : "Fixe"} · <strong>Transport</strong> ${d.tran || "?"}</div>
      <div><strong>FS</strong> ${d.fstype || "?"} · <strong>Taille</strong> ${d.size || "?"}</div>
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

  const html = list.slice(0, 3).map((j) => {
    const status = j.status || "?";
    const icon = status === "done" ? "✅" : status === "failed" ? "❌" : status === "running" ? "⏳" : "🕘";
    const err = j.error ? `<div class="muted">${j.error}</div>` : "";
    return `
      <div class="row" style="grid-template-columns: 1fr;">
        <div class="row__title" style="justify-content: space-between;">
          <strong>${icon} Import</strong>
          <span class="badge ${status === "done" ? "badge--ok" : status === "failed" ? "badge--danger" : "badge--warn"}">${status}</span>
        </div>
        ${j.progress != null ? `<div class="muted">Progression: ${j.progress.toFixed(1)}%</div>` : ""}
        ${err}
      </div>
    `;
  }).join("");

  importJobsEl.innerHTML = html;
}

async function refreshImportJobs() {
  try {
    const data = await apiGet("/api/import-jobs");
    hasRunningImport = (data.jobs || []).some(j => j.status === "running");
    updateActionLocks();
    renderImportJobs(data.jobs || []);
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
  const fd = new FormData();
  for (const f of files) fd.append("files", f);

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
