/* Clipping Mission Control — vanilla JS, hash-routed SPA. */

const API_BASE = "";            // same origin (served by FastAPI/StaticFiles)
const POLL_MS = 10000;          // overview + jobs auto-refresh
let   POLL_TIMER = null;

// ---------- Token handling (sessionStorage only — never localStorage) ----

function getToken() {
  return sessionStorage.getItem("mc_token") || "";
}
function setToken(t) {
  sessionStorage.setItem("mc_token", t);
  updateAuthStatus();
}
function clearToken() {
  sessionStorage.removeItem("mc_token");
  updateAuthStatus();
}
function updateAuthStatus() {
  const t = getToken();
  document.getElementById("auth-status").textContent = t ? "token loaded" : "no token";
}
function authHeaders() {
  const t = getToken();
  return t ? { "Authorization": "Bearer " + t } : {};
}

// ---------- Fetch helper -------------------------------------------------

async function api(path, opts = {}) {
  const r = await fetch(API_BASE + path, {
    ...opts,
    headers: { ...(opts.headers || {}), ...authHeaders() },
  });
  if (r.status === 401 || r.status === 403) {
    throw new Error("auth_required");
  }
  if (r.status === 404) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || "not_found");
  }
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error("HTTP " + r.status + ": " + (t || r.statusText));
  }
  return r.json();
}

// ---------- Common helpers -----------------------------------------------

function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024*1024) return (n/1024).toFixed(1) + " KB";
  if (n < 1024*1024*1024) return (n/1024/1024).toFixed(1) + " MB";
  return (n/1024/1024/1024).toFixed(2) + " GB";
}
function fmtDuration(s) {
  if (s == null) return "—";
  const m = Math.floor(s/60), sec = Math.round(s%60);
  return `${m}:${String(sec).padStart(2,"0")}`;
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
function fmtAgo(iso) {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return iso;
  const s = Math.max(0, Math.floor(ms/1000));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s/60) + "m ago";
  if (s < 86400) return Math.floor(s/3600) + "h ago";
  return Math.floor(s/86400) + "d ago";
}
function esc(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
function pill(status) {
  return `<span class="pill ${esc(status)}">${esc(status || "—")}</span>`;
}
function buildQuery(params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== "") qs.set(k, v);
  }
  return qs.toString() ? "?" + qs.toString() : "";
}

// ---------- Worker file URL helper (file_path → link when base set) ------

function renderPathCell(localPath, baseUrl) {
  if (!localPath) return "<span class='muted'>—</span>";
  if (baseUrl) {
    const url = baseUrl.replace(/\/$/, "") + "/" + localPath.replace(/\\/g, "/").replace(/^\/+/, "");
    return `<a href="${esc(url)}" target="_blank" rel="noopener" class="mono">${esc(localPath)}</a>`;
  }
  return `<code>${esc(localPath)}</code>`;
}

// ---------- Modal ---------------------------------------------------------

function openModal(html) {
  const m = document.getElementById("modal");
  document.getElementById("modal-body").innerHTML = html;
  m.classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}
document.addEventListener("click", (e) => {
  if (e.target.id === "modal" || e.target.classList.contains("modal-backdrop")
      || e.target.classList.contains("modal-close")) {
    closeModal();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// ---------- Sidebar active + router --------------------------------------

function setActive(route) {
  document.querySelectorAll(".sidebar a[data-route]").forEach(a => {
    a.classList.toggle("active", a.dataset.route === route);
  });
}
window.addEventListener("hashchange", () => renderRoute());
function currentRoute() {
  const h = location.hash.replace(/^#\//, "") || "overview";
  return h.split("/")[0];
}
async function renderRoute() {
  const hash = location.hash.replace(/^#/, "");
  const parts = hash.split("/").filter(Boolean);
  const route = parts[0] || "overview";
  setActive(route);
  const main = document.getElementById("main");
  main.innerHTML = `<div class="empty">Loading…</div>`;
  document.getElementById("last-update").textContent = "loading…";
  try {
    if (route === "overview")        await renderOverview(main);
    else if (route === "campaigns")  await renderCampaigns(main, parts[1] ? parseInt(parts[1]) : null);
    else if (route === "jobs")        await renderJobs(main);
    else if (route === "videos")      await renderVideos(main);
    else if (route === "clips")       await renderClips(main);
    else main.innerHTML = `<div class="empty">Unknown route: ${esc(route)}</div>`;
    document.getElementById("last-update").textContent = "updated " + fmtDateTime(new Date().toISOString());
  } catch (e) {
    if (String(e.message) === "auth_required") {
      main.innerHTML = `<div class="error">No token — enter your Bearer token in the top-right.</div>`;
    } else if (String(e.message) === "not_found") {
      main.innerHTML = `<div class="error">Mission Control is disabled or this resource doesn't exist (404).</div>`;
    } else {
      main.innerHTML = `<div class="error">Error: ${esc(e.message)}</div>`;
    }
  }
}

// =======================================================================
// 1. Overview
// =======================================================================
async function renderOverview(main) {
  stopPolling();
  const data = await api("/mission-control/overview");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  document.getElementById("base-url-status").textContent =
    "worker base: " + (data.worker_file_base_url || "(none — paths shown as text)");

  const camps = data.campaigns_by_status || {};
  const campTotal = Object.values(camps).reduce((a,b)=>a+b,0);
  const campReady = camps.ready || 0;
  const campActive = camps.active || 0;
  const campDraft = camps.draft || 0;

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
      <h2>Overview</h2>
      <div class="cards">
        <div class="card">
          <div class="card-title">Campaigns</div>
          <div class="card-value">${campTotal}</div>
          <div class="card-sub">
            ${pill("ready")} ${campReady} ·
            ${pill("active")} ${campActive} ·
            ${pill("draft")} ${campDraft}
          </div>
        </div>
        <div class="card">
          <div class="card-title">Active jobs</div>
          <div class="card-value">${activeJobs}</div>
          <div class="card-sub">${data.total_jobs_last_24h} jobs in last 24h</div>
        </div>
        <div class="card">
          <div class="card-title">Assets downloaded</div>
          <div class="card-value">${assetDl}</div>
          <div class="card-sub">${assetTr} transcribed</div>
        </div>
        <div class="card">
          <div class="card-title">Clips QA pass</div>
          <div class="card-value">${clipPass}</div>
          <div class="card-sub">
            ${pill("fail")} ${clipFail} ·
            ${pill("review")} ${clipRev}
          </div>
        </div>
        <div class="card ${errCardClass}">
          <div class="card-title">Disk-unavailable videos</div>
          <div class="card-value">${data.disk_unavailable_videos}</div>
          <div class="card-sub">assets with no local_path &amp; not pending</div>
        </div>
        <div class="card">
          <div class="card-title">Clips (24h)</div>
          <div class="card-value">${data.total_clips_last_24h}</div>
          <div class="card-sub">created in the last 24 hours</div>
        </div>
      </div>

      <h3>Active jobs by type</h3>
      ${renderJobsByTypeTable(jts)}

      <h3>Recent errors (last 5)</h3>
      ${renderRecentErrors(data.recent_errors || [])}
    </div>
  `;
  document.getElementById("auto-refresh-status").textContent = "10s";
  startPolling(renderOverview);
}

function renderJobsByTypeTable(jts) {
  const rows = Object.entries(jts);
  if (!rows.length) return "<div class='muted'>No jobs in last 24h.</div>";
  let html = `<table class="tbl"><thead><tr>
    <th>Job type</th><th>pending</th><th>assigned</th><th>processing</th>
    <th>completed</th><th>failed</th><th>cancelled</th>
  </tr></thead><tbody>`;
  for (const [jt, byS] of rows) {
    html += `<tr>
      <td class="mono">${esc(jt)}</td>
      <td>${byS.pending||0}</td>
      <td>${byS.assigned||0}</td>
      <td>${byS.processing||0}</td>
      <td>${byS.completed||0}</td>
      <td>${byS.failed||0}</td>
      <td>${byS.cancelled||0}</td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

function renderRecentErrors(errs) {
  if (!errs.length) return "<div class='muted'>No errors. 🎉</div>";
  let html = `<table class="tbl"><thead><tr>
    <th>When</th><th>Type</th><th>Job</th><th>Error</th>
  </tr></thead><tbody>`;
  for (const e of errs) {
    html += `<tr>
      <td class="mono" title="${esc(e.created_at)}">${fmtAgo(e.created_at)}</td>
      <td><span class="mono">${esc(e.job_type)}</span></td>
      <td class="mono"><a href="#/jobs" onclick="event.preventDefault();">${esc(e.id.slice(0,8))}…</a></td>
      <td class="mono"><span class="truncate">${esc(e.error_message)}</span></td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

// =======================================================================
// 2. Campaigns (list + detail)
// =======================================================================
async function renderCampaigns(main, cid) {
  stopPolling();
  if (cid) return renderCampaignDetail(main, cid);

  const data = await api("/mission-control/campaigns");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || (data.items[0]?.worker_file_base_url || "");
  const items = data.items || [];
  if (!items.length) {
    main.innerHTML = `<div class="section"><h2>Campaigns</h2><div class="empty">No campaigns yet.</div></div>`;
    return;
  }

  let html = `<div class="section">
    <h2>Campaigns <span class="muted">(${items.length})</span></h2>
    <div class="grid">`;
  for (const c of items) {
    html += `
      <div class="campaign-card" data-cid="${c.id}" onclick="location.hash='#/campaigns/${c.id}'">
        <h4>${esc(c.name)}</h4>
        <div class="meta">
          <span>${pill(c.status)}</span>
          <span class="provider">${esc(c.source_provider)}</span>
        </div>
        <div class="counters">
          <span class="counter">assets ${c.assets_transcribed}/${c.assets_total}</span>
          <span class="counter">clips ${c.clips_approved_qa} pass</span>
          <span class="counter">${c.clips_total} total</span>
        </div>
      </div>`;
  }
  html += "</div></div>";
  main.innerHTML = html;
  document.getElementById("auto-refresh-status").textContent = "off";
}

async function renderCampaignDetail(main, cid) {
  const d = await api(`/mission-control/campaigns/${cid}`);
  window.WORKER_FILE_BASE_URL = d.worker_file_base_url || "";
  const c = d.campaign;
  const assets = d.assets || [];
  const jobs = d.active_jobs || [];
  const clips = d.clips || [];

  // Pipeline counts
  const pipeline = {
    source: assets.length,
    download: assets.filter(a => ["downloaded","transcribed","failed"].includes(a.status)).length,
    transcribe: assets.filter(a => a.status === "transcribed").length,
    render: clips.length,
    qa: clips.filter(cl => cl.qa_status && cl.qa_status !== "pending").length,
    published: clips.filter(cl => cl.status === "published").length,
  };
  function stage(label, n, key) {
    const cssClass = n > 0 ? (["published","approved","pass"].includes(key) ? "passed" : "pending") : "pending";
    return `<div class="stage ${cssClass}">
      <div class="stage-name">${esc(label)}</div>
      <div class="stage-count">${n}</div>
    </div>`;
  }

  main.innerHTML = `
    <div class="section">
      <h2>${esc(c.name)} <span style="font-weight:normal;">${pill(c.status)} <span class="muted">· ${esc(c.source_provider)}</span></span></h2>
      <p class="muted">
        id ${c.id} ·
        ${c.source_url ? `<a href="${esc(c.source_url)}" target="_blank" rel="noopener">source ↗</a>` : "no source url"} ·
        updated ${fmtAgo(c.updated_at)}
      </p>

      <h3>Pipeline</h3>
      <div class="pipeline">
        ${stage("Source", pipeline.source, "source")}
        <span class="arrow">→</span>
        ${stage("Download", pipeline.download, "downloaded")}
        <span class="arrow">→</span>
        ${stage("Transcribe", pipeline.transcribe, "transcribed")}
        <span class="arrow">→</span>
        ${stage("Render", pipeline.render, "render")}
        <span class="arrow">→</span>
        ${stage("QA", pipeline.qa, "qa")}
        <span class="arrow">→</span>
        ${stage("Published", pipeline.published, "published")}
      </div>

      <div class="tabs">
        <button data-tab="assets" class="active">Assets (${assets.length})</button>
        <button data-tab="jobs">Active jobs (${jobs.length})</button>
        <button data-tab="clips">Clips (${clips.length})</button>
      </div>
      <div class="tab-content" id="tab-content"></div>
    </div>
  `;

  function activateTab(t) {
    document.querySelectorAll(".tabs button").forEach(b =>
      b.classList.toggle("active", b.dataset.tab === t));
    const tc = document.getElementById("tab-content");
    if (t === "assets") tc.innerHTML = renderAssetsTable(assets);
    if (t === "jobs")   tc.innerHTML = renderJobsTable(jobs.map(j => ({...j, campaign_name: c.name})));
    if (t === "clips")  tc.innerHTML = renderClipsTable(clips.map(cl => ({...cl, campaign_name: c.name})));
  }
  document.querySelectorAll(".tabs button").forEach(b => {
    b.addEventListener("click", () => activateTab(b.dataset.tab));
  });
  activateTab("assets");
  document.getElementById("auto-refresh-status").textContent = "off";
}

function renderAssetsTable(rows) {
  if (!rows.length) return "<div class='empty'>No assets.</div>";
  let html = `<table class="tbl"><thead><tr>
    <th>ID</th><th>Status</th><th>Source</th><th>local_path</th>
    <th>Size</th><th>Duration</th><th>Downloaded</th>
  </tr></thead><tbody>`;
  for (const a of rows) {
    html += `<tr>
      <td class="mono">${esc(a.id.slice(0,8))}…</td>
      <td>${pill(a.status)}</td>
      <td><span class="truncate mono"><a href="${esc(a.source_url)}" target="_blank" rel="noopener">${esc(a.source_url)}</a></span></td>
      <td>${renderPathCell(a.local_path, window.WORKER_FILE_BASE_URL)}</td>
      <td class="right">${fmtBytes(a.file_size)}</td>
      <td class="right">${fmtDuration(a.duration_seconds)}</td>
      <td class="mono">${fmtAgo(a.downloaded_at)}</td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

// =======================================================================
// 3. Jobs
// =======================================================================
async function renderJobs(main) {
  stopPolling();
  const typeFilter = sessionStorage.getItem("mc_jobs_type") || "";
  const statFilter = sessionStorage.getItem("mc_jobs_status") || "";
  const qs = buildQuery({ limit: 100, job_type: typeFilter, status: statFilter });
  const data = await api(`/mission-control/jobs/recent${qs}`);

  const types = ["download","transcribe","render","qa","health"];
  const stats = ["pending","assigned","processing","completed","failed","cancelled"];

  main.innerHTML = `
    <div class="section">
      <h2>Jobs <span class="muted">(${data.count})</span></h2>
      <div class="controls">
        <label>type
          <select id="jobs-type">
            <option value="">all</option>
            ${types.map(t => `<option value="${t}" ${t===typeFilter?'selected':''}>${t}</option>`).join('')}
          </select>
        </label>
        <label>status
          <select id="jobs-status">
            <option value="">all</option>
            ${stats.map(s => `<option value="${s}" ${s===statFilter?'selected':''}>${s}</option>`).join('')}
          </select>
        </label>
        <button id="jobs-refresh">Refresh now</button>
      </div>
      ${renderJobsTable(data.items)}
    </div>
  `;
  document.getElementById("jobs-type").addEventListener("change", e => {
    sessionStorage.setItem("mc_jobs_type", e.target.value);
    renderJobs(main);
  });
  document.getElementById("jobs-status").addEventListener("change", e => {
    sessionStorage.setItem("mc_jobs_status", e.target.value);
    renderJobs(main);
  });
  document.getElementById("jobs-refresh").addEventListener("click", () => renderJobs(main));
  document.getElementById("auto-refresh-status").textContent = "10s";
  startPolling(renderJobs);
}

function renderJobsTable(rows) {
  if (!rows.length) return "<div class='empty'>No jobs match the filter.</div>";
  let html = `<table class="tbl"><thead><tr>
    <th>ID</th><th>Type</th><th>Status</th><th>Priority</th>
    <th>Attempt</th><th>Campaign</th><th>Worker</th>
    <th>Elapsed</th><th>When</th><th>Error</th>
  </tr></thead><tbody>`;
  for (const j of rows) {
    html += `<tr data-job="${esc(j.id)}" style="cursor:pointer">
      <td class="mono">${esc(j.id.slice(0,8))}…</td>
      <td class="mono">${esc(j.job_type)}</td>
      <td>${pill(j.status)}</td>
      <td>${j.priority}</td>
      <td>${j.attempts}/${j.max_attempts}</td>
      <td>${esc(j.campaign_name || "—")}</td>
      <td class="mono">${esc(j.worker_id || "—")}</td>
      <td class="right">${fmtDuration(j.elapsed_seconds)}</td>
      <td class="mono" title="${esc(j.created_at)}">${fmtAgo(j.updated_at)}</td>
      <td class="mono"><span class="truncate">${esc(j.error_message || "")}</span></td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

document.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-job]");
  if (!tr) return;
  const id = tr.dataset.job;
  fetch(API_BASE + "/jobs/" + id, { headers: authHeaders() })
    .then(r => r.json())
    .then(job => openModal(renderJobModal(job)))
    .catch(err => openModal(`<div class="error">${esc(err.message)}</div>`));
});
function renderJobModal(j) {
  const timeline = `
    <div class="timeline">
      <div class="step mono">created ${esc(fmtDateTime(j.created_at))}</div>
      →
      <div class="step mono">started ${esc(fmtDateTime(j.started_at) || "—")}</div>
      →
      <div class="step mono">completed ${esc(fmtDateTime(j.completed_at) || "—")}</div>
    </div>
  `;
  return `
    <h3>Job ${esc(j.id)}</h3>
    <dl class="kv">
      <dt>Type</dt><dd class="mono">${esc(j.job_type)}</dd>
      <dt>Status</dt><dd>${pill(j.status)}</dd>
      <dt>Priority</dt><dd>${j.priority}</dd>
      <dt>Attempts</dt><dd>${j.attempts}/${j.max_attempts}</dd>
      <dt>Worker</dt><dd class="mono">${esc(j.worker_id || "—")}</dd>
    </dl>
    <h3>Timeline</h3>
    ${timeline}
    <h3>Payload</h3>
    <pre>${esc(JSON.stringify(j.payload, null, 2))}</pre>
    <h3>Result</h3>
    <pre>${esc(JSON.stringify(j.result, null, 2))}</pre>
    ${j.error_message ? `<h3>Error</h3><pre>${esc(j.error_message)}</pre>` : ""}
  `;
}

// =======================================================================
// 4. Videos
// =======================================================================
async function renderVideos(main) {
  stopPolling();
  const data = await api("/mission-control/videos");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  document.getElementById("base-url-status").textContent =
    "worker base: " + (data.worker_file_base_url || "(none — paths shown as text)");
  const items = data.items || [];
  main.innerHTML = `
    <div class="section">
      <h2>Downloaded videos <span class="muted">(${items.length})</span></h2>
      <p class="muted">${data.worker_file_base_url
        ? `Paths are clickable (base = <code>${esc(data.worker_file_base_url)}</code>).`
        : "No <code>WORKER_FILE_BASE_URL</code> configured — paths shown as text only."}</p>
      ${items.length ? `
      <table class="tbl"><thead><tr>
        <th>Campaign</th><th>Status</th><th>local_path</th>
        <th>Size</th><th>Duration</th><th>Downloaded</th>
      </tr></thead><tbody>` : ""}
      ${items.map(v => `
        <tr>
          <td>${esc(v.campaign_name)}</td>
          <td>${pill(v.status)}</td>
          <td>${renderPathCell(v.local_path, data.worker_file_base_url)}</td>
          <td class="right">${fmtBytes(v.file_size)}</td>
          <td class="right">${fmtDuration(v.duration_seconds)}</td>
          <td class="mono">${fmtAgo(v.downloaded_at)}</td>
        </tr>
      `).join('')}
      ${items.length ? "</tbody></table>" : "<div class='empty'>No downloaded videos yet.</div>"}
    </div>
  `;
  document.getElementById("auto-refresh-status").textContent = "off";
}

// =======================================================================
// 5. Clips
// =======================================================================
async function renderClips(main) {
  stopPolling();
  const data = await api("/mission-control/clips");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || "";
  const items = data.items || [];
  main.innerHTML = `
    <div class="section">
      <h2>Clips <span class="muted">(${items.length})</span></h2>
      ${items.length ? `
      <div class="clips-grid">` : "<div class='empty'>No clips yet.</div>"}
      ${items.map(c => `
        <div class="clip-card" data-clip="${esc(c.id)}">
          <div class="clip-thumb">
            ${data.worker_file_base_url && c.file_path
              ? `<video src="${esc(data.worker_file_base_url.replace(/\/$/, '') + '/' + c.file_path.replace(/\\/g,'/').replace(/^\/+/,''))}" muted preload="metadata"></video>`
              : "no preview"}
          </div>
          <div style="margin-top:6px">${pill(c.qa_status)} ${pill(c.status)}</div>
          <div class="clip-meta">
            <div class="mono truncate" title="${esc(c.file_path || '')}">${esc(c.file_path || '—')}</div>
            <div>${esc(c.campaign_name || '—')} · ${fmtDuration(c.duration_seconds)}</div>
            <div class="muted">${fmtAgo(c.created_at)}</div>
          </div>
        </div>
      `).join('')}
      ${items.length ? `</div>` : ""}
    </div>
  `;
  document.getElementById("auto-refresh-status").textContent = "off";
  document.querySelectorAll(".clip-card").forEach(card => {
    card.addEventListener("click", () => {
      const c = items.find(x => x.id === card.dataset.clip);
      if (c) openModal(renderClipModal(c, data.worker_file_base_url));
    });
  });
}
function renderClipModal(c, baseUrl) {
  const videoUrl = baseUrl && c.file_path
    ? baseUrl.replace(/\/$/, '') + '/' + c.file_path.replace(/\\/g,'/').replace(/^\/+/,'')
    : null;
  return `
    <h3>Clip ${esc(c.id)}</h3>
    <dl class="kv">
      <dt>Campaign</dt><dd>${esc(c.campaign_name || '—')}</dd>
      <dt>QA status</dt><dd>${pill(c.qa_status)}</dd>
      <dt>Status</dt><dd>${pill(c.status)}</dd>
      <dt>Duration</dt><dd>${fmtDuration(c.duration_seconds)}</dd>
      <dt>Size</dt><dd>${fmtBytes(c.file_size)}</dd>
      <dt>Created</dt><dd class="mono">${esc(fmtDateTime(c.created_at))}</dd>
      <dt>QA at</dt><dd class="mono">${esc(fmtDateTime(c.qa_at))}</dd>
      <dt>Published</dt><dd class="mono">${esc(fmtDateTime(c.published_at))}</dd>
      <dt>File path</dt><dd>${renderPathCell(c.file_path, baseUrl)}</dd>
    </dl>
    ${videoUrl ? `<video src="${esc(videoUrl)}" controls style="width:100%;max-height:60vh;background:#000;"></video>` : "<div class='muted'>No <code>worker_file_base_url</code> — can't render player.</div>"}
    <h3>QA result</h3>
    <pre>${esc(JSON.stringify(c.qa_result || {}, null, 2))}</pre>
  `;
}

// =======================================================================
// Polling
// =======================================================================
function startPolling(fn) {
  stopPolling();
  POLL_TIMER = setInterval(() => {
    if (document.hidden) return;
    renderRoute().catch(() => {});
  }, POLL_MS);
}
function stopPolling() {
  if (POLL_TIMER) { clearInterval(POLL_TIMER); POLL_TIMER = null; }
  document.getElementById("auto-refresh-status").textContent = "off";
}

// =======================================================================
// Wire up
// =======================================================================
document.getElementById("save-token").addEventListener("click", () => {
  const t = document.getElementById("token").value.trim();
  if (t) setToken(t);
  else clearToken();
  renderRoute();
});
document.getElementById("token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("save-token").click();
});
// Pre-fill token from sessionStorage
const existing = getToken();
if (existing) document.getElementById("token").value = existing;
updateAuthStatus();
window.addEventListener("DOMContentLoaded", () => {
  if (!location.hash) location.hash = "#/overview";
  renderRoute();
});
