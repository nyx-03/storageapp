const $ = (sel) => document.querySelector(sel);

const statusDot = $("#sysStatusDot");
const statusText = $("#sysStatusText");
const updatedAt = $("#sysUpdatedAt");
const refreshBtn = $("#sysRefreshBtn");
const shutdownBtn = $("#shutdownBtn");
const shutdownStatus = $("#shutdownStatus");

const fields = {
  hostname: $("#sysHostname"),
  ip: $("#sysIp"),
  os: $("#sysOs"),
  kernel: $("#sysKernel"),
  uptime: $("#sysUptime"),
  temp: $("#sysTemp"),
  mem: $("#sysMem"),
  disk: $("#sysDisk"),
};

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

function fmtUptime(seconds) {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const parts = [];
  if (days) parts.push(`${days}j`);
  if (hours) parts.push(`${hours}h`);
  parts.push(`${mins}m`);
  return parts.join(" ");
}

async function loadSystemInfo() {
  try {
    setStatus("warn", "Chargement…");
    const res = await fetch("/api/system/info", { headers: { "Accept": "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    fields.hostname.textContent = data.hostname || "—";
    fields.ip.textContent = data.ip || "—";
    fields.os.textContent = data.os || "—";
    fields.kernel.textContent = data.kernel || "—";
    fields.uptime.textContent = fmtUptime(data.uptime_seconds);
    fields.temp.textContent = data.cpu_temp_c != null ? `${data.cpu_temp_c} °C` : "—";

    const memTotal = data.memory?.total;
    const memAvail = data.memory?.available;
    if (memTotal && memAvail != null) {
      const used = memTotal - memAvail;
      fields.mem.textContent = `${fmtBytes(used)} / ${fmtBytes(memTotal)}`;
    } else {
      fields.mem.textContent = "—";
    }

    const diskTotal = data.disk?.total;
    const diskUsed = data.disk?.used;
    if (diskTotal && diskUsed != null) {
      fields.disk.textContent = `${fmtBytes(diskUsed)} / ${fmtBytes(diskTotal)}`;
    } else {
      fields.disk.textContent = "—";
    }

    updatedAt.textContent = `Mis à jour: ${new Date().toLocaleTimeString()}`;
    setStatus("ok", "Connecté");
  } catch (e) {
    console.error(e);
    setStatus("danger", "API indisponible");
  }
}

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
    const res = await fetch("/api/system/shutdown", {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    setShutdownStatus(data?.message || "✅ Commande envoyée. Extinction en cours…");
  } catch (e) {
    console.error(e);
    setShutdownStatus(`❌ ${e?.message || e}`);
    shutdownBtn.disabled = false;
  }
});

refreshBtn?.addEventListener("click", loadSystemInfo);

loadSystemInfo();
