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

let currentPath = "";

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

    const left = document.createElement("div");
    left.className = "file-name";
    left.textContent = e.name;

    const right = document.createElement("div");
    right.className = "file-meta";
    right.textContent = e.type === "dir" ? "Dossier" : fmtBytes(e.size);

    row.appendChild(left);
    row.appendChild(right);

    if (e.type === "dir") {
      row.addEventListener("click", () => {
        currentPath = currentPath ? `${currentPath}/${e.name}` : e.name;
        loadFiles();
      });
    } else {
      row.classList.add("file-row--file");
    }

    fileList.appendChild(row);
  }
}

async function loadFiles() {
  try {
    setStatus("warn", "Chargement…");
    filesError.textContent = "";
    const res = await fetch(`/api/files?path=${encodeURIComponent(currentPath)}`, {
      headers: { "Accept": "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    diskLabel.textContent = data.disk?.label || data.disk?.dev || "—";
    mountLabel.textContent = data.disk?.mountpoint || "—";
    filesPath.textContent = "/" + (data.cwd || "");

    renderEntries(data.entries || []);
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

loadFiles();
