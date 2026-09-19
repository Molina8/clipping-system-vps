/* Mission Control v2.5 overlay — loaded after app.js */
function statusPill(status) {
  const st = status || "—";
  return `<span class="mc-status-pill ${esc(String(st))}"><span class="dot"></span>${esc(st)}</span>`;
}
function pathLooksPendingUpload(fp) {
  if (!fp) return false;
  return /pending_upload/i.test(String(fp).replace(/\\/g, "/"));
}
function renderPathCell(localPath, baseUrl) {
  if (!localPath) return "<span class='mc-muted'>—</span>";
  if (baseUrl) {
    const url = baseUrl.replace(/\/$/, "") + "/" + String(localPath).replace(/\\/g, "/").replace(/^\/+/, "");
    return `<a href="${esc(url)}" target="_blank" rel="noopener" class="mc-path-link">${esc(localPath)}</a>`;
  }
  return `<code>${esc(localPath)}</code>`;
}

async function renderOverview(main) {
  stopPolling();
  const data = await api("/mission-control/overview");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  document.getElementById("base-url-status").textContent =
    "worker base: " + (data.worker_file_base_url || "(none — paths shown as text)");
  const camps = data.campaigns_by_status || {};
  const campTotal = Object.values(camps).reduce((a,b)=>a+b,0);
  const campScored = camps.scored || 0;
  const campBriefed = camps.briefed || 0;
  const assets = data.assets_by_status || {};
  const assetDl = assets.downloaded || 0;
  const assetTr = assets.transcribed || 0;
  const clipsQ = data.clips_by_qa_status || {};
  const clipPass = clipsQ.pass || 0;
  const clipFail = clipsQ.fail || 0;
  const clipRev  = clipsQ.review || 0;
  const jts = data.jobs_by_type_status || {};
  const activeJobs = Object.entries(jts).reduce(
    (sum, [_, byStatus]) => sum + (byStatus.pending||0) + (byStatus.assigned||0) + (byStatus.processing||0),
    0
  );
  const errCardClass = data.disk_unavailable_videos > 0 ? "err" : "";
  main.innerHTML = `
    <div class="section">
      <div class="mc-section-header">
        <div>
          <div class="mc-detail-eyebrow">Plataforma</div>
          <h2 class="mc-detail-title">Overview</h2>
        </div>
      </div>
      <div class="mc-tiles">
        <div class="mc-tile">
          <div class="mc-tile-head"><span class="mc-tile-label">Campaigns</span><span class="mc-tile-badge sky">${campTotal}</span></div>
          <div class="mc-tile-body"><span class="mc-tile-value">${campTotal}</span><span class="mc-tile-delta">${campScored} scored · ${campBriefed} briefed</span></div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill" style="width:100%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head"><span class="mc-tile-label">Active jobs</span><span class="mc-tile-badge amber">${activeJobs}</span></div>
          <div class="mc-tile-body"><span class="mc-tile-value">${activeJobs}</span><span class="mc-tile-delta amber">${data.total_jobs_last_24h} jobs in last 24h</span></div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill amber" style="width:${Math.min(100, Math.round(activeJobs * 10))}%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head"><span class="mc-tile-label">Assets downloaded</span><span class="mc-tile-badge emerald">${assetDl}</span></div>
          <div class="mc-tile-body"><span class="mc-tile-value">${assetDl}</span><span class="mc-tile-delta">${assetTr} transcribed</span></div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill emerald" style="width:${assetDl ? Math.min(100, Math.round(assetTr * 100 / assetDl)) : 0}%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head"><span class="mc-tile-label">Clips QA pass</span><span class="mc-tile-badge emerald">${clipPass}</span></div>
          <div class="mc-tile-body"><span class="mc-tile-value">${clipPass}</span><span class="mc-tile-delta">${clipFail} fail · ${clipRev} review</span></div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill emerald" style="width:${clipPass ? Math.min(100, Math.round(clipPass * 100 / (clipPass + clipFail + clipRev))) : 0}%"></div></div>
        </div>
        <div class="mc-tile ${errCardClass ? 'has-err' : ''}">
          <div class="mc-tile-head"><span class="mc-tile-label">Disk-unavailable videos</span><span class="mc-tile-badge rose">${data.disk_unavailable_videos}</span></div>
          <div class="mc-tile-body"><span class="mc-tile-value">${data.disk_unavailable_videos}</span><span class="mc-tile-delta rose">assets with no local_path & not pending</span></div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill rose" style="width:${Math.min(100, data.disk_unavailable_videos * 5)}%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head"><span class="mc-tile-label">Clips (24h)</span><span class="mc-tile-badge sky">${data.total_clips_last_24h}</span></div>
          <div class="mc-tile-body"><span class="mc-tile-value">${data.total_clips_last_24h}</span><span class="mc-tile-delta">created in the last 24 hours</span></div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill" style="width:100%"></div></div>
        </div>
      </div>
      <div class="mc-section-header"><h3>Pipeline campañas</h3></div>
      ${renderCampaignPipeline(camps)}
      <div class="mc-section-header"><h3>Assets por estado</h3></div>
      ${renderAssetPipeline(assets)}
      <div class="mc-section-header"><h3>Clips por QA / status</h3></div>
      ${renderClipPipeline(clipsQ, data.clips_by_status || {})}
      <div class="mc-section-header"><h3>Active jobs by type</h3></div>
      ${renderJobsByTypeTable(jts)}
      <div class="mc-section-header"><h3>Recent errors (last 5)</h3></div>
      ${renderRecentErrors(data.recent_errors || [])}
    </div>
  `;
  document.getElementById("auto-refresh-status").textContent = "10s";
  startPolling(renderOverview);
}

function renderCampaignPipeline(camps) {
  const stages = [["discovered","slate"],["briefed","amber"],["assets_resolved","sky"],["scored","emerald"],["failed_brief","rose"],["failed_resolve","rose"],["blocked_no_assets","rose"]];
  Object.keys(camps || {}).forEach((k) => { if (!stages.some(([n]) => n === k)) stages.push([k, "slate"]); });
  return `<div class="mc-pipeline">${stages.map(([name, tint], i) => {
    const arrow = i ? `<span class="mc-pipeline-arrow">→</span>` : "";
    return `${arrow}<div class="mc-stage mc-stage-${tint}"><div class="mc-stage-name">${esc(name)}</div><div class="mc-stage-count">${camps[name] || 0}</div></div>`;
  }).join("")}</div>`;
}
function renderAssetPipeline(assets) {
  const stages = [["pending","slate"],["downloaded","sky"],["transcribed","emerald"],["failed","rose"]];
  Object.keys(assets || {}).forEach((k) => { if (!stages.some(([n]) => n === k)) stages.push([k, "slate"]); });
  return `<div class="mc-pipeline">${stages.map(([name, tint], i) => {
    const arrow = i ? `<span class="mc-pipeline-arrow">→</span>` : "";
    return `${arrow}<div class="mc-stage mc-stage-${tint}"><div class="mc-stage-name">${esc(name)}</div><div class="mc-stage-count">${assets[name] || 0}</div></div>`;
  }).join("")}</div>`;
}
function renderClipPipeline(qa, st) {
  const qaStages = [["pass","emerald"],["fail","rose"],["review","amber"],["pending","slate"]];
  const qaHtml = qaStages.map(([name, tint]) => `<div class="mc-stage mc-stage-${tint}"><div class="mc-stage-name">qa ${esc(name)}</div><div class="mc-stage-count">${(qa && qa[name]) || 0}</div></div>`).join("");
  const keys = Object.keys(st || {}).sort();
  const stHtml = keys.length ? keys.map((name) => `<div class="mc-stage mc-stage-slate"><div class="mc-stage-name">${esc(name)}</div><div class="mc-stage-count">${st[name] || 0}</div></div>`).join("") : `<div class="mc-muted">Sin clips.status todavía.</div>`;
  return `<div class="mc-pipeline">${qaHtml}<span class="mc-pipeline-arrow">·</span>${stHtml}</div>`;
}

async function renderVideos(main) {
  stopPolling();
  const data = await api("/mission-control/videos");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  document.getElementById("base-url-status").textContent =
    "worker base: " + (data.worker_file_base_url || "(none — paths shown as text)");
  const items = data.items || [];
  const byStatus = items.reduce((acc, v) => { acc[v.status] = (acc[v.status] || 0) + 1; return acc; }, {});
  const filter = sessionStorage.getItem("mc_videos_status") || "";
  const shown = filter ? items.filter((v) => v.status === filter) : items;
  main.innerHTML = `
    <div class="section">
      <div class="mc-section-header"><div><div class="mc-detail-eyebrow">Plataforma</div>
        <h2 class="mc-detail-title">Videos <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${items.length}</span></h2></div></div>
      <p class="mc-muted">${data.worker_file_base_url
        ? `Paths clickables (base = <code>${esc(data.worker_file_base_url)}</code>). Inventario = downloaded + transcribed.`
        : "Sin <code>WORKER_FILE_BASE_URL</code> — paths en texto. Inventario = downloaded + transcribed."}</p>
      <div class="mc-toolbar"><div class="mc-tabs">
        <button type="button" class="mc-tab ${!filter ? "is-active" : ""}" data-st="">Todos <span class="mc-tab-count">${items.length}</span></button>
        ${["downloaded","transcribed"].map((st) => `<button type="button" class="mc-tab ${filter===st ? "is-active" : ""}" data-st="${st}">${st} <span class="mc-tab-count">${byStatus[st]||0}</span></button>`).join("")}
      </div></div>
      ${shown.length ? `
      <div class="mc-jobs-table-section"><div class="mc-jobs-table-head"><div><span class="mc-jobs-table-title">Assets en disco</span><span class="mc-jobs-table-title-pill">${shown.length}</span></div></div>
        <div style="overflow-x:auto;"><table class="mc-jobs-table"><thead><tr>
          <th>Campaña</th><th>Status</th><th>local_path</th><th class="num">Size</th><th class="num">Duration</th><th>Downloaded</th>
        </tr></thead><tbody>
      ${shown.map((v) => `<tr>
          <td><div class="mc-campaign-cell"><a class="mc-campaign-name" href="#/campaigns/${esc(v.campaign_id)}">${esc(v.campaign_name || "—")}</a><div class="mc-campaign-sub mc-mono">${esc((v.id || "").slice(0, 8))}…</div></div></td>
          <td>${statusPill(v.status)}</td>
          <td>${renderPathCell(v.local_path, data.worker_file_base_url)}</td>
          <td class="num mc-mono">${fmtBytes(v.file_size)}</td>
          <td class="num mc-mono">${fmtDuration(v.duration_seconds)}</td>
          <td class="mc-mono" style="color:var(--mc-slate-600);font-size:11.5px;">${fmtAgo(v.downloaded_at)}</td>
        </tr>`).join("")}
        </tbody></table></div></div>` : `<div class="mc-empty">No hay vídeos downloaded/transcribed.</div>`}
    </div>`;
  document.querySelectorAll(".mc-tab[data-st]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sessionStorage.setItem("mc_videos_status", btn.dataset.st || "");
      renderVideos(main);
    });
  });
  document.getElementById("auto-refresh-status").textContent = "off";
}

async function renderClips(main) {
  stopPolling();
  const qaFilter = sessionStorage.getItem("mc_clips_qa") || "";
  const qs = buildQuery({ limit: 200, qa_status: qaFilter });
  const data = await api("/mission-control/clips" + qs);
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  const items = data.items || [];
  const pendingUpload = items.filter((c) => pathLooksPendingUpload(c.file_path)).length;
  const locFilter = sessionStorage.getItem("mc_clips_loc") || "";
  const shown = locFilter === "pending_upload" ? items.filter((c) => pathLooksPendingUpload(c.file_path)) : items;
  main.innerHTML = `
    <div class="section">
      <div class="mc-section-header"><div><div class="mc-detail-eyebrow">Plataforma</div>
        <h2 class="mc-detail-title">Clips <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${items.length}</span></h2></div></div>
      <div class="mc-toolbar"><div class="mc-tabs">
        <button type="button" class="mc-tab ${!qaFilter ? "is-active" : ""}" data-qa="">QA all</button>
        ${["pass","fail","review","pending"].map((q) => `<button type="button" class="mc-tab ${qaFilter===q ? "is-active" : ""}" data-qa="${q}">${q}</button>`).join("")}
        <button type="button" class="mc-tab ${locFilter==="pending_upload" ? "is-active" : ""}" data-loc="pending_upload">pending_upload <span class="mc-tab-count">${pendingUpload}</span></button>
      </div></div>
      ${shown.length ? `<div class="mc-clips-grid">
      ${shown.map((c) => {
        const thumb = data.worker_file_base_url && c.file_path
          ? `<video src="${esc(data.worker_file_base_url.replace(/\/$/, "") + "/" + String(c.file_path).replace(/\\/g,"/").replace(/^\/+/,""))}" muted preload="metadata"></video>`
          : `<div class="mc-clip-noprev">sin preview</div>`;
        return `<div class="mc-clip-card" data-clip="${esc(c.id)}">
          <div class="mc-clip-thumb">${thumb}</div>
          <div class="mc-clip-body">
            <div class="mc-clip-badges">${statusPill(c.qa_status)} ${statusPill(c.status)} ${pathLooksPendingUpload(c.file_path) ? statusPill("pending_upload") : ""}</div>
            <div class="mc-clip-name">${esc(c.campaign_name || "—")} · ${fmtDuration(c.duration_seconds)}</div>
            <div class="mc-mono mc-clip-path" title="${esc(c.file_path || "")}">${esc(c.file_path || "—")}</div>
            <div class="mc-muted">${fmtAgo(c.created_at)}</div>
          </div></div>`;
      }).join("")}
      </div>` : `<div class="mc-empty">No hay clips con este filtro.</div>`}
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
      const on = sessionStorage.getItem("mc_clips_loc") === "pending_upload";
      sessionStorage.setItem("mc_clips_loc", on ? "" : "pending_upload");
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
  const videoUrl = baseUrl && c.file_path
    ? baseUrl.replace(/\/$/, "") + "/" + String(c.file_path).replace(/\\/g,"/").replace(/^\/+/,"")
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
      <dt>Location</dt><dd>${pathLooksPendingUpload(c.file_path) ? statusPill("pending_upload") : `<span class="mc-muted">—</span>`}</dd>
      <dt>Duration</dt><dd>${fmtDuration(c.duration_seconds)}</dd>
      <dt>Size</dt><dd>${fmtBytes(c.file_size)}</dd>
      <dt>Created</dt><dd class="mc-mono">${esc(fmtDateTime(c.created_at))}</dd>
      <dt>QA at</dt><dd class="mc-mono">${esc(fmtDateTime(c.qa_at))}</dd>
      <dt>Published</dt><dd class="mc-mono">${esc(fmtDateTime(c.published_at))}</dd>
      <dt>File path</dt><dd>${renderPathCell(c.file_path, baseUrl)}</dd>
    </dl>
    ${videoUrl ? `<video class="mc-clip-player" src="${esc(videoUrl)}" controls></video>` : `<div class="mc-muted">Sin worker_file_base_url — no hay player.</div>`}
    <div class="mc-modal-section"><h4 class="mc-modal-section-title">QA result</h4>
      <pre class="mc-modal-pre">${esc(JSON.stringify(c.qa_result || {}, null, 2))}</pre></div>`;
}
