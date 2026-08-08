"use strict";

const el = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
const age = (seconds) => seconds === null ? "無最後成功圖片" : `最後成功 ${seconds}s 前`;
const stats = (value) => Object.entries(value).map(([window, data]) => `${window}: ${data.attempts} 次，成功率 ${data.success_rate === null ? "—" : `${(data.success_rate * 100).toFixed(1)}%`}，平均 ${data.latency_mean_ms ?? "—"}ms，σ ${data.latency_population_stddev_ms ?? "—"}ms`).join("\n");
let selectedSiteId = null;

function cameraCard(siteId, camera) {
  const cameraId = camera.camera_id;
  const card = el("article"); card.className = `camera ${camera.latest_attempt ? `attempt-${camera.latest_attempt.status}` : ""}`;
  card.append(el("h3", `Camera ${cameraId}`), el("p", age(camera.last_good_age_seconds)));
  if (camera.latest_attempt) card.append(el("p", `最近 attempt：${camera.latest_attempt.status}`));
  if (camera.last_good_age_seconds === null) card.append(el("p", "尚未成功取得圖片")).className = "placeholder";
  else { const image = document.createElement("img"); image.alt = `Camera ${cameraId} last good`; image.src = `api/v1/sites/${encodeURIComponent(siteId)}/cameras/${encodeURIComponent(cameraId)}/last-good-snapshot`; card.append(image); }
  card.append(el("pre", stats(camera.statistics))); return card;
}

function renderSite(site) {
  const root = document.getElementById("site-content"); root.replaceChildren(el("h2", site.display_name));
  root.append(el("h3", "Snapshot 統計"), el("pre", stats(site.statistics)), el("h3", "Camera"));
  const cards = el("div"); cards.className = "cards"; site.cameras.forEach(camera => cards.append(cameraCard(site.site_id, camera))); root.append(cards);
  root.append(el("h3", "Producer health queue/drop"), el("pre", JSON.stringify(site.producer_health, null, 2)));
  root.append(el("h3", "最新 telemetry（已去敏）"), el("pre", JSON.stringify(site.latest_telemetry, null, 2)));
}

function renderSummary(summary) {
  const telemetry = summary.capacity.telemetry; const snapshots = summary.capacity.snapshots;
  document.getElementById("capacity").textContent = `Telemetry SQLite：${telemetry.logical_bytes}/${telemetry.logical_limit_bytes} logical bytes；${telemetry.physical_bytes}/${telemetry.physical_limit_bytes} physical bytes。Snapshot store：${snapshots.bytes}/${snapshots.limit_bytes} bytes，${snapshots.file_count} files`;
  document.getElementById("scheduler").textContent = `Scheduler metadata dropped：${summary.scheduler.metadata_dropped}`;
  const tabs = document.getElementById("tabs"); tabs.replaceChildren();
  const selected = summary.sites.find(site => site.site_id === selectedSiteId) || summary.sites[0];
  if (!selected) { document.getElementById("site-content").replaceChildren(el("p", "尚無 site")); return; }
  selectedSiteId = selected.site_id;
  summary.sites.forEach(site => { const button = el("button", site.display_name); button.type = "button"; button.classList.toggle("active", site.site_id === selectedSiteId); button.addEventListener("click", () => { selectedSiteId = site.site_id; renderSummary(summary); }); tabs.append(button); });
  renderSite(selected);
}

async function refresh() {
  const response = await fetch("api/v1/dashboard/summary", { cache: "no-store" }); if (!response.ok) throw new Error("summary_unavailable");
  renderSummary(await response.json()); document.getElementById("status").textContent = "已更新";
}
async function refreshLoop() {
  try { await refresh(); } catch (_error) { document.getElementById("status").textContent = "無法讀取 dashboard summary；將重試"; }
  window.setTimeout(refreshLoop, 15000);
}
refreshLoop();
