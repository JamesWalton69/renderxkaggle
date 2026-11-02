/* Jobs view: table with filters, detail/logs modal, output files, cancel/requeue. */
"use strict";

window.Views = window.Views || {};

window.Views.jobs = {
  title: "Jobs",
  _timer: null,
  _filter: "",
  _focusJob: null,

  render(root, params = {}) {
    this._focusJob = params.focus ? Number(params.focus) : null;
    UI.loading(root);
    root.innerHTML = `
      <div class="card">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" id="job-filters">
          ${["", "queued", "running", "stopping", "success", "failed", "cancelled", "interrupted", "uncertain"].map((s) =>
            `<button class="btn btn-ghost btn-sm${this._filter === s ? " btn-primary" : ""}" data-f="${s}" type="button">${s || "all"}</button>`
          ).join("")}
        </div>
        <div class="table-wrap"><table><thead><tr>
          <th>#</th><th>Notebook</th><th>Kind</th><th>Status</th><th>Kaggle</th>
          <th>Created</th><th>Duration</th><th style="width:190px">Actions</th>
        </tr></thead><tbody id="job-rows"><tr><td colspan="8"><div class="empty"><span class="spinner"></span></div></td></tr></tbody></table></div>
      </div>`;
    root.querySelector("#job-filters").addEventListener("click", (e) => {
      const f = e.target.dataset.f;
      if (f === undefined) return;
      this._filter = f;
      this.render(root);
    });
    this._load(root);
    this._timer = setInterval(() => this._load(root, true), 6000);
  },

  destroy() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  },

  async _load(root, silent) {
    try {
      const q = this._filter ? `?status=${encodeURIComponent(this._filter)}` : "";
      const jobs = await API.get(`/api/jobs${q}`);
      this._paint(root, jobs);
      if (this._focusJob) {
        const job = jobs.find((j) => j.id === this._focusJob);
        if (job) { this._openDetail(job); this._focusJob = null; }
      }
    } catch (e) {
      if (!silent) UI.toast(e.message, "error");
    }
  },

  _paint(root, jobs) {
    const tbody = root.querySelector("#job-rows");
    if (!tbody) return;
    if (!jobs.length) {
      tbody.innerHTML = `<tr><td colspan="8"><div class="empty">No ${this._filter || ""} jobs</div></td></tr>`;
      return;
    }
    tbody.innerHTML = jobs.map((j) => `
      <tr>
        <td><b>#${j.id}</b></td>
        <td>${UI.esc(j.notebook_title || "deleted notebook")}</td>
        <td>${UI.esc(j.kind)}</td>
        <td>${UI.badge(j.status)}</td>
        <td>${j.kaggle_status ? UI.esc(j.kaggle_status) : "—"}</td>
        <td>${UI.fmtTime(j.created_at)}</td>
        <td>${UI.duration(j.started_at, j.finished_at)}</td>
        <td>
          <button class="btn btn-ghost btn-sm" data-act="detail" data-id="${j.id}" type="button">Open</button>
          ${["queued", "uncertain"].includes(j.status) ? `<button class="btn btn-danger btn-sm" data-act="cancel" data-id="${j.id}" type="button">Cancel</button>` : ""}
          ${["running", "stopping"].includes(j.status) ? `<button class="btn btn-danger btn-sm" data-act="stop" data-id="${j.id}" type="button">Stop</button>` : ""}
          ${!["queued", "running", "uncertain", "stopping"].includes(j.status) ? `<button class="btn btn-ghost btn-sm" data-act="requeue" data-id="${j.id}" type="button">Requeue</button>` : ""}
        </td>
      </tr>`).join("");

    tbody.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = () => {
        const id = Number(btn.dataset.id);
        const act = btn.dataset.act;
        if (act === "detail") this._openDetail(jobs.find((j) => j.id === id));
        else if (act === "cancel") this._cancel(id, false);
        else if (act === "stop") this._cancel(id, true);
        else if (act === "requeue") this._requeue(id);
      };
    });
  },

  _cancel(id, stopping) {
    UI.confirm(stopping
      ? `Stop job #${id}? Outputs are snapshotted first, then an immediate-exit stub is pushed to Kaggle.`
      : `Cancel job #${id}? (Only queued/uncertain jobs can be cancelled.)`, async () => {
      try {
        await API.post(`/api/jobs/${id}/cancel`, {});
        UI.toast(`Job #${id} cancelled`);
        this._load(document.getElementById("view"));
      } catch (e) { UI.toast(e.message, "error"); }
    });
  },

  _requeue(id) {
    UI.confirm(`Requeue job #${id}? It will run again when the queue is free.`, async () => {
      try {
        await API.post(`/api/jobs/${id}/requeue`, {});
        UI.toast(`Job #${id} requeued`);
        this._load(document.getElementById("view"));
      } catch (e) { UI.toast(e.message, "error"); }
    });
  },

  async _openDetail(job) {
    let m;
    let timer = null;
    const render = async () => {
      try {
        const full = await API.get(`/api/jobs/${job.id}`);
        const running = ["queued", "running", "uncertain", "stopping"].includes(full.status);
        const logs = (full.logs || []).map((l) => `<div><span style="color:var(--faint)">[${UI.esc(l.ts.slice(11, 19))}]</span> ${UI.esc(l.line)}</div>`).join("") || `<div class="empty" style="padding:16px">No log lines yet.</div>`;
        const meta = full.meta || {};
        let outputHtml = "";
        if (meta.files && meta.files.length) {
          outputHtml = `<div style="margin-top:8px"><b>Output files:</b><br>` +
            meta.files.map((f) => `<a href="/api/jobs/${job.id}/output/${encodeURIComponent(f)}" download style="display:inline-block;margin:4px 8px 0 0">⬇ ${UI.esc(f)}</a>`).join("") + `</div>`;
        }
        m.root.querySelector("#job-logs").innerHTML = logs;
        m.root.querySelector("#job-lead").innerHTML = `
          <span>${UI.badge(full.status)}</span>
          <span>${full.kaggle_status ? `<span class="badge badge-neutral">kaggle: ${UI.esc(full.kaggle_status)}</span>` : ""}</span>
          <span style="color:var(--muted)">created ${UI.fmtTime(full.created_at)} · duration ${UI.duration(full.started_at, full.finished_at)}</span>`;
        m.root.querySelector("#job-extra").innerHTML = (full.error ? `<p style="color:#fb7185">⚠ ${UI.esc(full.error)}</p>` : "") + outputHtml;
        if (running) {
          setTimeout(render, 4000);
        } else {
          if (timer) clearTimeout(timer);
        }
      } catch (e) {
        if (timer) clearTimeout(timer);
      }
    };

    m = UI.modal({
      title: `Job #${job.id} — ${job.notebook_title || "deleted notebook"}`,
      wide: true,
      body: `
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px" id="job-lead"></div>
        <div id="job-extra" style="margin-bottom:12px"></div>
        <div class="card-title" style="margin-bottom:6px">Logs</div>
        <div class="mono" id="job-logs" style="max-height:340px;overflow-y:auto;background:rgba(7,11,20,.6);border:1px solid var(--border);border-radius:10px;padding:12px;color:var(--muted)"></div>`,
    });
    render();
  },
};
