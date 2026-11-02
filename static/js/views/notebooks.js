/* Notebooks view: grid, create/edit modal (code editor), run, schedule, delete. */
"use strict";

window.Views = window.Views || {};

window.Views.notebooks = {
  title: "Notebooks",
  _timer: null,

  render(root) {
    UI.loading(root);
    root.innerHTML = `
      <div style="display:flex;justify-content:flex-end;margin-bottom:16px">
        <button class="btn btn-primary" id="nb-new" type="button">＋ New notebook</button>
      </div>
      <div class="nb-grid" id="nb-grid"></div>`;
    root.querySelector("#nb-new").onclick = () => this._openEditor(null);
    this._load(root);
    this._timer = setInterval(() => this._load(root, true), 20000);
  },

  destroy() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  },

  async _load(root, silent) {
    try {
      const list = await API.get("/api/notebooks");
      this._paint(root, list);
    } catch (e) {
      if (!silent) UI.toast(e.message, "error");
    }
  },

  _paint(root, list) {
    const grid = root.querySelector("#nb-grid");
    if (!grid) return;
    if (!list.length) {
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="big">📓</div>No notebooks yet.<br><button class="btn btn-primary" style="margin-top:12px" id="nb-new-empty" type="button">Create the first one</button></div>`;
      const btn = grid.querySelector("#nb-new-empty");
      if (btn) btn.onclick = () => this._openEditor(null);
      return;
    }
    grid.innerHTML = list.map((nb) => {
      const code = notebookCellToCode(nb.source);
      const schedule = nb.schedule ? `· ${UI.esc(nb.schedule)}` : "";
      return `
        <div class="card nb-card">
          <div>
            <div class="nb-title">${UI.esc(nb.title)}</div>
            <div class="nb-slug">${UI.esc(nb.slug)}${schedule}</div>
          </div>
          <div class="nb-code">${UI.esc(code.slice(0, 220))}${code.length > 220 ? "…" : ""}</div>
          <div class="nb-foot">
            <span>${nb.last_job_status ? UI.badge(nb.last_job_status) : `<span class="badge badge-neutral">never run</span>`}</span>
            <span style="flex:1"></span>
            <button class="btn btn-ghost btn-sm" data-act="edit" data-id="${nb.id}" type="button">Edit</button>
            <button class="btn btn-primary btn-sm" data-act="run" data-id="${nb.id}" type="button">Run</button>
            <button class="btn btn-ghost btn-sm" data-act="schedule" data-id="${nb.id}" type="button">⏱</button>
            <button class="btn btn-danger btn-sm" data-act="delete" data-id="${nb.id}" type="button">✕</button>
          </div>
        </div>`;
    }).join("");

    grid.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = () => {
        const id = Number(btn.dataset.id);
        const act = btn.dataset.act;
        const nb = list.find((n) => n.id === id);
        if (act === "edit") this._openEditor(nb);
        else if (act === "run") this._run(nb);
        else if (act === "schedule") this._schedule(nb);
        else if (act === "delete") this._delete(nb);
      };
    });
  },

  async _run(nb) {
    try {
      const res = await API.post(`/api/notebooks/${nb.id}/run`, {});
      UI.toast(`Job #${res.job_id} queued — it will run as soon as the queue is free`);
      window.dispatchEvent(new CustomEvent("kc-jobs-changed"));
    } catch (e) {
      UI.toast(e.message, "error");
    }
  },

  _schedule(nb) {
    UI.modal({
      title: `Schedule — ${nb.title}`,
      body: `
        <div class="field">
          <label>Cron (UTC) — 5 fields: minute hour day month weekday</label>
          <input id="sch-cron" value="${UI.esc(nb.schedule || "")}" placeholder="e.g. 0 9 * * 1  (Mondays 09:00)">
        </div>
        <p style="color:var(--muted);font-size:12.5px">Leave empty to disable scheduling.</p>`,
      footer: `<button class="btn btn-ghost" data-act="no" type="button">Cancel</button><button class="btn btn-primary" data-act="yes" type="button">Save</button>`,
    }).root.addEventListener("click", async (e) => {
      const act = e.target.dataset.act;
      if (!act || act === "no") return;
      if (act === "yes") {
        const cron = document.getElementById("sch-cron").value.trim();
        try {
          await API.put(`/api/notebooks/${nb.id}`, { schedule: cron });
          UI.toast(cron ? `Schedule set: ${cron} (UTC)` : "Schedule disabled");
          this._load(document.getElementById("view"));
        } catch (err) {
          UI.toast(err.message, "error");
        }
      }
    });
  },

  _delete(nb) {
    UI.confirm(`Delete notebook «${nb.title}» from this manager? Nothing is removed from Kaggle itself.`, async () => {
      try {
        await API.del(`/api/notebooks/${nb.id}`);
        UI.toast("Notebook deleted");
        this._load(document.getElementById("view"));
      } catch (e) {
        UI.toast(e.message, "error");
      }
    });
  },

  async _openEditor(nb) {
    const isNew = !nb;
    const code = nb ? notebookCellToCode(nb.source) : "print('Hello from Kaggle Cloud Manager')";
    let accounts = [];
    try { accounts = await API.get("/api/accounts"); } catch (e) { /* non-fatal */ }
    const accountOptions = `<option value="">Default (env)</option>` + accounts.map((a) =>
      `<option value="${a.id}" ${nb && nb.account_id === a.id ? "selected" : ""}>${UI.esc(a.name)} (${UI.esc(a.username)})</option>`
    ).join("");
    const ACCELS = ["auto", "cpu", "gpu_t4", "gpu_t4_x2", "gpu_p100", "gpu_v100", "gpu_l4", "gpu_a100", "gpu_h100", "tpu_v3_8"];
    const accOptions = ACCELS.map((a) =>
      `<option value="${a}" ${nb && nb.accelerator === a ? "selected" : ""}>${a}</option>`
    ).join("");
    const m = UI.modal({
      title: isNew ? "New notebook" : `Edit — ${nb.title}`,
      wide: true,
      body: `
        <div class="form-row">
          <div class="field"><label>Title</label><input id="ed-title" value="${UI.esc(nb ? nb.title : "")}" placeholder="Notebook title"></div>
          <div class="field"><label>Description</label><input id="ed-desc" value="${UI.esc(nb ? nb.description : "")}" placeholder="optional"></div>
        </div>
        <div class="form-row">
          <div class="field"><label>Kaggle account</label><select id="ed-acc">${accountOptions}</select></div>
          <div class="field"><label>Schedule (cron, UTC)</label><input id="ed-sch" value="${UI.esc(nb ? nb.schedule : "")}" placeholder="e.g. 0 9 * * 1 — empty = off"></div>
        </div>
        <div class="form-row">
          <div class="field"><label>Accelerator</label><select id="ed-accel">${accOptions}</select></div>
          <div class="field"><label>Internet / dataset access</label>
            <label style="display:flex;align-items:center;gap:8px;margin-top:8px">
              <input type="checkbox" id="ed-inet" ${nb && nb.internet === 0 ? "" : "checked"}> enable internet
            </label>
          </div>
        </div>
        <div class="field"><label>Code</label><textarea id="ed-code" rows="12" spellcheck="false">${UI.esc(code)}</textarea></div>`,
      footer: `
        <button class="btn btn-ghost" data-act="cancel" type="button">Cancel</button>
        <button class="btn btn-primary" data-act="save" type="button">${isNew ? "Create" : "Save"}</button>`,
    });

    m.root.addEventListener("click", async (e) => {
      const act = e.target.dataset.act;
      if (!act) return;
      if (act === "cancel") { m.close(); return; }
      const title = document.getElementById("ed-title").value.trim();
      if (!title) { UI.toast("Title is required", "error"); return; }
      const source = codeToNotebookJson(document.getElementById("ed-code").value);
      const desc = document.getElementById("ed-desc").value;
      const accVal = document.getElementById("ed-acc").value;
      const accountId = accVal === "" ? null : Number(accVal);
      const cron = document.getElementById("ed-sch").value.trim();
      const accelerator = document.getElementById("ed-accel").value;
      const internet = document.getElementById("ed-inet").checked;
      try {
        if (isNew) {
          const created = await API.post("/api/notebooks", {
            title, description: desc, code: document.getElementById("ed-code").value,
            account_id: accountId, schedule: cron, accelerator, internet,
          });
          UI.toast(`Notebook #${created.id} created (stored locally — press Run to execute on Kaggle)`);
        } else {
          await API.put(`/api/notebooks/${nb.id}`, {
            title, description: desc, source,
            account_id: accountId, schedule: cron, accelerator, internet,
          });
          UI.toast("Notebook saved");
        }
        m.close();
        this._load(document.getElementById("view"));
      } catch (err) {
        UI.toast(err.message, "error");
      }
    });
  },
};
