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
    const tr = el("tr"); row.forEach(cell => {
      const td = el("td");
      if (cell instanceof Node) td.append(cell); else td.textContent = cell;
      tr.append(td);
    });
    body.append(tr);
  });
  result.append(body); return result;
}

function cameraCard(siteId, camera) {
  const card = el("article"); card.className = "camera";
  if (camera.last_good_age_seconds !== null) {
    card.classList.add(camera.snapshot_available ? "snapshot-fresh" : "snapshot-stale");
  }
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

function channelToggle(siteId, camera) {
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = camera.enabled;
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-label", `CH ${String(camera.camera_id).padStart(2, "0")} 啟用`);
  toggle.addEventListener("change", async () => {
    const enabled = toggle.checked;
    toggle.disabled = true;
    try {
      const response = await fetch(
        `api/v1/sites/${encodeURIComponent(siteId)}/cameras/${encodeURIComponent(camera.camera_id)}/enabled`,
        {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})},
      );
      if (!response.ok) throw new Error("toggle_failed");
      await refresh();
    } catch (_error) {
      toggle.checked = !enabled;
      document.getElementById("status").textContent = "無法變更 CH 狀態；請重試";
    } finally {
      toggle.disabled = false;
    }
  });
  return toggle;
}

function renderSite(site) {
  const root = document.getElementById("site-content"); root.replaceChildren(el("h2", site.display_name));
  const rows = site.cameras.map(camera => [
    String(camera.camera_id).padStart(2, "0"), yesNo(camera.snapshot_available),
    shown(camera.snapshot_success_rate), shown(camera.snapshot_success_count),
    shown(camera.snapshot_failure_count), shown(camera.snapshot_consecutive_failures),
    shown(camera.latest_attempt?.error_code),
    channelToggle(site.site_id, camera),
  ]);
  root.append(table(
    ["CH", "截圖可用", "成功率 %", "成功", "失敗", "連續失敗", "最近錯誤", "啟用"],
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
