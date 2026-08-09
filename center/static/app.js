"use strict";

const el = (tag, text) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
};
const value = (item, key) => item && Object.hasOwn(item, key) && item[key] !== null ? item[key] : null;
const shown = (item, key, suffix = "") => {
  const raw = value(item, key);
  return raw === null ? "—" : `${raw}${suffix}`;
};
const bool = (raw) => raw === true ? "是" : raw === false ? "否" : "—";
const state = (raw) => raw === true ? "正常" : raw === false ? "失敗" : "—";
const incomplete = (item) => value(item, "truncated") === true ? "不完整" : "";
const channelLabel = (item, channelId) => item && typeof item.label === "string" && item.label.trim() ? item.label.trim() : `Channel ${channelId}`;
let selectedSiteId = null;

function table(headers, rows) {
  const result = el("table"); result.className = "data-table";
  const head = el("thead"); const headerRow = el("tr");
  headers.forEach(header => headerRow.append(el("th", header)));
  head.append(headerRow); result.append(head);
  const body = el("tbody");
  rows.forEach(row => {
    const tr = el("tr"); row.forEach(cell => tr.append(el("td", cell)));
    body.append(tr);
  });
  result.append(body); return result;
}

function cameraCard(siteId, camera) {
  const cameraId = camera.camera_id;
  const card = el("article"); card.className = `camera ${camera.latest_attempt ? `attempt-${camera.latest_attempt.status}` : ""}`;
  card.append(el("h3", channelLabel(camera, cameraId)), el("p", snapshotText(camera)));
  if (camera.last_good_age_seconds === null) card.append(el("p", "尚未成功取得圖片")).className = "placeholder";
  else {
    const image = document.createElement("img"); image.alt = `${channelLabel(camera, cameraId)} last good`;
    image.src = `api/v1/sites/${encodeURIComponent(siteId)}/cameras/${encodeURIComponent(cameraId)}/last-good-snapshot`; card.append(image);
  }
  return card;
}

function byKind(events, kind) {
  return new Map(events.filter(event => event.kind === kind && event.channel_id !== null).map(event => [event.channel_id, event]));
}

function snapshotText(camera) {
  if (!camera) return "—";
  const latest = camera.latest_attempt;
  const status = latest ? latest.status : "—";
  const age = camera.last_good_age_seconds === null ? "—" : `${camera.last_good_age_seconds}s`;
  const windows = ["1h", "24h", "7d"].map(window => {
    const stats = camera.statistics && camera.statistics[window];
    if (!stats || stats.attempts === 0) return `${window}: 無樣本`;
    const rate = stats.success_rate === null ? "—" : `${(stats.success_rate * 100).toFixed(1)}%`;
    return `${window}: attempts ${stats.attempts} / success ${rate} / mean ${shown(stats, "latency_mean_ms", "ms")} / pop σ ${shown(stats, "latency_population_stddev_ms", "ms")}`;
  }).join("；");
  const detail = latest ? `latest ${status} / ${shown(latest, "latency_ms", "ms")} / ${shown(latest, "error_code")}` : "latest —";
  return `${detail}；last age ${age}；${windows}`;
}

function nvrTable(site) {
  const events = site.latest_telemetry || [];
  const live = byKind(events, "nvr.live"); const recording = byKind(events, "nvr.recording");
  const cameras = new Map((site.cameras || []).map(camera => [camera.camera_id, camera]));
  const channelIds = new Set([...live.keys(), ...recording.keys(), ...cameras.keys()]);
  const rows = [...channelIds].sort((a, b) => a - b).map(channelId => {
    const liveEvent = live.get(channelId); const recordingEvent = recording.get(channelId);
    const liveMetrics = liveEvent && liveEvent.metrics; const recordingMetrics = recordingEvent && recordingEvent.metrics;
    const liveSamples = value(liveMetrics, "live_sample_count_24h");
    const live24 = typeof liveSamples === "number" && liveSamples > 0
      ? `${shown(liveMetrics, "live_online_rate_24h", "%")} / ${shown(liveMetrics, "live_observed_hours_24h", "h")} / ${shown(liveMetrics, "disconnect_count_24h")}`
      : "—（無樣本）";
    const recordingCounts = recordingMetrics ? `${shown(recordingMetrics, "file_count_24h")} files / invalid ${shown(recordingMetrics, "invalid_file_count_24h")} / pages ${shown(recordingMetrics, "page_count")} / query ${shown(recordingMetrics, "query_duration_ms", "ms")}` : "—";
    const recordingGaps = recordingMetrics ? `${shown(recordingMetrics, "recording_coverage_24h_pct", "%")} coverage / ${shown(recordingMetrics, "gap_count_24h")} gaps / total ${shown(recordingMetrics, "gap_total_seconds_24h", "s")} / largest ${shown(recordingMetrics, "largest_gap_seconds_24h", "s")} ${incomplete(recordingMetrics)}`.trim() : "—";
    return [
      channelLabel(liveEvent || recordingEvent || cameras.get(channelId), channelId),
      bool(value(liveMetrics, "live_video")),
      `${shown(liveMetrics, "live_sample_count_24h")} samples / ${shown(liveMetrics, "error_code")}`,
      live24,
      bool(value(recordingMetrics, "recording_query_ok")),
      bool(value(recordingMetrics, "recording_recent")),
      `${shown(recordingMetrics, "last_recording")} / age ${shown(recordingMetrics, "last_recording_age_hours", "h")} / ${shown(recordingMetrics, "error_code")}`,
      recordingCounts,
      recordingGaps,
      snapshotText(cameras.get(channelId)),
    ];
  });
  return table(["Camera", "Live", "Live samples / error", "24h online / observed / disconnect", "Recording query", "Recent", "Last / age / error", "Files / invalid / pages / duration", "Coverage / gaps / incomplete", "snapshot attempt / age / 1h / 24h / 7d"], rows);
}

function siteSummary(site) {
  const health = (site.producer_health || []).find(event => event.source === "addon");
  const metrics = health && health.metrics;
  const rows = [
    ["站點", `${site.display_name} (${site.site_id})`],
    ["版本 / state", `${shown(metrics, "source_version")} / ${shown(metrics, "producer_state")}`],
    ["channel / concurrency", `${shown(metrics, "channel_count")} / ${shown(metrics, "snapshot_max_concurrency")}`],
    ["producer queue / drop", `${shown(metrics, "telemetry_queue_depth")} / ${shown(metrics, "telemetry_queue_capacity")}；drop ${shown(metrics, "dropped_events")}`],
    ["Center reachable", state(value(metrics, "center_reachable"))],
    ["Telemetry logical", shown(site, "logical_bytes")],
  ];
  return table(["Add-on / producer / capacity", "摘要"], rows);
}

function renderSite(site) {
  const root = document.getElementById("site-content"); root.replaceChildren(el("h2", site.display_name));
  root.append(el("h3", "Add-on / producer / capacity"), siteSummary(site));
  root.append(el("h3", "NVR live / recording"), nvrTable(site));
  root.append(el("h3", "Snapshot last good（單張）"));
  const cards = el("div"); cards.className = "cards";
  (site.cameras || []).forEach(camera => cards.append(cameraCard(site.site_id, camera))); root.append(cards);
}

function renderGlobalSummary(summary) {
  const telemetry = summary.capacity.telemetry; const snapshots = summary.capacity.snapshots;
  const root = document.getElementById("global-summary"); root.replaceChildren(
    el("p", `Center telemetry：logical ${telemetry.logical_bytes}/${telemetry.logical_limit_bytes}；physical ${telemetry.physical_bytes}/${telemetry.physical_limit_bytes}`),
    el("p", `Center last-good snapshot store：${snapshots.bytes}/${snapshots.limit_bytes} bytes，${snapshots.file_count} entries`),
    el("p", `Scheduler metadata drop：${summary.scheduler.metadata_dropped}`),
  );
}

function renderSummary(summary) {
  renderGlobalSummary(summary);
  const tabs = document.getElementById("tabs"); tabs.replaceChildren();
  const selected = summary.sites.find(site => site.site_id === selectedSiteId) || summary.sites[0];
  if (!selected) { document.getElementById("site-content").replaceChildren(el("p", "尚無 site")); return; }
  selectedSiteId = selected.site_id;
  summary.sites.forEach(site => {
    const button = el("button", site.display_name); button.type = "button";
    button.classList.toggle("active", site.site_id === selectedSiteId);
    button.addEventListener("click", () => { selectedSiteId = site.site_id; renderSummary(summary); }); tabs.append(button);
  });
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
