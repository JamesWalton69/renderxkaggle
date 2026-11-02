/* Approvals view: pending sensitive-agent-action requests. */
"use strict";

window.Views = window.Views || {};

window.Views.approvals = {
  title: "Approvals",
  _timer: null,

  render(root) {
    UI.loading(root);
    this._load(root);
    this._timer = setInterval(() => this._load(root, true), 8000);
  },

  destroy() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  },

  async _load(root, silent) {
    try {
      const list = await API.get("/api/approvals?status=pending");
      const decided = await API.get("/api/approvals?status=approved");
      const rejected = await API.get("/api/approvals?status=rejected");
      this._paint(root, list, [...decided.slice(0, 5), ...rejected.slice(0, 5)]);
    } catch (e) {
      if (!silent) UI.toast(e.message, "error");
    }
  },

  _paint(root, pending, recent) {
    if (!pending.length) {
      root.innerHTML = `<div class="card"><div class="empty"><div class="big">✅</div>No approvals pending. The assistant only pauses here for consequential actions (submitting, cancelling, deleting, rescheduling).</div></div>`;
      return;
    }
    root.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px">
        ${pending.map((a) => `
          <div class="card approval-card">
            <div class="approval-tool">${UI.esc(a.tool)} <span style="float:right">${UI.badge("pending", "pending")}</span></div>
            <div class="approval-params">${UI.esc(JSON.stringify(a.params || {}, null, 2))}</div>
            ${a.reason ? `<div class="approval-reason">💬 ${UI.esc(a.reason)}</div>` : ""}
            <div class="approval-reason" style="margin-top:4px">requested ${UI.fmtTime(a.created_at)}</div>
            <div style="display:flex;gap:8px;margin-top:14px">
              <button class="btn btn-success btn-sm" data-act="approve" data-id="${a.id}" type="button">✓ Approve</button>
              <button class="btn btn-danger btn-sm" data-act="reject" data-id="${a.id}" type="button">✕ Reject</button>
            </div>
          </div>`).join("")}
      </div>
      ${recent.length ? `
        <div class="card" style="margin-top:18px">
          <div class="card-title">Recent decisions</div>
          ${recent.map((a) => `<div class="kv"><span class="k">${UI.esc(a.tool)} · ${UI.fmtTime(a.decided_at)}</span><span class="v">${UI.badge(a.status)}</span></div>`).join("")}
        </div>` : ""}
    `;

    root.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = () => this._decide(Number(btn.dataset.id), btn.dataset.act === "approve");
    });
  },

  async _decide(id, approve) {
    const verb = approve ? "Approve" : "Reject";
    UI.confirm(`${verb} this action? The assistant will be informed and continue.`, async () => {
      try {
        const btn = document.querySelector(`[data-id="${id}"]`);
        if (btn) { btn.disabled = true; btn.textContent = "…"; }
        await API.post(`/api/approvals/${id}/decide`, { approve });
        UI.toast(`Decision recorded — assistant notified`);
        window.dispatchEvent(new CustomEvent("kc-approvals-changed"));
        window.dispatchEvent(new CustomEvent("kc-jobs-changed"));
        this._load(document.getElementById("view"));
      } catch (e) {
        UI.toast(e.message, "error");
      }
    });
  },
};
