/* Location + storage path overlay — loaded after mc-v25.js */
function clipStoragePath(c) {
  if (!c) return "";
  return c.final_path_worker || (c.qa_result && c.qa_result.final_path_worker) || c.file_path || "";
}
function clipLocation(c) {
  if (c && c.location) return c.location;
  const p = String(clipStoragePath(c)).replace(/\\/g, "/");
  if (/\/uploaded\//i.test(p)) return "uploaded";
  if (/\/archived\//i.test(p)) return "archived";
  if (/pending_upload/i.test(p)) return "pending_upload";
  return "";
}
function locPill(loc) {
  if (!loc) return `<span class="mc-muted">sin location</span>`;
  return statusPill(loc);
}
async function hydrateClipLocations(items) {
  try {
    const canon = await api("/clips?limit=200");
    const list = Array.isArray(canon) ? canon : (canon.items || []);
    const byId = {};
    list.forEach((x) => { byId[x.id] = x; });
    (items || []).forEach((c) => {
      const e = byId[c.id];
      if (!e) return;
      if (e.location) c.location = e.location;
      if (e.final_path_worker) c.final_path_worker = e.final_path_worker;
      if (e.published_at) c.published_at = e.published_at;
      if (e.publish_approved_at) c.publish_approved_at = e.publish_approved_at;
    });
  } catch (err) {
    console.warn("hydrateClipLocations", err);
  }
}
async function renderClips(main) {
  stopPolling();
  const qaFilter = sessionStorage.getItem("mc_clips_qa") || "";
  const qs = buildQuery({ limit: 200, qa_status: qaFilter });
  const data = await api("/mission-control/clips" + qs);
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  const items = data.items || [];
  await hydrateClipLocations(items);
  const locOf = (c) => clipLocation(c);
  const nPending = items.filter((c) => locOf(c) === "pending_upload").length;
  const nUploaded = items.filter((c) => locOf(c) === "uploaded").length;
  const nArchived = items.filter((c) => locOf(c) === "archived").length;
  const locFilter = sessionStorage.getItem("mc_clips_loc") || "";
  const shown = locFilter ? items.filter((c) => locOf(c) === locFilter) : items;
  main.innerHTML = `
    <div class="section">
      <div class="mc-section-header"><div><div class="mc-detail-eyebrow">Plataforma</div>
        <h2 class="mc-detail-title">Clips <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${items.length}</span></h2></div></div>
      <div class="mc-toolbar"><div class="mc-tabs">
        <button type="button" class="mc-tab ${!qaFilter ? "is-active" : ""}" data-qa="">QA all</button>
        ${["pass","fail","review","pending"].map((q) => `<button type="button" class="mc-tab ${qaFilter===q ? "is-active" : ""}" data-qa="${q}">${q}</button>`).join("")}
        <button type="button" class="mc-tab ${locFilter==="pending_upload" ? "is-active" : ""}" data-loc="pending_upload">pending_upload <span class="mc-tab-count">${nPending}</span></button>
        <button type="button" class="mc-tab ${locFilter==="uploaded" ? "is-active" : ""}" data-loc="uploaded">uploaded <span class="mc-tab-count">${nUploaded}</span></button>
        <button type="button" class="mc-tab ${locFilter==="archived" ? "is-active" : ""}" data-loc="archived">archived <span class="mc-tab-count">${nArchived}</span></button>
      </div></div>
      ${shown.length ? `<div class="mc-clips-grid">${shown.map((c) => {
        const storagePath = clipStoragePath(c);
        const loc = clipLocation(c);
        const thumb = data.worker_file_base_url && storagePath
          ? `<video src="${esc(data.worker_file_base_url.replace(/\/$/, "") + "/" + String(storagePath).replace(/\\/g,"/").replace(/^\/+/,""))}" muted preload="metadata"></video>`
          : `<div class="mc-clip-noprev">sin preview</div>`;
        return `<div class="mc-clip-card" data-clip="${esc(c.id)}"><div class="mc-clip-thumb">${thumb}</div><div class="mc-clip-body">
            <div class="mc-clip-badges">${locPill(loc)} ${statusPill(c.qa_status)} ${statusPill(c.status)}${c.publish_approved_at ? statusPill("gate") : ""}</div>
            <div class="mc-clip-name">${esc(c.campaign_name || "—")} · ${fmtDuration(c.duration_seconds)}</div>
            <div class="mc-mono mc-clip-id" title="${esc(c.id || "")}">${esc(c.id || "—")}</div>
            <div class="mc-mono mc-clip-path" title="${esc(storagePath)}">${esc(storagePath || "—")}</div>
            <div class="mc-muted">${fmtAgo(c.created_at)}${c.published_at ? " · pub " + fmtAgo(c.published_at) : ""}</div>
          </div></div>`;
      }).join("")}</div>` : `<div class="mc-empty">No hay clips con este filtro.</div>`}
    </div>`;
  document.querySelectorAll(".mc-tab[data-qa]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sessionStorage.setItem("mc_clips_qa", btn.dataset.qa || "");
      sessionStorage.setItem("mc_clips_loc", "");
      renderClips(main);
    });
  });
  document.querySelectorAll(".mc-tab[data-loc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cur = sessionStorage.getItem("mc_clips_loc") || "";
      const next = btn.dataset.loc || "";
      sessionStorage.setItem("mc_clips_loc", cur === next ? "" : next);
      renderClips(main);
    });
  });
  document.querySelectorAll(".mc-clip-card").forEach((card) => {
    card.addEventListener("click", () => {
      const c = items.find((x) => x.id === card.dataset.clip);
      if (c) openModal(renderClipModal(c, data.worker_file_base_url));
    });
  });
  document.getElementById("auto-refresh-status").textContent = "off";
}
function renderClipModal(c, baseUrl) {
  const storagePath = clipStoragePath(c);
  const videoUrl = baseUrl && storagePath
    ? baseUrl.replace(/\/$/, "") + "/" + String(storagePath).replace(/\\/g,"/").replace(/^\/+/,"")
    : null;
  return `
    <div class="mc-modal-header"><div class="mc-modal-header-left">
      <div class="mc-modal-eyebrow">Clip</div>
      <h3 class="mc-modal-title">${esc((c.id || "").slice(0, 8))}…</h3>
      <div class="mc-modal-full-id">${esc(c.id || "")}</div>
    </div></div>
    <dl class="mc-kv">
      <dt>Campaign</dt><dd>${esc(c.campaign_name || "—")}</dd>
      <dt>QA</dt><dd>${statusPill(c.qa_status)}</dd>
      <dt>Status</dt><dd>${statusPill(c.status)}</dd>
      <dt>Location</dt><dd>${locPill(clipLocation(c))}</dd>
      <dt>Clip id</dt><dd class="mc-mono">${esc(c.id || "")}</dd>
      <dt>Duration</dt><dd>${fmtDuration(c.duration_seconds)}</dd>
      <dt>Size</dt><dd>${fmtBytes(c.file_size)}</dd>
      <dt>Published</dt><dd class="mc-mono">${esc(fmtDateTime(c.published_at))}</dd>
      <dt>Storage path</dt><dd>${renderPathCell(storagePath, baseUrl)}</dd>
      <dt>Render path</dt><dd>${renderPathCell(c.file_path, baseUrl)}</dd>
    </dl>
    ${videoUrl ? `<video class="mc-clip-player" src="${esc(videoUrl)}" controls></video>` : `<div class="mc-muted">Sin worker_file_base_url — no hay player.</div>`}
    <div class="mc-modal-section"><h4 class="mc-modal-section-title">QA result</h4>
      <pre class="mc-modal-pre">${esc(JSON.stringify(c.qa_result || {}, null, 2))}</pre></div>`;
}
function renderClipsTable(rows) {
  if (!rows.length) return `<div class="mc-empty">No clips.</div>`;
  hydrateClipLocations(rows);
  let html = `<div class="mc-jobs-table-section"><div class="mc-jobs-table-head"><div><span class="mc-jobs-table-title">Clips</span><span class="mc-jobs-table-title-pill">${rows.length}</span></div></div><div style="overflow-x:auto;"><table class="mc-jobs-table"><thead><tr><th>ID</th><th>Location</th><th>Status / QA</th><th>Path</th><th class="num">Duration</th><th>Created</th></tr></thead><tbody>`;
  for (const cl of rows) {
    const loc = clipLocation(cl);
    const sp = clipStoragePath(cl);
    html += `<tr><td><span class="mc-id-pill" title="${esc(cl.id || "")}">${esc(cl.id || "—")}</span></td><td>${locPill(loc)}</td><td><div class="mc-status-cell"><span class="mc-status-pill ${esc(cl.status || "draft")}"><span class="dot"></span>${esc(cl.status || "draft")}</span><div class="mc-status-meta">QA: ${esc(cl.qa_status || "pending")}</div></div></td><td><span class="mc-mono" style="font-size:11px;word-break:break-all;">${esc(sp || "—")}</span></td><td class="num mc-mono">${esc(fmtDuration(cl.duration_seconds))}</td><td class="mc-mono">${esc(fmtAgo(cl.created_at))}</td></tr>`;
  }
  html += `</tbody></table></div></div>`;
  return html;
}
