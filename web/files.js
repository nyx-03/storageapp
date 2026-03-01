const $ = (sel) => document.querySelector(sel);

const statusDot = $("#filesStatusDot");
const statusText = $("#filesStatusText");
const diskLabel = $("#filesDiskLabel");
const mountLabel = $("#filesMount");
const filesPath = $("#filesPath");
const fileList = $("#fileList");
const filesError = $("#filesError");
const refreshBtn = $("#refreshBtn");
const upBtn = $("#upBtn");
const searchInput = $("#searchInput");
const showHidden = $("#showHidden");
const filesSummary = $("#filesSummary");
const preview = $("#filePreview");
const previewTitle = $("#previewTitle");
const previewBody = $("#previewBody");
const previewClose = $("#previewClose");

let currentPath = "";
let currentEntries = [];

if (preview) {
  preview.hidden = true;
}

function setStatus(kind, text) {
  statusText.textContent = text;
  statusDot.style.background =
    kind === "ok" ? "var(--ok)" :
    kind === "danger" ? "var(--danger)" :
    "var(--warn)";
}

function fmtBytes(value) {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = value;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function renderEntries(entries) {
  if (!fileList) return;
  fileList.innerHTML = "";

  if (!entries.length) {
    fileList.innerHTML = `<div class="muted">Dossier vide.</div>`;
    return;
  }

  for (const e of entries) {
    const row = document.createElement("div");
    row.className = "file-row";
    row.dataset.type = e.type;

    const left = document.createElement("div");
    left.className = "file-name";
    const tag = document.createElement("span");
    tag.className = `file-tag ${e.type === "dir" ? "file-tag--dir" : "file-tag--file"}`;
    tag.textContent = e.type === "dir" ? "DIR" : "FILE";
    const name = document.createElement("span");
    name.textContent = e.name;
    left.appendChild(tag);
    left.appendChild(name);

    const right = document.createElement("div");
    right.className = "file-meta";
    const mtime = e.mtime ? new Date(e.mtime * 1000).toLocaleString() : "—";
    right.innerHTML = e.type === "dir"
      ? `<span>Dossier</span><span>${mtime}</span>`
      : `<span>${fmtBytes(e.size)}</span><span>${mtime}</span>`;

    row.appendChild(left);
    row.appendChild(right);

    if (e.type === "dir") {
      row.addEventListener("click", () => {
        currentPath = currentPath ? `${currentPath}/${e.name}` : e.name;
        loadFiles();
      });
    } else {
      row.classList.add("file-row--file");
      row.addEventListener("click", () => showPreview(e));
    }

    fileList.appendChild(row);
  }
}

function isImage(name) {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name);
}

function isVideo(name) {
  return /\.(mp4|mov|webm|ogg|mkv|avi)$/i.test(name);
}

function buildFilePath(name) {
  return currentPath ? `${currentPath}/${name}` : name;
}

function showPreview(entry) {
  if (!preview || !previewBody || !previewTitle) return;
  preview.hidden = false;
  document.body.classList.add("preview-open");
  previewTitle.textContent = entry.name;
  previewBody.innerHTML = "";

  const filePath = buildFilePath(entry.name);
  const url = `/api/file?path=${encodeURIComponent(filePath)}`;

  if (isImage(entry.name)) {
    const img = document.createElement("img");
    img.className = "file-preview__media";
    img.src = url;
    img.alt = entry.name;
    previewBody.appendChild(img);
  } else if (isVideo(entry.name)) {
    const video = document.createElement("video");
    video.className = "file-preview__media";
    video.src = url;
    video.controls = true;
    video.playsInline = true;
    previewBody.appendChild(video);
  } else {
    const info = document.createElement("div");
    info.className = "muted";
    info.textContent = "Aperçu non disponible pour ce type de fichier.";
    previewBody.appendChild(info);
  }
}

function hidePreview() {
  if (!preview || !previewBody) return;
  preview.hidden = true;
  previewBody.innerHTML = "";
  document.body.classList.remove("preview-open");
}

function applyFilters() {
  const term = (searchInput?.value || "").trim().toLowerCase();
  const showHiddenValue = !!showHidden?.checked;
  const filtered = (currentEntries || []).filter((e) => {
    if (!showHiddenValue && e.name.startsWith(".")) return false;
    if (!term) return true;
    return e.name.toLowerCase().includes(term);
  });

  if (filesSummary) {
    filesSummary.textContent = `${filtered.length} élément(s) affiché(s)`;
  }
  hidePreview();
  renderEntries(filtered);
}

async function loadFiles() {
  try {
    setStatus("warn", "Chargement…");
    filesError.textContent = "";
    hidePreview();
    const res = await fetch(`/api/files?path=${encodeURIComponent(currentPath)}`, {
      headers: { "Accept": "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    diskLabel.textContent = data.disk?.label || data.disk?.dev || "—";
    mountLabel.textContent = data.disk?.mountpoint || "—";
    filesPath.textContent = "/" + (data.cwd || "");

    currentEntries = data.entries || [];
    applyFilters();
    setStatus("ok", "Connecté");
  } catch (e) {
    console.error(e);
    filesError.textContent = e.message || String(e);
    setStatus("danger", "Erreur");
  }
}

refreshBtn?.addEventListener("click", () => loadFiles());
upBtn?.addEventListener("click", () => {
  if (!currentPath) return;
  const parts = currentPath.split("/").filter(Boolean);
  parts.pop();
  currentPath = parts.join("/");
  loadFiles();
});
searchInput?.addEventListener("input", applyFilters);
showHidden?.addEventListener("change", applyFilters);
previewClose?.addEventListener("click", hidePreview);
preview?.addEventListener("click", (e) => {
  if (e.target === preview) hidePreview();
});

loadFiles();
