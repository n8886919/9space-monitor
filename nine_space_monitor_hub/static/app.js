"use strict";

const el = (tag, text) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
};
const shown = value => value === null || value === undefined ? "—" : String(value);
const yesNo = value => value === true ? "是" : value === false ? "否" : "—";
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
  const card = el("article"); card.className = "camera";
  const attempt = camera.latest_attempt;
  const attemptText = attempt
    ? `${attempt.success ? "成功" : "失敗"} / ${shown(attempt.latency_ms)} ms / ${shown(attempt.error_code)}`
    : "尚無嘗試";
  card.append(el("h3", camera.label), el("p", `${attemptText}；圖片 age ${shown(camera.last_good_age_seconds)} s`));
  if (camera.last_good_age_seconds === null) {
    card.append(el("p", "尚未成功取得圖片"));
  } else {
    const image = document.createElement("img"); image.alt = `${camera.label} last good`;
    image.src = `api/v1/sites/${encodeURIComponent(siteId)}/cameras/${encodeURIComponent(camera.camera_id)}/last-good-snapshot`;
    card.append(image);
  }
  return card;
}

function renderSite(site) {
  const root = document.getElementById("site-content"); root.replaceChildren(el("h2", site.display_name));
  const rows = site.cameras.map(camera => [
    camera.label,
    yesNo(camera.live_video),
    yesNo(camera.recording_query_ok),
    yesNo(camera.recording_recent),
    shown(camera.last_recording),
    shown(camera.recording_files_24h),
    shown(camera.recording_coverage_24h),
    yesNo(camera.snapshot_available),
    shown(camera.recording_error),
  ]);
  root.append(table(
    ["Camera", "Live", "錄影查詢", "24h 有錄影", "最後錄影", "檔案數", "覆蓋率", "截圖", "錯誤"],
    rows,
  ));
  root.append(el("h3", "Snapshot last-good（每鏡頭一張）"));
  const cards = el("div"); cards.className = "cards";
  site.cameras.forEach(camera => cards.append(cameraCard(site.site_id, camera)));
  root.append(cards);
}

function render(summary) {
  const usage = summary.snapshot_store;
  document.getElementById("global-summary").replaceChildren(
    el("p", `Snapshot store：${usage.bytes}/${usage.limit_bytes} bytes，${usage.file_count} entries`),
  );
  const tabs = document.getElementById("tabs"); tabs.replaceChildren();
  const selected = summary.sites.find(site => site.site_id === selectedSiteId) || summary.sites[0];
  if (!selected) {
    document.getElementById("site-content").replaceChildren(el("p", "尚未設定 site"));
    return;
  }
  selectedSiteId = selected.site_id;
  summary.sites.forEach(site => {
    const button = el("button", site.display_name); button.type = "button";
    button.classList.toggle("active", site.site_id === selectedSiteId);
    button.addEventListener("click", () => { selectedSiteId = site.site_id; render(summary); });
    tabs.append(button);
  });
  renderSite(selected);
}

async function refresh() {
  const response = await fetch("api/v1/dashboard/summary", {cache: "no-store"});
  if (!response.ok) throw new Error("summary_unavailable");
  render(await response.json());
  document.getElementById("status").textContent = "已更新";
}
async function refreshLoop() {
  try { await refresh(); }
  catch (_error) { document.getElementById("status").textContent = "無法更新；將重試"; }
  window.setTimeout(refreshLoop, 15000);
}
refreshLoop();
