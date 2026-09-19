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
  const norm = (r) => {
    if (!r) return "";
    if (r.startsWith("campaigns/") || r === "campaigns") return "campaigns";
    if (r.startsWith("jobs/")     || r === "jobs")     return "jobs";
    if (r.startsWith("videos/")   || r === "videos")   return "videos";
    if (r.startsWith("clips/")    || r === "clips")    return "clips";
    return r;
  };
  const active = norm(route);
  document.querySelectorAll(".mc-sidebar-link[data-route]").forEach(a => {
    const on = a.dataset.route === active;
    a.classList.toggle("is-active", on);
    const count = a.querySelector(".mc-sidebar-count");
    if (count) count.classList.toggle("mc-sidebar-count-active", on);
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
          <div class="mc-tile-head">
            <span class="mc-tile-label">Campaigns</span>
            <span class="mc-tile-badge sky">${campTotal}</span>
          </div>
          <div class="mc-tile-body">
            <span class="mc-tile-value">${campTotal}</span>
            <span class="mc-tile-delta">${campScored} scored · ${campBriefed} briefed</span>
          </div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill" style="width:100%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head">
            <span class="mc-tile-label">Active jobs</span>
            <span class="mc-tile-badge amber">${activeJobs}</span>
          </div>
          <div class="mc-tile-body">
            <span class="mc-tile-value">${activeJobs}</span>
            <span class="mc-tile-delta amber">${data.total_jobs_last_24h} jobs in last 24h</span>
          </div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill amber" style="width:${Math.min(100, Math.round(activeJobs * 10))}%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head">
            <span class="mc-tile-label">Assets downloaded</span>
            <span class="mc-tile-badge emerald">${assetDl}</span>
          </div>
          <div class="mc-tile-body">
            <span class="mc-tile-value">${assetDl}</span>
            <span class="mc-tile-delta">${assetTr} transcribed</span>
          </div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill emerald" style="width:${assetDl ? Math.min(100, Math.round(assetTr * 100 / assetDl)) : 0}%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head">
            <span class="mc-tile-label">Clips QA pass</span>
            <span class="mc-tile-badge emerald">${clipPass}</span>
          </div>
          <div class="mc-tile-body">
            <span class="mc-tile-value">${clipPass}</span>
            <span class="mc-tile-delta">${clipFail} fail · ${clipRev} review</span>
          </div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill emerald" style="width:${clipPass ? Math.min(100, Math.round(clipPass * 100 / (clipPass + clipFail + clipRev))) : 0}%"></div></div>
        </div>
        <div class="mc-tile ${errCardClass ? 'has-err' : ''}">
          <div class="mc-tile-head">
            <span class="mc-tile-label">Disk-unavailable videos</span>
            <span class="mc-tile-badge rose">${data.disk_unavailable_videos}</span>
          </div>
          <div class="mc-tile-body">
            <span class="mc-tile-value">${data.disk_unavailable_videos}</span>
            <span class="mc-tile-delta rose">assets with no local_path &amp; not pending</span>
          </div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill rose" style="width:${Math.min(100, data.disk_unavailable_videos * 5)}%"></div></div>
        </div>
        <div class="mc-tile">
          <div class="mc-tile-head">
            <span class="mc-tile-label">Clips (24h)</span>
            <span class="mc-tile-badge sky">${data.total_clips_last_24h}</span>
          </div>
          <div class="mc-tile-body">
            <span class="mc-tile-value">${data.total_clips_last_24h}</span>
            <span class="mc-tile-delta">created in the last 24 hours</span>
          </div>
          <div class="mc-tile-bar"><div class="mc-tile-bar-fill" style="width:100%"></div></div>
        </div>
      </div>

      <div class="mc-section-header">
        <h3>Active jobs by type</h3>
      </div>
      ${renderJobsByTypeTable(jts)}

      <div class="mc-section-header">
        <h3>Recent errors (last 5)</h3>
      </div>
      ${renderRecentErrors(data.recent_errors || [])}
    </div>
  `;
  document.getElementById("auto-refresh-status").textContent = "10s";
  startPolling(renderOverview);
}

function renderJobsByTypeTable(jts) {
  const rows = Object.entries(jts);
  if (!rows.length) return "<div class='mc-empty'>No jobs in last 24h.</div>";
  let html = `<div class="mc-table-wrap"><table class="mc-table"><thead><tr>
    <th>Job type</th><th class="num">pending</th><th class="num">assigned</th><th class="num">processing</th>
    <th class="num">completed</th><th class="num">failed</th><th class="num">cancelled</th>
  </tr></thead><tbody>`;
  for (const [jt, byS] of rows) {
    html += `<tr>
      <td><span class="mc-mono">${esc(jt)}</span></td>
      <td class="num">${byS.pending||0}</td>
      <td class="num">${byS.assigned||0}</td>
      <td class="num">${byS.processing||0}</td>
      <td class="num">${byS.completed||0}</td>
      <td class="num">${byS.failed||0}</td>
      <td class="num">${byS.cancelled||0}</td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

function renderRecentErrors(errs) {
  if (!errs.length) return `<div class="mc-empty">No errors. 🎉</div>`;
  let html = `
    <div class="mc-jobs-table-section">
      <div class="mc-jobs-table-head">
        <div>
          <span class="mc-jobs-table-title">Recent errors</span>
          <span class="mc-jobs-table-title-pill">${errs.length} registro${errs.length === 1 ? '' : 's'}</span>
        </div>
        <div class="mc-jobs-table-head-right">
          <span class="dot"></span>Error crítico
        </div>
      </div>
      <div style="overflow-x:auto;">
        <table class="mc-jobs-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Tipo</th>
              <th>Job</th>
              <th style="min-width:340px;">Traceback / Detalle</th>
            </tr>
          </thead>
          <tbody>`;
  for (const e of errs) {
    const trace = (e.error_message || "").split("\n");
    const traceHead = trace[0] || "Error";
    const traceBody = trace.slice(1).join("\n").slice(0, 240);
    const id8 = (e.id || "").slice(0, 8);
    html += `
      <tr>
        <td class="mc-mono" title="${esc(e.created_at)}" style="font-size:11.5px;color:var(--mc-slate-600);">${esc(fmtAgo(e.created_at))}</td>
        <td><span class="mc-type-pill">${esc(e.job_type)}</span></td>
        <td><span class="mc-id-pill">${esc(id8)}…</span></td>
        <td>
          <div class="mc-traceback">
            <div class="mc-traceback-head">${esc(traceHead)}</div>
            <div class="mc-traceback-body">${esc(traceBody)}</div>
          </div>
        </td>
      </tr>`;
  }
  html += `</tbody></table></div></div>`;
  return html;
}

// =======================================================================
// 2. Campaigns (list + detail)
// =======================================================================
// Pipeline status order (left → right, top → bottom). Unknown statuses
// get appended at the end so we never drop a campaign from the view.
const CAMPAIGN_STATUS_ORDER = [
  "discovered",         // brief-reader-tick hasn't touched it yet
  "briefed",            // brief done, assets not resolved
  "assets_resolved",    // drive/dropbox resolvers expanded folder assets
  "scored",             // campaign-scorer wrote priority_score + tier
  "failed_brief",
  "failed_resolve",
  "blocked_no_assets",
];

async function renderCampaigns(main, cid) {
  stopPolling();
  if (cid) return renderCampaignDetail(main, cid);

  const data = await api("/mission-control/campaigns");
  window.WORKER_FILE_BASE_URL = data.worker_file_base_url || (data.items[0]?.worker_file_base_url || "");
  const items = data.items || [];
  if (!items.length) {
    main.innerHTML = `<div class="section">
      <div class="mc-section-header">
        <div>
          <div class="mc-detail-eyebrow">Plataforma</div>
          <h2 class="mc-detail-title">Campaigns</h2>
        </div>
      </div>
      <div class="empty">No campaigns yet.</div>
    </div>`;
    return;
  }

  // Status → color hint for the tab + group dot (matches Stitch palette).
  const STATUS_TINT = {
    discovered:      "slate",
    briefed:         "amber",
    assets_resolved: "sky",
    scored:          "emerald",
    failed_brief:    "rose",
    failed_resolve:  "rose",
    blocked_no_assets: "rose",
    unknown:         "slate",
  };
  // Human label per status (Stitch used TitleCase for the tab names).
  const STATUS_LABEL = {
    discovered:      "Discovered",
    briefed:         "Briefed",
    assets_resolved: "Assets Resolved",
    scored:          "Scored",
    failed_brief:    "Failed Brief",
    failed_resolve:  "Failed Resolve",
    blocked_no_assets: "Blocked Assets",
    unknown:         "Unknown",
  };

  // Show ALL pipeline statuses (and "unknown") even if no campaigns use
  // them yet — the user wants the full filter bar visible.
  const allStatuses = [...CAMPAIGN_STATUS_ORDER, "unknown"];

  main.innerHTML = `
    <div class="section">
      <div class="mc-section-header">
        <div>
          <div class="mc-detail-eyebrow">Plataforma</div>
          <h2 class="mc-detail-title">Campaigns <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${items.length}</span></h2>
        </div>
      </div>
      <div class="mc-toolbar">
        <div class="mc-search-row">
          <div class="mc-search">
            <svg class="mc-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            <input id="mc-campaign-search" type="text" placeholder="Buscar por nombre o ID..." autocomplete="off" />
            <span id="mc-campaign-search-count" class="mc-search-count">${items.length}/${items.length}</span>
          </div>
        </div>
        <div class="mc-tabs" id="mc-filter-tabs" role="tablist" aria-label="Filter by status"></div>
      </div>
      <div id="mc-campaign-list"></div>
    </div>`;

  const searchEl = document.getElementById("mc-campaign-search");
  const tabsEl = document.getElementById("mc-filter-tabs");
  const listEl = document.getElementById("mc-campaign-list");
  const countEl = document.getElementById("mc-campaign-search-count");

  // Active filter (null = "all"). Single-select for clarity.
  let activeStatus = null;

  // Build tabs: "All" + one per status (always all of them, even if 0).
  function buildTabs() {
    const totalFor = (s) => items.filter(c => (c.status || "unknown") === s).length;
    const tabs = [{ key: null, label: "Todos", count: items.length, tint: null }];
    for (const s of allStatuses) {
      const tint = STATUS_TINT[s] || "slate";
      tabs.push({ key: s, label: STATUS_LABEL[s] || s, count: totalFor(s), tint });
    }
    tabsEl.innerHTML = tabs.map(t => {
      const tintCls = t.tint ? `tint-${t.tint}` : "";
      const dot = t.tint ? `<span class="mc-tab-dot"></span>` : "";
      return `
        <button type="button" class="mc-tab ${tintCls} ${t.key === null ? "is-active" : ""}" data-status="${esc(t.key ?? "")}">
          ${dot}
          <span>${esc(t.label)}</span>
          <span class="mc-tab-count">${t.count}</span>
        </button>`;
    }).join("");
    tabsEl.querySelectorAll(".mc-tab").forEach(btn => {
      btn.addEventListener("click", () => {
        const s = btn.dataset.status || null;
        activeStatus = (s === activeStatus) ? null : s;
        tabsEl.querySelectorAll(".mc-tab").forEach(b => b.classList.toggle("is-active", (b.dataset.status || null) === activeStatus));
        renderList();
      });
    });
  }

  function renderList() {
    const q = (searchEl.value || "").trim().toLowerCase();
    const filtered = items.filter(c => {
      if (activeStatus !== null && (c.status || "unknown") !== activeStatus) return false;
      if (!q) return true;
      const idStr = String(c.id);
      const nameStr = (c.name || "").toLowerCase();
      return idStr === q || idStr.includes(q) || nameStr.includes(q);
    });

    countEl.textContent = `${filtered.length}/${items.length}`;

    if (!filtered.length) {
      listEl.innerHTML = `<div class="mc-empty">No campaigns match your filters.</div>`;
      return;
    }

    const groups = new Map();
    for (const c of filtered) {
      const key = c.status || "unknown";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(c);
    }
    const orderedKeys = [
      ...CAMPAIGN_STATUS_ORDER.filter(k => groups.has(k)),
      ...[...groups.keys()].filter(k => !CAMPAIGN_STATUS_ORDER.includes(k)),
    ];

    let html = "";
    for (const key of orderedKeys) {
      const camps = groups.get(key);
      camps.sort((a, b) => (b.id || 0) - (a.id || 0));
      const tint = STATUS_TINT[key] || "slate";
      const titleLabel = STATUS_LABEL[key] || key;
      html += `<div class="mc-group" data-status="${esc(key)}">
        <div class="mc-group-header">
          <span class="mc-group-dot ${tint}"></span>
          <span class="mc-group-title">${esc(titleLabel)} Campañas</span>
          <span class="mc-group-count">${camps.length}</span>
        </div>
        <div class="grid">`;
      for (const c of camps) html += _campaignCardHTML(c);
      html += `</div></div>`;
    }
    listEl.innerHTML = html;
  }

  buildTabs();
  searchEl.addEventListener("input", renderList);
  renderList();
  document.getElementById("auto-refresh-status").textContent = "off";
}

// Una sola fuente para la card de campaña. La usan el render inicial y el re-filter del search.
function _campaignCardHTML(c) {
  // Status tint (matches the tabs/group headers in CSS).
  const STATUS_CARD_TINT = {
    discovered: "slate", briefed: "amber", assets_resolved: "sky",
    scored: "emerald",
    failed_brief: "rose", failed_resolve: "rose", blocked_no_assets: "rose",
    unknown: "slate",
  };
  const tint = STATUS_CARD_TINT[c.status] || "slate";

  // Side info from Whop (campaign source metadata).
  const sm = c.source_metadata || {};
  const disc = sm.discovered || {};
  const org = sm.organization_name || disc.organization_name || "";
  const platforms = (sm.platforms || disc.platforms || []).slice(0, 4);
  const refMats = sm.reference_materials || disc.reference_materials || [];
  const payouts = sm.payouts || disc.payouts || [];

  // Best (min) CPM across platforms.
  let bestCpm = null;
  for (const p of payouts) {
    if (p && p.rate_cents != null) {
      const v = p.rate_cents / 100;
      if (bestCpm == null || v < bestCpm) bestCpm = v;
    }
  }

  // Warn pill ("sin transcribir", "no assets", etc.) — keeps the original logic.
  const isBriefedOrLater = ["briefed","assets_resolved","scored"].includes(c.status);
  let warnPill = "";
  if (isBriefedOrLater && c.assets_total === 0) {
    warnPill = `<span class="warn-pill">no assets</span>`;
  } else if (isBriefedOrLater && c.assets_total > 0 && c.assets_transcribed === 0) {
    warnPill = `<span class="warn-pill">sin transcribir</span>`;
  } else if (c.assets_total > 0 && c.assets_transcribed > 0 && c.clips_approved_qa === 0) {
    warnPill = `<span class="warn-pill">sin clips QA</span>`;
  }

  const orgRow = org ? `
    <div class="org-row">
      <span class="org-name" title="${esc(org)}">${esc(org)}</span>
      ${bestCpm != null ? `<span class="cpm-pill" title="Min CPM across platforms">$${bestCpm.toFixed(2)} cpm</span>` : ""}
    </div>` : (bestCpm != null ? `
    <div class="org-row">
      <span class="org-name muted">—</span>
      <span class="cpm-pill" title="Min CPM across platforms">$${bestCpm.toFixed(2)} cpm</span>
    </div>` : "");

  const platformHtml = platforms.length
    ? `<div class="platform-badges">${platforms.map(p => `<span class="platform-badge">${esc(p)}</span>`).join("")}</div>`
    : `<div class="platform-badges"><span class="platform-badge" style="opacity:.5">—</span></div>`;

  const refCount = refMats.length;

  // Score pill — solo si el scorer ha escrito algo en source_metadata.score.
  // El campo viene del endpoint como priority_score / priority_tier (ya
  // resuelto desde source_metadata.score.{total,priority} en el backend).
  // Mostrar: número grande 0-100 con tier "N/10" al lado. Tint por tier
  // (>=7 emerald, >=4 amber, <4 rose).
  let scorePill = "";
  if (c.priority_score != null) {
    const score = Number(c.priority_score);
    const tier = c.priority_tier != null ? Number(c.priority_tier) : null;
    let scoreTint = "rose";
    if (score >= 70) scoreTint = "emerald";
    else if (score >= 40) scoreTint = "amber";
    const tierText = tier != null ? `· ${tier}/10` : "";
    const reason = c.priority_rank_reason ? ` title="${esc(c.priority_rank_reason)}"` : "";
    scorePill = `
      <span class="score-pill ${scoreTint}"${reason}>
        <strong>${score.toFixed(0)}</strong><span class="muted">${esc(tierText)}</span>
      </span>`;
  }

  return `
    <div class="campaign-card" data-cid="${c.id}" onclick="location.hash='#/campaigns/${c.id}'">
      <div>
        <div class="card-head">
          <div class="card-badges">
            <span class="status-pill ${tint}">${esc((c.status || "unknown").replace(/_/g, " "))}</span>
            ${scorePill}
            ${warnPill}
          </div>
          <span class="provider">${esc(c.source_provider || "—")}</span>
        </div>
        <h3>${esc(c.name || "(sin nombre)")}</h3>
        ${orgRow}
        <div class="meta-row">
          ${platformHtml}
          <span class="ref-count">${refCount} ref</span>
        </div>
      </div>
      <div class="card-footer">
        <span>assets: <strong>${c.assets_transcribed || 0}/${c.assets_total || 0}</strong></span>
        <span>clips: <strong>${c.clips_approved_qa || 0} pass</strong> / ${c.clips_total || 0} tot</span>
      </div>
    </div>`;
}

// 2026-09-17: compact icon for a list of reference materials (drive/docs/youtube/web).
function _refMatIcon(refs) {
  if (!refs.length) return "";
  let drive = 0, docs = 0, yt = 0, other = 0;
  for (const r of refs) {
    const u = (r.url || "").toLowerCase();
    if (u.includes("drive.google.com")) drive++;
    else if (u.includes("docs.google.com")) docs++;
    else if (u.includes("youtube.com") || u.includes("youtu.be")) yt++;
    else other++;
  }
  const parts = [];
  if (drive) parts.push(`${drive}D`);
  if (docs) parts.push(`${docs}G`);
  if (yt) parts.push(`${yt}Y`);
  if (other) parts.push(`${other}W`);
  return `(${parts.join("·")})`;
}

// 2026-09-17: pretty DOMAIN for a URL (drive.google.com / docs.google.com / ...)
function _hostFor(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

// 2026-09-18: panel de error para campañas en estado failed_*. Lee
// briefing_error / resolve_error del payload (saneado en backend) y muestra
// tipo, mensaje, timestamp y acción sugerida según `kind`.
const ERROR_HINTS = {
  // brief-reader kinds
  no_materials:        "El brief no incluye reference_materials. Revisa Whop API o pega el brief manualmente.",
  empty_brief:         "Briefing sin campos clave (cpm/prize/workflow). El operador debe rellenarlo o reintentar más tarde.",
  drive_auth_required: "Falta token de gog (Drive auth). Recarga /opt/clipping-system/secrets/gog/gog-keyring.env y reintenta.",
  parse_error:         "El JSON devuelto por el LLM no parsea. Reclasificar a discovered y reintentar; si persiste, log de cron.",
  llm_timeout:         "El LLM tardó > 90s. Reclasificar a discovered y reintentar en el próximo tick.",
  http_403:            "Doc/Drive devolvió 403 al brief-reader. Renovar acceso al doc y reintentar.",
  http_404:            "Doc/Drive devolvió 404 al brief-reader. Verificar que el link siga vivo o re-pegarlo.",
  // drive-resolver / dropbox-resolver kinds
  access_denied:       "El folder (Drive/Dropbox) denegó el acceso. Pedir al operador que cambie a 'Anyone with the link can view'.",
  quota_exceeded:      "Drive/Dropbox rate-limit. Esperar 1h y reintentar (cron lo hace solo).",
  auth_required:       "Falta token del resolver. Revisar /opt/clipping-system/secrets/...",
  folder_not_found:    "Folder no existe o link roto. Verificar URL o re-pegar el link público.",
  empty_folder:        "Folder público pero sin archivos media. Avisa al operador de la campaña.",
  all_files_filtered:  "Todos los assets quedaron fuera del filtro (.mp4/.mov/.mkv/.webm/.zip). Cambia el filtro o acepta otras extensiones.",
  gog_timeout:         "gog CLI colgó. Reclasificar a briefed y reintentar (cron uses timeout corto).",
  playwright_timeout:  "Playwright/Chromium no cargó la página de Dropbox. Verificar binario o reintentar.",
  dropbox_user_disabled_dl: "Dropbox no permite descarga anónima (solo view). Pedir al operador que active 'Anyone with the link can download'.",
  rlkey_expired:       "La firma `rlkey` del shared link caducó. Refrescar el link desde Dropbox.",
  // generic
  other:               "Causa no clasificada. Revisar logs del cron (openclaw cron runs).",
};

function _renderOneError(title, err, accentKey) {
  if (!err) return "";
  const kind = err.kind || "unknown";
  const message = err.message || "(sin mensaje)";
  const at = err.at ? fmtDateTime(err.at) : "—";
  const hint = ERROR_HINTS[kind] || ERROR_HINTS.other;
  return `
    <div class="mc-error-panel ${accentKey}">
      <div class="mc-error-panel-head">
        <span class="mc-error-panel-title">${esc(title)}</span>
        <span class="mc-error-panel-kind">${esc(kind)}</span>
      </div>
      <div class="mc-error-panel-body">
        <div class="mc-error-panel-msg"><strong>${esc(message)}</strong></div>
        <div class="mc-error-panel-meta">Detectado: ${esc(at)}</div>
        <div class="mc-error-panel-hint"><span class="muted">Acción sugerida:</span> ${esc(hint)}</div>
      </div>
    </div>`;
}

function renderErrorPanel(c) {
  const be = c.briefing_error;
  const re = c.resolve_error;
  if (!be && !re) return "";
  return `
    <div class="mc-section-header"><h3>Errores</h3></div>
    ${_renderOneError("Brief-reader falló", be, "rose")}
    ${_renderOneError("Resolver falló", re, "rose")}
  `;
}

// 2026-09-17: render a tile-grid with the 4 key Whop fields (organization /
// platforms / payouts / reference_materials). Designed to be slotted just
// above the tabs in the campaign detail view.
function renderWhopSurface(sm, disc) {
  const f = (sm && typeof sm === "object") ? sm : {};
  const d = (disc && typeof disc === "object") ? disc : {};
  const org = f.organization_name || d.organization_name || "";
  const verified = f.organization_verified ?? d.organization_verified;
  const orgId = f.organization_id || d.organization_id;
  const platforms = (f.platforms && f.platforms.length) ? f.platforms : (d.platforms || []);
  const payouts = (f.payouts && f.payouts.length) ? f.payouts : (d.payouts || []);
  const refMats = (f.reference_materials && f.reference_materials.length)
    ? f.reference_materials : (d.reference_materials || []);
  const whopStatus = f.status || d.status || "";
  const requiresApplication = f.requires_application ?? d.requires_application;

  // Tile 1: Organization
  const tileOrg = `
    <div class="mc-detail-card">
      <div class="mc-detail-card-head">
        <span class="mc-detail-card-title">Organization</span>
        ${verified === true ? '<span class="mc-jobs-tile-badge emerald">✓ verified</span>' : verified === false ? '<span class="mc-jobs-tile-badge slate">· unverified</span>' : ''}
      </div>
      <div class="mc-detail-card-body">
        <strong style="font-size:13px;color:var(--mc-slate-900);">${esc(org || "—")}</strong>
        ${orgId ? `<div class="muted" style="font-size:10.5px;margin-top:4px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">id ${esc(String(orgId).slice(0,12))}</div>` : ""}
      </div>
    </div>`;

  // Tile 2: Platforms
  const tilePlatforms = `
    <div class="mc-detail-card">
      <div class="mc-detail-card-head">
        <span class="mc-detail-card-title">Platforms</span>
        <span class="mc-jobs-tile-badge slate">${platforms.length}</span>
      </div>
      <div class="mc-detail-card-body">
        ${platforms.length ? `<div style="display:flex;flex-wrap:wrap;gap:6px;">${platforms.map(p => `<span class="mc-type-pill">${esc(p)}</span>`).join("")}</div>` : '<span class="muted">—</span>'}
        ${whopStatus ? `<div class="muted" style="font-size:11px;margin-top:8px;">whop status: <strong style="color:var(--mc-slate-700);">${esc(whopStatus)}</strong></div>` : ""}
        ${requiresApplication === true ? '<div class="muted" style="font-size:11px;margin-top:4px;">requires application</div>' : ""}
      </div>
    </div>`;

  // Tile 3: Payouts (tabla tintada)
  let payoutsHtml;
  if (!payouts.length) {
    payoutsHtml = '<span class="muted">—</span>';
  } else {
    payoutsHtml = `
      <div style="overflow-x:auto;border:1px solid var(--mc-slate-200);border-radius:8px;">
        <table class="mc-jobs-table">
          <thead>
            <tr>
              <th>platform</th>
              <th>type</th>
              <th class="num">rate/1k</th>
              <th class="num">min</th>
              <th class="num">max</th>
            </tr>
          </thead>
          <tbody>${payouts.map(p => `
            <tr>
              <td><span class="mc-type-pill">${esc(p.platform || "—")}</span></td>
              <td>${esc(p.payout_type || "—")}</td>
              <td class="num mc-mono">${p.rate_cents != null ? `$${(p.rate_cents/100).toFixed(2)}` : "—"}</td>
              <td class="num mc-mono">${p.min_payout_cents != null ? `$${(p.min_payout_cents/100).toFixed(2)}` : "—"}</td>
              <td class="num mc-mono">${p.max_payout_cents != null ? `$${(p.max_payout_cents/100).toFixed(2)}` : "—"}</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>`;
  }
  const tilePayouts = `
    <div class="mc-detail-card">
      <div class="mc-detail-card-head">
        <span class="mc-detail-card-title">Payouts</span>
        <span class="mc-jobs-tile-badge slate">${payouts.length}</span>
      </div>
      <div class="mc-detail-card-body">${payoutsHtml}</div>
    </div>`;

  // Tile 4: Reference materials (clickable links, icon by host)
  let refHtml;
  if (!refMats.length) {
    refHtml = '<span class="muted">none exposed by Whop API</span>';
  } else {
    refHtml = `<ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;">${refMats.map(r => `
      <li>
        <a class="mc-detail-ref-link" href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.url)}">
          <span class="mc-jobs-tile-badge slate" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">${esc(_hostFor(r.url))}</span>
          <span>${esc(r.type || r.media_type || "link")}</span>
          <span class="muted" style="margin-left:auto;font-size:10.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.url)}</span>
        </a>
      </li>`).join("")}</ul>`;
  }
  const tileRefs = `
    <div class="mc-detail-card" style="grid-column:1 / -1;">
      <div class="mc-detail-card-head">
        <span class="mc-detail-card-title">Reference materials</span>
        <span class="mc-jobs-tile-badge slate">${refMats.length} · not yet downloaded</span>
      </div>
      <div class="mc-detail-card-body">${refHtml}</div>
    </div>`;

  return `<div class="mc-detail-grid">${tileOrg}${tilePlatforms}${tilePayouts}${tileRefs}</div>`;
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
    const STAGE_TINT = { source: "slate", download: "sky", transcribe: "amber", render: "sky", qa: "emerald", published: "emerald" };
    const STAGE_KEY = { source: "source", download: "downloaded", transcribe: "transcribed", render: "render", qa: "qa", published: "published" };
    const stageKey = STAGE_KEY[key] || key;
    const tint = STAGE_TINT[stageKey] || "slate";
    return `<div class="mc-stage mc-stage-${tint}">
      <div class="mc-stage-name">${esc(label)}</div>
      <div class="mc-stage-count">${n}</div>
    </div>`;
  }

  main.innerHTML = `
    <div class="section">
      <div class="mc-detail-header">
        <div class="mc-detail-header-top">
          <div class="mc-detail-eyebrow">
            <a class="mc-link" href="#/campaigns">← Campañas</a>
            <span class="muted">· id ${c.id}</span>
          </div>
          <div class="mc-detail-header-left">
            <h2 class="mc-detail-title">${esc(c.name)}</h2>
            <div class="mc-detail-meta">
              <span class="mc-status-pill ${esc(c.status)}"><span class="dot"></span>${esc(c.status)}</span>
              <span class="mc-type-pill">${esc(c.source_provider)}</span>
              ${c.source_url ? `<a class="mc-link" href="${esc(c.source_url)}" target="_blank" rel="noopener">source ↗</a>` : ""}
              <span class="muted">· updated ${fmtAgo(c.updated_at)}</span>
            </div>
          </div>
        </div>
        <div class="mc-detail-header-actions">
          <button class="mc-btn mc-btn-ghost" id="mc-detail-refresh" type="button">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            Refrescar
          </button>
        </div>
      </div>

      <div class="mc-section-header"><h3>Pipeline</h3></div>
      <div class="mc-pipeline">
        ${stage("Source", pipeline.source, "source")}
        <span class="mc-pipeline-arrow">→</span>
        ${stage("Download", pipeline.download, "downloaded")}
        <span class="mc-pipeline-arrow">→</span>
        ${stage("Transcribe", pipeline.transcribe, "transcribed")}
        <span class="mc-pipeline-arrow">→</span>
        ${stage("Render", pipeline.render, "render")}
        <span class="mc-pipeline-arrow">→</span>
        ${stage("QA", pipeline.qa, "qa")}
        <span class="mc-pipeline-arrow">→</span>
        ${stage("Published", pipeline.published, "published")}
      </div>

      ${renderErrorPanel(c)}

      <div class="mc-section-header"><h3>Whop API surface</h3></div>
      ${renderWhopSurface(c.source_metadata || {}, (c.source_metadata || {}).discovered || {})}

      <div class="mc-section-header"><h3>Detalle</h3></div>
      <div class="mc-tabs mc-tabs-old" role="tablist">
        <button data-tab="assets" class="mc-tab is-active">Assets <span class="mc-tab-count">(${assets.length})</span></button>
        <button data-tab="rules" class="mc-tab">Reglas</button>
        <button data-tab="jobs" class="mc-tab">Active jobs <span class="mc-tab-count">(${jobs.length})</span></button>
        <button data-tab="clips" class="mc-tab">Clips <span class="mc-tab-count">(${clips.length})</span></button>
        <button data-tab="errors" class="mc-tab">Recent errors</button>
      </div>
      <div class="mc-tab-content" id="tab-content"></div>
    </div>
  `;

  document.getElementById("mc-detail-refresh")?.addEventListener("click", () => renderCampaignDetail(main, cid));

  function activateTab(t) {
    document.querySelectorAll(".mc-tabs-old .mc-tab").forEach(b =>
      b.classList.toggle("is-active", b.dataset.tab === t));
    const tc = document.getElementById("tab-content");
    if (t === "assets") tc.innerHTML = renderAssetsTable(assets);
    if (t === "rules")  tc.innerHTML = '<div class="mc-empty" style="padding:20px;">Cargando reglas…</div>';
    if (t === "jobs")   tc.innerHTML = renderJobsTable(jobs.map(j => ({...j, campaign_name: c.name})));
    if (t === "clips")  tc.innerHTML = renderClipsTable(clips.map(cl => ({...cl, campaign_name: c.name})));
  }
  // Carga lazy de la tab Reglas (no bloquear el drill-down)
  let rulesLoaded = false;
  const rulesBtn = document.querySelector('.mc-tabs-old .mc-tab[data-tab="rules"]');
  if (rulesBtn) {
    rulesBtn.addEventListener("click", async () => {
      if (rulesLoaded) return;
      rulesLoaded = true;
      try {
        const rules = await api(`/mission-control/campaigns/${cid}/rules`);
        const tc = document.getElementById("tab-content");
        tc.innerHTML = renderRulesTab(rules);
      } catch (e) {
        rulesLoaded = false; // reintentar la próxima
        const tc = document.getElementById("tab-content");
        tc.innerHTML = `<div class="mc-empty" style="padding:20px;color:var(--mc-rose-700);">No se pudieron cargar las reglas: ${esc(e.message)}</div>`;
      }
    });
  }
  document.querySelectorAll(".mc-tabs-old .mc-tab").forEach(b => {
    b.addEventListener("click", () => activateTab(b.dataset.tab));
  });
  activateTab("assets");
  document.getElementById("auto-refresh-status").textContent = "off";
}

// ---------- Tab Reglas ----------
function _priorityBar(value) {
  if (value == null) return "";
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  return `<span class="priority-bar" style="width:${Math.max(8, pct)}px;" title="${pct}%"></span>`;
}

function renderRulesTab(r) {
  // Bloque 1: Spec canónico (lo que la BD considera "spec" oficial)
  const specBlock = r.spec_is_empty
    ? `<div class="mc-rules-warn">⚠ spec vacío en BD. Las reglas reales viven en <code>source_metadata.rules</code> (más abajo) o en el <code>card_text</code> del briefing.</div>`
    : `<pre class="mc-modal-pre">${esc(JSON.stringify(r.spec, null, 2))}</pre>`;

  // Bloque 2: Reglas estructuradas (source_metadata.rules)
  const rulesObj = r.rules || {};
  const rulesEmpty = !rulesObj || Object.keys(rulesObj).length === 0;
  let rulesContent;
  if (rulesEmpty) {
    rulesContent = `<div class="mc-rules-warn">⚠ <code>source_metadata.rules</code> vacío. El briefing de esta campaña no generó reglas estructuradas.</div>`;
  } else {
    const chips = [];
    if (Array.isArray(rulesObj.platforms)) {
      rulesObj.platforms.forEach(p => chips.push(`<span class="mc-type-pill">${esc(p)}</span>`));
    }
    rulesContent = `
      ${chips.length ? `<div style="margin-bottom:10px;display:flex;flex-wrap:wrap;gap:6px;">${chips.join("")}</div>` : ""}
      <pre class="mc-modal-pre">${esc(JSON.stringify(rulesObj, null, 2))}</pre>`;
  }

  // Bloque 3: card_text (lo que vio el LLM para sacar las reglas)
  const cardBlock = r.card_text
    ? `<pre class="mc-modal-pre">${esc(r.card_text)}</pre>`
    : `<div class="muted">Sin <code>card_text</code> en el briefing original.</div>`;

  // Bloque 4: Priority breakdown
  const pc = r.priority_components || {};
  const pcRows = Object.entries(pc)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${typeof v === "number" ? v.toFixed(3) : esc(String(v))}${_priorityBar(typeof v === "number" ? v : null)}</dd>`)
    .join("");
  const priorityBlock = r.priority_score != null
    ? `
        <dl class="mc-kv">
          <dt>tier</dt><dd>${esc(r.priority_tier || "—")}</dd>
          <dt>score</dt><dd>${esc(String(r.priority_score))}${_priorityBar(r.priority_score)}</dd>
        </dl>
        ${pcRows ? `<dl class="mc-kv" style="margin-top:10px;">${pcRows}</dl>` : ""}`
    : `<div class="muted">Sin priority score (campaña aún no priorizada).</div>`;

  // Bloque 5: Asset links que dijo el briefing (Drive, YouTube, TikTok…)
  // Diferencia CLAVE vs assets reales: aquí están los links crudos, no los assets resueltos.
  const linkItems = (r.asset_links_raw || []).map(url => {
    let chip = `<span class="mc-jobs-tile-badge slate">link</span>`;
    if (/drive\.google\.com/.test(url)) chip = `<span class="mc-jobs-tile-badge sky">Drive</span>`;
    else if (/youtube\.com|youtu\.be/.test(url)) chip = `<span class="mc-type-pill">YouTube</span>`;
    else if (/tiktok\.com/.test(url)) chip = `<span class="mc-type-pill">TikTok</span>`;
    else if (/instagram\.com/.test(url)) chip = `<span class="mc-type-pill">Instagram</span>`;
    return `<a class="mc-detail-ref-link" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(url)}">${chip}<span class="muted" style="margin-left:auto;font-size:10.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(url)}</span></a>`;
  }).join("");

  const linksBlock = (r.asset_links_raw || []).length
    ? linkItems
    : `<div class="muted">El briefing no reportó asset_links para esta campaña.</div>`;

  const driveBlock = (r.drive_ids || []).length
    ? `<dl class="mc-kv" style="margin-top:10px;">
         <dt>Drive IDs</dt><dd>${r.drive_ids.map(id => `<span class="mc-jobs-tile-badge sky">${esc(id)}</span>`).join(" ")}</dd>
       </dl>`
    : "";

  // Bloque 6: metadata extra (briefed_at, cpm, prize…)
  const meta = r.discovered || {};
  const metaBlock = `
    <dl class="mc-kv">
      <dt>status</dt><dd><span class="mc-status-pill ${esc(r.status)}"><span class="dot"></span>${esc(r.status)}</span></dd>
      <dt>provider</dt><dd><span class="mc-type-pill">${esc(r.source_provider || "—")}</span></dd>
      ${meta.detail_url ? `<dt>detail url</dt><dd><a class="mc-link" href="${esc(meta.detail_url)}" target="_blank" rel="noopener">${esc(meta.detail_url)}</a></dd>` : ""}
      ${meta.external_id ? `<dt>external id</dt><dd class="mc-mono">${esc(meta.external_id)}</dd>` : ""}
      ${meta.cpm_usd_per_1k != null ? `<dt>CPM/1k</dt><dd>$${esc(String(meta.cpm_usd_per_1k))}</dd>` : ""}
      ${meta.prize_pool_usd != null ? `<dt>prize pool</dt><dd>$${esc(String(meta.prize_pool_usd))}</dd>` : ""}
      ${meta.joined != null ? `<dt>joined</dt><dd>${esc(String(meta.joined))}</dd>` : ""}
      ${r.briefed_at ? `<dt>briefed at</dt><dd class="mc-mono">${esc(fmtDateTime(r.briefed_at))}</dd>` : ""}
    </dl>`;

  // Aviso si status es "briefed" sin assets — el caso que Molina pidió revisar.
  const briefedNoAssetsWarn = (r.status === "briefed" && (r.asset_links_count || 0) > 0)
    ? `<div class="mc-rules-warn" style="margin-bottom:12px;">
        ⚠ Esta campaña está <b>briefed</b> y el briefing reportó <b>${r.asset_links_count}</b> link(s) de asset(s).
        Si abajo en la tab <b>Assets</b> no ves ninguno resuelto, el resolver aún no ha bajado el contenido
        (o falló). Revisa logs de <code>assets_resolver_tick</code>.
      </div>`
    : "";

  return `
    ${briefedNoAssetsWarn}
    <div class="mc-rules-grid">
      <div class="mc-modal-section">
        <h4 class="mc-modal-section-title">Spec canónico</h4>
        ${specBlock}
      </div>
      <div class="mc-modal-section">
        <h4 class="mc-modal-section-title">Reglas estructuradas <span class="muted" style="font-size:10px;font-weight:500;text-transform:none;letter-spacing:0;">(source_metadata.rules)</span></h4>
        ${rulesContent}
      </div>
      <div class="mc-modal-section" style="grid-column:1 / -1;">
        <h4 class="mc-modal-section-title">Briefing original <span class="muted" style="font-size:10px;font-weight:500;text-transform:none;letter-spacing:0;">(card_text del LLM)</span></h4>
        ${cardBlock}
      </div>
      <div class="mc-modal-section">
        <h4 class="mc-modal-section-title">Prioridad</h4>
        ${priorityBlock}
      </div>
      <div class="mc-modal-section">
        <h4 class="mc-modal-section-title">Metadata</h4>
        ${metaBlock}
      </div>
      <div class="mc-modal-section" style="grid-column:1 / -1;">
        <h4 class="mc-modal-section-title">Asset links del briefing
          <span class="muted" style="font-size:10px;font-weight:500;text-transform:none;letter-spacing:0;">(${r.asset_links_count || 0} links · distintos de los assets ya resueltos)</span>
        </h4>
        ${linksBlock}
        ${driveBlock}
      </div>
    </div>`;
}

function renderAssetsTable(rows) {
  if (!rows.length) return `<div class="mc-empty">No assets.</div>`;
  let html = `
    <div class="mc-jobs-table-section">
      <div class="mc-jobs-table-head">
        <div>
          <span class="mc-jobs-table-title">Assets</span>
          <span class="mc-jobs-table-title-pill">${rows.length} registro${rows.length === 1 ? '' : 's'}</span>
        </div>
      </div>
      <div style="overflow-x:auto;">
        <table class="mc-jobs-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th style="min-width:240px;">Source</th>
              <th>local_path</th>
              <th class="num">Size</th>
              <th class="num">Duration</th>
              <th>Downloaded</th>
            </tr>
          </thead>
          <tbody>`;
  for (const a of rows) {
    const id8 = (a.id || "").slice(0, 8);
    html += `
      <tr>
        <td><span class="mc-id-pill">${esc(id8)}…</span></td>
        <td><span class="mc-status-pill ${esc(a.status)}"><span class="dot"></span>${esc(a.status)}</span></td>
        <td>
          ${a.source_url ? `<a class="mc-link" href="${esc(a.source_url)}" target="_blank" rel="noopener" title="${esc(a.source_url)}" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;display:inline-block;max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom;">${esc(a.source_url)}</a>` : `<span class="muted">—</span>`}
        </td>
        <td>${renderPathCell(a.local_path, window.WORKER_FILE_BASE_URL)}</td>
        <td class="num mc-mono">${esc(fmtBytes(a.file_size))}</td>
        <td class="num mc-mono">${esc(fmtDuration(a.duration_seconds))}</td>
        <td class="mc-mono" style="color:var(--mc-slate-600);font-size:11.5px;">${esc(fmtAgo(a.downloaded_at))}</td>
      </tr>`;
  }
  html += `</tbody></table></div></div>`;
  return html;
}

function renderClipsTable(rows) {
  if (!rows.length) return `<div class="mc-empty">No clips.</div>`;
  let html = `
    <div class="mc-jobs-table-section">
      <div class="mc-jobs-table-head">
        <div>
          <span class="mc-jobs-table-title">Clips</span>
          <span class="mc-jobs-table-title-pill">${rows.length} registro${rows.length === 1 ? '' : 's'}</span>
        </div>
      </div>
      <div style="overflow-x:auto;">
        <table class="mc-jobs-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Plataforma</th>
              <th>Status / QA</th>
              <th>Hook</th>
              <th class="num">Score</th>
              <th class="num">Duration</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>`;
  for (const cl of rows) {
    const id8 = (cl.id || "").slice(0, 8);
    const qa = cl.qa_status || "pending";
    const stat = cl.status || "draft";
    html += `
      <tr>
        <td><span class="mc-id-pill">${esc(id8)}…</span></td>
        <td><span class="mc-type-pill">${esc(cl.platform || "—")}</span></td>
        <td>
          <div class="mc-status-cell">
            <span class="mc-status-pill ${esc(stat)}"><span class="dot"></span>${esc(stat)}</span>
            <div class="mc-status-meta">QA: ${esc(qa)}</div>
          </div>
        </td>
        <td><span class="mc-campaign-name" style="font-size:12px;font-weight:500;color:var(--mc-slate-700);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;">${esc(cl.hook || cl.title || "—")}</span></td>
        <td class="num mc-mono" style="font-weight:700;color:${cl.score >= 80 ? 'var(--mc-emerald-700)' : cl.score >= 50 ? 'var(--mc-amber-700)' : 'var(--mc-slate-700)'};">${cl.score != null ? cl.score : "—"}</div></td>
        <td class="num mc-mono">${esc(fmtDuration(cl.duration_seconds))}</td>
        <td class="mc-mono" style="color:var(--mc-slate-600);font-size:11.5px;">${esc(fmtAgo(cl.created_at))}</td>
      </tr>`;
  }
  html += `</tbody></table></div></div>`;
  return html;
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
  const allData = await api(`/mission-control/jobs/recent${buildQuery({ limit: 500 })}`);

  const items = data.items || [];
  const allItems = allData.items || [];

  // KPI aggregates from the full set (last 500, not just filtered)
  const total = allItems.length;
  const failedItems = allItems.filter(j => j.status === "failed");
  const failedCount = failedItems.length;
  const pendingCount  = allItems.filter(j => j.status === "pending").length;
  const processingCount = allItems.filter(j => j.status === "processing").length;
  const completedCount = allItems.filter(j => j.status === "completed").length;
  const maxAttCount = allItems.filter(j => j.attempts >= j.max_attempts && j.status === "failed").length;

  // Tipo activo (el más frecuente entre los recientes)
  const typeCounts = {};
  for (const j of allItems) typeCounts[j.job_type] = (typeCounts[j.job_type] || 0) + 1;
  const activeType = Object.entries(typeCounts).sort((a,b)=>b[1]-a[1])[0]?.[0] || "—";

  // Workers únicos
  const workerIds = new Set(allItems.map(j => j.worker_id).filter(Boolean));
  const workerCount = workerIds.size;

  // Error recurrente (última palabra de error_message agrupada)
  const errCounts = {};
  for (const j of failedItems) {
    const last = (j.error_message || "").split("\n").pop() || "";
    const key = last.length > 80 ? last.slice(0, 80) + "…" : last;
    if (key) errCounts[key] = (errCounts[key] || 0) + 1;
  }
  const topErr = Object.entries(errCounts).sort((a,b)=>b[1]-a[1])[0]?.[0] || "—";

  // Elapsed time range (en segundos)
  const elapsedSecs = allItems.map(j => j.elapsed_seconds || 0).filter(x => x > 0);
  const minMin = elapsedSecs.length ? Math.min(...elapsedSecs) / 60 : 0;
  const maxMin = elapsedSecs.length ? Math.max(...elapsedSecs) / 60 : 0;
  const elapsedRange = elapsedSecs.length ? `${Math.round(minMin)}m - ${Math.round(maxMin)}m` : "—";

  const types = ["download","transcribe","render","qa","health"];
  const stats = ["pending","assigned","processing","completed","failed","cancelled"];

  main.innerHTML = `
    <div class="section">
      <div class="mc-section-header">
        <div>
          <div class="mc-detail-eyebrow">Plataforma</div>
          <h2 class="mc-detail-title">Jobs <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${total}</span></h2>
        </div>
      </div>

      <div class="mc-jobs-tiles">
        <div class="mc-jobs-tile">
          <div class="mc-jobs-tile-head">
            <span class="mc-jobs-tile-label">Total Jobs Registrados</span>
            <span class="mc-jobs-tile-badge sky">${total}</span>
          </div>
          <div class="mc-jobs-tile-body">${total}<span class="muted">tareas batch</span></div>
          <div class="mc-jobs-tile-foot">
            <span><span style="display:inline-block;width:6px;height:6px;border-radius:999px;background:var(--mc-sky-500);margin-right:6px;"></span>Tipo activo: <span class="mc-mono" style="margin-left:4px;font-weight:600;color:var(--mc-slate-700);">${esc(activeType)}</span></span>
          </div>
        </div>

        <div class="mc-jobs-tile is-critical">
          <div class="mc-jobs-tile-head">
            <span class="mc-jobs-tile-label">Jobs Fallidos</span>
            <span class="mc-jobs-tile-badge rose">${failedCount}</span>
          </div>
          <div class="mc-jobs-tile-body">${failedCount}<span class="muted" style="color:var(--mc-rose-500);font-weight:600;">${total ? Math.round(failedCount * 100 / total) : 0}% de la cola actual</span></div>
          <div class="mc-jobs-tile-foot" style="border-top-color:var(--mc-rose-100);">
            <span style="color:var(--mc-rose-600);font-weight:600;">Max attempts agotados (3/3): ${maxAttCount}</span>
          </div>
        </div>

        <div class="mc-jobs-tile">
          <div class="mc-jobs-tile-head">
            <span class="mc-jobs-tile-label">Workers Únicos</span>
            <span class="mc-jobs-tile-badge ${workerCount > 0 ? 'emerald' : 'slate'}">${workerCount > 0 ? 'Online' : 'Offline'}</span>
          </div>
          <div class="mc-jobs-tile-body">${workerCount}<span class="muted mc-mono">${esc([...workerIds][0] || "—")}</span></div>
          <div class="mc-jobs-tile-foot">
            <span>Capacidad: ${workerCount} worker${workerCount === 1 ? '' : 's'}</span>
          </div>
        </div>

        <div class="mc-jobs-tile">
          <div class="mc-jobs-tile-head">
            <span class="mc-jobs-tile-label">Tiempo Transcurrido Promedio</span>
            <span class="mc-jobs-tile-badge slate">${failedCount > 0 ? 'errores' : 'ok'}</span>
          </div>
          <div class="mc-jobs-tile-body">${esc(elapsedRange)}</div>
          <div class="mc-jobs-tile-foot">
            <span style="color:var(--mc-slate-400);">Error recurrente:</span>
            <span class="mc-mono" style="color:var(--mc-slate-600);font-size:10px;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(topErr)}">${esc(topErr)}</span>
          </div>
        </div>
      </div>

      <div class="mc-jobs-filterbar">
        <div class="mc-jobs-filterbar-left">
          <span style="font-size:11.5px;font-weight:700;color:var(--mc-slate-700);">Filtros:</span>
          <div class="mc-toolbar-field">
            <select id="jobs-type" class="mc-select">
              <option value="">Tipo: Todos (${total})</option>
              ${types.map(t => `<option value="${t}" ${t===typeFilter?'selected':''}>${t} (${typeCounts[t] || 0})</option>`).join('')}
            </select>
          </div>
          <div class="mc-toolbar-field">
            <select id="jobs-status" class="mc-select">
              <option value="">Estado: Todos</option>
              ${stats.map(s => `<option value="${s}" ${s===statFilter?'selected':''}>${s} (${allItems.filter(j=>j.status===s).length})</option>`).join('')}
            </select>
          </div>
          <div class="mc-jobs-filterbar-divider"></div>
          <span class="mc-jobs-filterbar-pill ${maxAttCount > 0 ? '' : 'slate'}">
            ${maxAttCount > 0 ? '<span class="dot"></span>' : ''}Attempt 3/3 (${maxAttCount})
          </span>
          <span class="mc-jobs-filterbar-pill slate">Prioridad 5 (${allItems.filter(j=>j.priority===5).length})</span>
        </div>
        <div class="mc-jobs-filterbar-right">
          <button id="jobs-refresh" class="mc-btn mc-btn-ghost">Refresh now</button>
        </div>
      </div>

      ${renderJobsTableStitch(items)}
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

function renderJobsTableStitch(rows) {
  if (!rows.length) return `<div class="mc-jobs-table-section"><div class="mc-empty">No jobs match the filter.</div></div>`;
  const failedHere = rows.filter(r => r.status === "failed").length;
  const headError = failedHere > 0 ? `<span class="dot"></span>Error crítico en jobs fallidos` : "";

  let html = `
    <div class="mc-jobs-table-section">
      <div class="mc-jobs-table-head">
        <div>
          <span class="mc-jobs-table-title">Cola de Ejecución y Monitoreo</span>
          <span class="mc-jobs-table-title-pill">${rows.length} registros</span>
        </div>
        ${headError ? `<div class="mc-jobs-table-head-right">${headError}</div>` : ""}
      </div>
      <div style="overflow-x:auto;">
        <table class="mc-jobs-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Tipo</th>
              <th>Estado / Intento</th>
              <th>Campaña de Origen</th>
              <th>Worker</th>
              <th>Transcurrido</th>
              <th>Traceback / Detalle de Error</th>
              <th style="text-align:right;">Acciones</th>
            </tr>
          </thead>
          <tbody>`;

  for (const j of rows) {
    const id8 = (j.id || "").slice(0, 8);
    const trace = (j.error_message || "").split("\n");
    const traceHead = trace[0] || "";
    const traceBody = trace.slice(1).join("\n").slice(0, 240);

    html += `
      <tr data-job="${esc(j.id)}">
        <td>
          <div style="display:inline-flex;align-items:center;gap:6px;">
            <span class="mc-id-pill">${esc(id8)}…</span>
            <button class="mc-id-copy" title="Copiar ID" type="button" data-copy="${esc(j.id)}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"></path></svg>
            </button>
          </div>
        </td>
        <td>
          <span class="mc-type-pill">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            ${esc(j.job_type)}
          </span>
        </td>
        <td>
          <div class="mc-status-cell">
            <span class="mc-status-pill ${esc(j.status)}"><span class="dot"></span>${esc(j.status)}</span>
            <div class="mc-status-meta">Prioridad: ${j.priority} · <span class="${j.attempts >= j.max_attempts && j.status === 'failed' ? 'rose' : ''}">Att: ${j.attempts}/${j.max_attempts}</span></div>
          </div>
        </td>
        <td>
          <div class="mc-campaign-cell">
            <div class="mc-campaign-name">${esc(j.campaign_name || "—")}</div>
          </div>
        </td>
        <td>
          <span class="mc-worker-pill"><span class="dot"></span>${esc((j.worker_id || "—").replace(/^windows-gpu-worker-0?/, "windows-gpu-"))}</span>
        </td>
        <td>
          <div class="mc-elapsed">
            <span class="mc-elapsed-time">${esc(fmtDuration(j.elapsed_seconds))}</span>
            <span class="mc-elapsed-when">${esc(fmtAgo(j.updated_at))}</span>
          </div>
        </td>
        <td>
          ${j.error_message ? `
            <div class="mc-traceback">
              <div class="mc-traceback-head">${esc(traceHead)}</div>
              <div class="mc-traceback-body">${esc(traceBody)}</div>
            </div>` : `<span class="muted" style="font-size:11px;">—</span>`}
        </td>
        <td style="text-align:right;">
          <button class="mc-row-action" title="Ver detalle" type="button">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" stroke-linecap="round" stroke-linejoin="round"></path><circle cx="12" cy="12" r="3"></circle></svg>
          </button>
        </td>
      </tr>`;
  }
  html += `</tbody></table></div></div>`;
  return html;
}

function renderJobsTable(rows) {
  if (!rows.length) return "<div class='mc-empty'>No jobs match the filter.</div>";
  let html = `<div class="mc-table-wrap"><table class="mc-table"><thead><tr>
    <th>ID</th><th>Type</th><th>Status</th><th class="num">Priority</th>
    <th class="num">Attempt</th><th>Campaign</th><th>Worker</th>
    <th class="num">Elapsed</th><th>When</th><th>Error</th>
  </tr></thead><tbody>`;
  for (const j of rows) {
    html += `<tr data-job="${esc(j.id)}" class="mc-row-clickable">
      <td><span class="mc-mono">${esc(j.id.slice(0,8))}…</span></td>
      <td><span class="mc-mono">${esc(j.job_type)}</span></td>
      <td>${pill(j.status)}</td>
      <td class="num">${j.priority}</td>
      <td class="num">${j.attempts}/${j.max_attempts}</td>
      <td>${esc(j.campaign_name || "—")}</td>
      <td><span class="mc-mono">${esc(j.worker_id || "—")}</span></td>
      <td class="num">${fmtDuration(j.elapsed_seconds)}</td>
      <td><span class="mc-mono" title="${esc(j.created_at)}">${fmtAgo(j.updated_at)}</span></td>
      <td><span class="mc-truncate">${esc(j.error_message || "")}</span></td>
    </tr>`;
  }
  return html + "</tbody></table></div>";
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
  const id8 = (j.id || "").slice(0, 8);
  const isFailed = j.status === "failed";
  const trace = (j.error_message || "").split("\n");
  const traceHead = trace[0] || "";
  const traceBody = trace.slice(1).join("\n");

  const timeline = `
    <div class="mc-modal-timeline">
      <div class="mc-modal-step"><span class="mc-modal-step-label">created</span> <strong>${esc(fmtDateTime(j.created_at))}</strong></div>
      <span class="mc-modal-arrow">→</span>
      <div class="mc-modal-step"><span class="mc-modal-step-label">started</span> <strong>${esc(fmtDateTime(j.started_at) || "—")}</strong></div>
      <span class="mc-modal-arrow">→</span>
      <div class="mc-modal-step"><span class="mc-modal-step-label">completed</span> <strong>${esc(fmtDateTime(j.completed_at) || "—")}</strong></div>
    </div>
  `;

  return `
    <div class="mc-modal-header">
      <div class="mc-modal-header-left">
        <div class="mc-modal-eyebrow">Job</div>
        <h3 class="mc-modal-title">
          <span class="mc-id-pill">${esc(id8)}…</span>
          <span class="mc-modal-full-id">${esc(j.id || "")}</span>
        </h3>
        <div class="mc-modal-sub">
          <span class="mc-type-pill">${esc(j.job_type)}</span>
          <span class="mc-status-pill ${esc(j.status)}"><span class="dot"></span>${esc(j.status)}</span>
          <span class="muted">· ${esc(j.campaign_name || "sin campaña")}</span>
        </div>
      </div>
    </div>

    <div class="mc-modal-section">
      <h4 class="mc-modal-section-title">Resumen</h4>
      <dl class="mc-kv">
        <dt>Tipo</dt><dd>${esc(j.job_type)}</dd>
        <dt>Estado</dt><dd><span class="mc-status-pill ${esc(j.status)}"><span class="dot"></span>${esc(j.status)}</span></dd>
        <dt>Prioridad</dt><dd class="mc-mono">${j.priority}</dd>
        <dt>Intentos</dt><dd class="mc-mono">${j.attempts}/${j.max_attempts}</dd>
        <dt>Worker</dt><dd><span class="mc-worker-pill"><span class="dot"></span>${esc(j.worker_id || "—")}</span></dd>
        <dt>Campaña</dt><dd>${esc(j.campaign_name || "—")}</dd>
        <dt>Elapsed</dt><dd class="mc-mono">${esc(fmtDuration(j.elapsed_seconds))}</dd>
      </dl>
    </div>

    <div class="mc-modal-section">
      <h4 class="mc-modal-section-title">Timeline</h4>
      ${timeline}
    </div>

    ${j.payload && Object.keys(j.payload).length ? `
    <div class="mc-modal-section">
      <h4 class="mc-modal-section-title">Payload</h4>
      <pre class="mc-modal-pre">${esc(JSON.stringify(j.payload, null, 2))}</pre>
    </div>` : ""}

    ${j.result && Object.keys(j.result).length ? `
    <div class="mc-modal-section">
      <h4 class="mc-modal-section-title">Result</h4>
      <pre class="mc-modal-pre">${esc(JSON.stringify(j.result, null, 2))}</pre>
    </div>` : ""}

    ${j.error_message ? `
    <div class="mc-modal-section">
      <h4 class="mc-modal-section-title" style="color:var(--mc-rose-700);">Error · traceback</h4>
      <div class="mc-traceback" style="margin-top:8px;">
        <div class="mc-traceback-head">${esc(traceHead)}</div>
        <div class="mc-traceback-body" style="-webkit-line-clamp:unset;">${esc(traceBody)}</div>
      </div>
    </div>` : ""}
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
      <div class="mc-section-header">
        <div>
          <div class="mc-detail-eyebrow">Plataforma</div>
          <h2 class="mc-detail-title">Downloaded videos <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${items.length}</span></h2>
        </div>
      </div>
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
      <div class="mc-section-header">
        <div>
          <div class="mc-detail-eyebrow">Plataforma</div>
          <h2 class="mc-detail-title">Clips <span class="mc-jobs-tile-badge slate" style="margin-left:8px;vertical-align:middle;">${items.length}</span></h2>
        </div>
      </div>
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
