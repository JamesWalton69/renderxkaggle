/* Overview view: stats, engine state, recent jobs, activity feed, 14-day chart. */
"use strict";

window.Views = window.Views || {};

window.Views.overview = {
  title: "Overview",
  _timer: null,

  render(root) {
    UI.loading(root);
    this._load(root);
    this._timer = setInterval(() => this._load(root, true), 25000);
  },

  destroy() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  },

  async _load(root, silent) {
    try {
      const ov = await API.get("/api/overview");
      this._paint(root, ov);
    } catch (e) {
      if (!silent) root.innerHTML = `<div class="empty"><div class="big">⚠️</div>${UI.esc(e.message)}</div>`;
    }
  },

  _paint(root, ov) {
    const c = ov.counts || {};
    const active = ov.active_job;
    const series = ov.series || [];
    const maxN = Math.max(1, ...series.map((s) => s.n));
    const bars = series.map((s) => {
      const h = Math.max(4, Math.round((s.n / maxN) * 100));
      return `<div class="bar" style="height:${h}%"><span class="bar-tip">${s.day}: ${s.n}</span></div>`;
    }).join("");

    const recentRows = (ov.recent_jobs || []).map((j) => `
      <tr>
        <td><a href="#/jobs" onclick="event.preventDefault();location.hash='#/jobs?focus=${j.id}'">#${j.id}</a></td>
        <td>${UI.esc(j.notebook_title || "deleted notebook")}</td>
        <td>${UI.esc(j.kind)}</td>
        <td>${UI.badge(j.status)}</td>
        <td>${UI.fmtTime(j.created_at)}</td>
        <td>${UI.duration(j.started_at, j.finished_at)}</td>
      </tr>`).join("");

    const feed = window.Views.overview._feed || [];

    root.innerHTML = `
      <div class="stat-grid">
        <div class="card stat-card"><div class="stat-value">${ov.total_jobs}</div><div class="stat-label">total jobs</div></div>
        <div class="card stat-card"><div class="stat-value stat-accent">${ov.success_rate == null ? "—" : ov.success_rate + "%"}</div><div class="stat-label">success rate</div></div>
        <div class="card stat-card"><div class="stat-value">${ov.notebooks}</div><div class="stat-label">notebooks</div></div>
      </div>

      <div style="display:grid;grid-template-columns:1.4fr 1fr;gap:16px;margin-top:16px" class="ov-grid">
        <div class="card">
          <div class="card-title">Engine</div>
          <div class="kv"><span class="k">state</span><span class="v">${UI.badge(active ? ov.engine_state : "idle", ov.engine_state)}</span></div>
          <div class="kv"><span class="k">active job</span><span class="v">${active ? `<a href="#/jobs?focus=${active.id}">#${active.id} · ${UI.esc(active.notebook_title || "")}</a>` : "none"}</span></div>
          <div class="kv"><span class="k">last activity</span><span class="v">${feed && feed.length ? UI.fmtTime(feed[0].ts) : "—"}</span></div>
          <div class="kv"><span class="k">version</span><span class="v">${UI.esc(ov.version)}</span></div>
          <div style="margin-top:12px"><a href="#/jobs" class="btn btn-ghost btn-sm">Open jobs</a></div>
        </div>
        <div class="card">
          <div class="card-title">Runs — last 14 days</div>
          ${series.length ? `<div class="bars">${bars}</div><div class="bar-axis"><span>${series[0] ? series[0].day : ""}</span><span>today</span></div>` : `<div class="empty" style="padding:24px">No runs yet</div>`}
        </div>
      </div>

      <div class="card" style="margin-top:16px">
        <div class="card-title">Recent jobs</div>
        <div class="table-wrap">
          ${recentRows ? `<table><thead><tr><th>Job</th><th>Notebook</th><th>Kind</th><th>Status</th><th>Created</th><th>Duration</th></tr></thead><tbody>${recentRows}</tbody></table>` : `<div class="empty">No jobs yet — create a notebook and press Run.</div>`}
        </div>
      </div>

      <div class="card" style="margin-top:16px">
        <div class="card-title">Live activity</div>
        <div id="ov-feed" class="mono" style="max-height:260px;overflow-y:auto;color:var(--muted)"></div>
      </div>`;

    const feedEl = root.querySelector("#ov-feed");
    if (feedEl) feedEl.innerHTML = feed.length ? feed.map((l) =>
      `<div style="padding:3px 0"><span style="color:var(--faint)">[${UI.esc(l.ts.slice(11, 19))}]</span> job ${l.job_id}: ${UI.esc(l.line)}</div>`
    ).join("") : `<div class="empty" style="padding:16px">No activity yet</div>`;

    const grid = root.querySelector(".ov-grid");
    if (grid) {
      grid.style.display = "grid";
      grid.style.gridTemplateColumns = "1.4fr 1fr";
      grid.style.gap = "16px";
      const media = window.matchMedia("(max-width: 900px)");
      grid.style.gridTemplateColumns = media.matches ? "1fr" : "1.4fr 1fr";
    }
  },
};

/* cached feed (filled by app.js global upload) */
window.Views.overview._feed = [];
