// Front minimal — vanilla JS
// Assumes FastAPI at same origin (serving static) OR change API_BASE accordingly.

const API_BASE = ""; // "" => same origin. If needed: "http://10.42.0.1"
const $ = (sel) => document.querySelector(sel);

const statusDot = $("#statusDot");
const statusText = $("#statusText");
const diskList = $("#diskList");
const emptyState = $("#emptyState");
const activeBox = $("#activeBox");
const refreshBtn = $("#refreshBtn");
const apiBaseLabel = $("#apiBaseLabel");
const fileInput = $("#fileInput");
const uploadBtn = $("#uploadBtn");
const uploadStatus = $("#uploadStatus");
const sdSourceSelect = $("#sdSourceSelect");
const importSdBtn = $("#importSdBtn");
const sdStatus = $("#sdStatus");
const sdIgnoreExisting = $("#sdIgnoreExisting");
const importJobsEl = $("#importJobs");
const shutdownBtn = $("#shutdownBtn");
const shutdownStatus = $("#shutdownStatus");
const apiKeyInput = $("#apiKeyInput");
const apiKeySaveBtn = $("#apiKeySaveBtn");
const apiKeyStatus = $("#apiKeyStatus");

let hasActiveDisk = false;
let hasRunningImport = false;
let apiKey = localStorage.getItem("storageapp_api_key") || "";

if (apiKeyInput) {
  apiKeyInput.value = apiKey;
}

apiBaseLabel.textContent = API_BASE ? `API: ${API_BASE}` : `API: (same origin)`;
if (apiKeyStatus && apiKey) apiKeyStatus.textContent = "Clé API chargée.";

function setStatus(kind, text) {
  // kind: ok | warn | danger
  statusText.textContent = text;
  statusDot.style.background =
    kind === "ok" ? "var(--ok)" :
    kind === "danger" ? "var(--danger)" :
    "var(--warn)";
}

function updateActionLocks() {
  const lockNoDisk = !hasActiveDisk;
  const lockImportRunning = hasRunningImport;

  // Upload
  if (uploadBtn) uploadBtn.disabled = lockNoDisk || lockImportRunning;
  if (fileInput) fileInput.disabled = lockNoDisk || lockImportRunning;

  // SD Import
  if (importSdBtn) importSdBtn.disabled = lockNoDisk || lockImportRunning;
  if (sdSourceSelect) sdSourceSelect.disabled = lockNoDisk || lockImportRunning;

  // Status messages
  if (lockNoDisk) {
    if (uploadStatus) uploadStatus.textContent = "Aucun disque actif disponible.";
    if (sdStatus) sdStatus.textContent = "Aucun disque actif disponible.";
  } else if (lockImportRunning) {
    if (sdStatus) sdStatus.textContent = "Import en cours… actions temporairement désactivées.";
  }
}

function badge(text, cls) {
  const el = document.createElement("span");
  el.className = `badge ${cls || ""}`.trim();
  el.textContent = text;
  return el;
}

function row(disk, activeDev) {
  const el = document.createElement("div");
  el.className = "row";

  const left = document.createElement("div");

  const title = document.createElement("div");
  title.className = "row__title";

  const label = document.createElement("strong");
  label.textContent = disk.label || disk.dev;

  title.appendChild(label);
  title.appendChild(badge(disk.dev, ""));

  if (disk.active || disk.dev === activeDev) {
    title.appendChild(badge("ACTIF", "badge--active"));
  }

  // FS support
  if (!disk.supported) title.appendChild(badge("FS non supporté", "badge--danger"));
  else title.appendChild(badge("Supporté", "badge--ok"));

  // Mount
  if (disk.mountpoint) title.appendChild(badge("Monté", "badge--ok"));
  else title.appendChild(badge("Non monté", "badge--warn"));

  // Writable
  if (disk.writable) title.appendChild(badge("Écriture OK", "badge--ok"));
  else title.appendChild(badge("Écriture KO", "badge--warn"));

  left.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.innerHTML = `
    <div><strong>Type</strong> ${disk.rm ? "Amovible" : "Fixe"} · <strong>Transport</strong> ${disk.tran || "?"}</div>
    <div><strong>FS</strong> ${disk.fstype || "?"} · <strong>Taille</strong> ${disk.size || "?"}</div>
    <div><strong>Chemin</strong> <code>${disk.mountpoint || "—"}</code></div>
  `;
  left.appendChild(meta);

  const right = document.createElement("div");
  right.className = "actions";

  const btn = document.createElement("button");
  btn.className = "btn btn--primary";
  btn.textContent = (disk.dev === activeDev) ? "Disque actif" : "Utiliser ce disque";
  btn.disabled = (disk.dev === activeDev) || (!disk.supported);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await setActive(disk.dev);
      await refresh();
    } catch (e) {
      console.error(e);
      alert(`Impossible de définir ce disque actif: ${e.message || e}`);
      btn.disabled = false;
    }
  });

  right.appendChild(btn);

  // Petit warning UX
  if (disk.supported && disk.mountpoint && !disk.writable) {
    const warn = document.createElement("div");
    warn.className = "muted";
    warn.style.maxWidth = "220px";
    warn.style.textAlign = "right";
    warn.textContent = "⚠️ Monté mais non écrivable par le service (on corrigera côté Pi).";
    right.appendChild(warn);
  }

  el.appendChild(left);
  el.appendChild(right);
  return el;
}

async function apiGet(path) {
  const headers = { "Accept": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(`${API_BASE}${path}`, { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function apiPost(path, body) {
  const headers = { "Content-Type": "application/json", "Accept": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function setActive(dev) {
  await apiPost("/api/disks/active", { dev });
}

async function refresh() {
  try {
    setStatus("warn", "Chargement…");
    const data = await apiGet("/api/disks");

    diskList.innerHTML = "";
    const disks = data.disks || [];
    const activeDev = data.active_dev || null;
    hasActiveDisk = !!activeDev;

    if (!disks.length) {
      hasActiveDisk = false;
      updateActionLocks();
      emptyState.hidden = false;
      activeBox.innerHTML = `<p class="muted">Aucun disque détecté.</p>`;
      setStatus("ok", "Connecté (0 disque)");
      return;
    }
    emptyState.hidden = true;

    disks.forEach(d => diskList.appendChild(row(d, activeDev)));

    const active = disks.find(d => d.dev === activeDev);
    activeBox.innerHTML = active
      ? `<div><strong>${active.label}</strong> <span class="muted">(${active.dev})</span></div>
         <div class="muted" style="margin-top:6px">FS: ${active.fstype || "?"} · Taille: ${active.size || "?"}</div>
         <div class="muted" style="margin-top:6px">Chemin: <code>${active.mountpoint || "—"}</code></div>`
      : `<p class="muted">Aucun disque actif pour le moment.</p>`;

    updateActionLocks();
    setStatus("ok", `Connecté (${disks.length} disque${disks.length > 1 ? "s" : ""})`);
  } catch (e) {
    setStatus("danger", "API indisponible");
    console.error(e);
  }
}

async function apiPostForm(path, formData) {
  const headers = {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function setSdStatus(text) {
  if (!sdStatus) return;
  sdStatus.textContent = text || "";
}

function renderImportJobs(jobs) {
  if (!importJobsEl) return;
  const list = jobs || [];

  if (!list.length) {
    importJobsEl.innerHTML = "";
    return;
  }

  const html = list.slice(0, 6).map((j) => {
    const status = j.status || "?";
    const icon = status === "done" ? "✅" : status === "failed" ? "❌" : status === "running" ? "⏳" : "🕘";
    const src = j.source || "";
    const dst = j.dest || "";
    const err = j.error ? `<div class=\"muted\">${j.error}</div>` : "";
    return `
      <div class="row" style="grid-template-columns: 1fr;">
        <div class="row__title" style="justify-content: space-between;">
          <strong>${icon} Import SD</strong>
          <span class="badge ${status === "done" ? "badge--ok" : status === "failed" ? "badge--danger" : "badge--warn"}">${status}</span>
        </div>
        ${j.progress != null ? `<div class="muted">Progression: ${j.progress.toFixed(1)}%</div>` : ""}
        <div class="meta">
          <div><strong>Source</strong> <code>${src}</code></div>
          <div><strong>Destination</strong> <code>${dst}</code></div>
          ${err}
        </div>
      </div>
    `;
  }).join("");

  importJobsEl.innerHTML = html;
}

async function loadSdSources() {
  if (!sdSourceSelect) return;

  // Reset list
  sdSourceSelect.innerHTML = '<option value="">— Sélectionne une source SD —</option>';

  try {
    const data = await apiGet("/api/sd/sources");
    const sources = data.sources || [];

    if (!sources.length) {
      setSdStatus("Aucune carte SD détectée.");
      return;
    }

    for (const s of sources) {
      const opt = document.createElement("option");
      const value = s.recommended_path || s.path;
      opt.value = value;
      const sig = (s.signatures || []).join(", ");
      opt.textContent = sig ? `${value} (${sig})` : value;
      sdSourceSelect.appendChild(opt);
    }

    setSdStatus("Sources SD détectées.");
  } catch (e) {
    console.error(e);
    setSdStatus("Impossible de charger les sources SD.");
  }
}

async function startSdImport(sourcePath) {
  if (!sourcePath) {
    setSdStatus("Sélectionne une source SD.");
    return;
  }

  setSdStatus("Démarrage de l'import…");
  if (importSdBtn) importSdBtn.disabled = true;

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
    if (importSdBtn) importSdBtn.disabled = false;
  }
}

async function refreshImportJobs() {
  try {
    const data = await apiGet("/api/import-jobs");
    hasRunningImport = (data.jobs || []).some(j => j.status === "running");
    updateActionLocks();
    renderImportJobs(data.jobs || []);
  } catch (e) {
    // On évite de spammer l'UI si l'API n'est pas prête
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

    // Rafraîchit pour garder l’état disque actif visible
    await refresh();
  } finally {
    uploadBtn.disabled = false;
  }

}

// Upload button handler
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

// Reset status when user changes selection
fileInput?.addEventListener("change", () => {
  if (!uploadStatus) return;
  const count = fileInput.files ? fileInput.files.length : 0;
  uploadStatus.textContent = count ? `${count} fichier(s) sélectionné(s).` : "";
});

// SD import wiring
importSdBtn?.addEventListener("click", async () => {
  const sourcePath = sdSourceSelect?.value || "";
  await startSdImport(sourcePath);
});


sdSourceSelect?.addEventListener("change", () => {
  if (!sdStatus) return;
  sdStatus.textContent = sdSourceSelect.value
    ? `Source sélectionnée: ${sdSourceSelect.value}`
    : "";
});

function setShutdownStatus(text) {
  if (!shutdownStatus) return;
  shutdownStatus.textContent = text || "";
}

shutdownBtn?.addEventListener("click", async () => {
  const ok = confirm("Confirmer l’extinction du Raspberry Pi ?");
  if (!ok) return;

  shutdownBtn.disabled = true;
  setShutdownStatus("Extinction en cours…");

  try {
    const res = await apiPost("/api/system/shutdown", {});
    setShutdownStatus(res?.message || "✅ Commande envoyée. Extinction en cours…");
  } catch (e) {
    console.error(e);
    setShutdownStatus(`❌ ${e?.message || e}`);
    shutdownBtn.disabled = false;
  }
});

apiKeySaveBtn?.addEventListener("click", () => {
  apiKey = (apiKeyInput?.value || "").trim();
  if (apiKey) {
    localStorage.setItem("storageapp_api_key", apiKey);
    if (apiKeyStatus) apiKeyStatus.textContent = "Clé API enregistrée.";
  } else {
    localStorage.removeItem("storageapp_api_key");
    if (apiKeyStatus) apiKeyStatus.textContent = "Clé API supprimée.";
  }
});

refreshBtn.addEventListener("click", async () => {
  await refresh();
  await loadSdSources();
  await refreshImportJobs();
});

// Initial load
refresh();
loadSdSources();
refreshImportJobs();
startImportPolling();
