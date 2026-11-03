/* Shared UI helpers: DOM, toasts, modals, formatting, badges. */
"use strict";

const UI = {
  esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  },

  fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  },

  fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  },

  duration(startIso, endIso) {
    if (!startIso || !endIso) return "—";
    const s = new Date(startIso), e = new Date(endIso);
    if (isNaN(s) || isNaN(e)) return "—";
    let sec = Math.max(0, Math.round((e - s) / 1000));
    const h = Math.floor(sec / 3600); sec %= 3600;
    const m = Math.floor(sec / 60); sec %= 60;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${sec}s`;
    return `${sec}s`;
  },

  statusClass(status) {
    const map = {
      queued: "queued", running: "running", success: "success", failed: "failed",
      cancelled: "cancelled", interrupted: "interrupted", uncertain: "uncertain",
      stopping: "stopping",
      active: "running", idle: "neutral", pending: "amber",
    };
    return map[status] || "neutral";
  },

  badge(status, label) {
    const cls = this.statusClass(status);
    return `<span class="badge badge-${cls}">${this.esc(label || status)}</span>`;
  },

  toast(message, type = "ok", duration = 3600) {
    const root = document.getElementById("toast-root");
    const el = document.createElement("div");
    el.className = `toast ${type === "ok" ? "" : type}`;
    el.innerHTML = this.esc(message);
    root.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 320); }, duration);
  },

  modal({ title, body, footer, wide }) {
    const root = document.getElementById("modal-root");
    root.innerHTML = `
      <div class="modal-backdrop">
        <div class="modal${wide ? " modal-wide" : ""}">
          <div class="modal-head">
            <div class="modal-title">${this.esc(title)}</div>
            <button class="btn btn-ghost btn-sm modal-close" type="button">✕</button>
          </div>
          <div class="modal-body">${body}</div>
          ${footer ? `<div class="modal-foot">${footer}</div>` : ""}
        </div>
      </div>`;
    const backdrop = root.firstElementChild;
    const close = () => { root.innerHTML = ""; };
    backdrop.querySelector(".modal-close").onclick = close;
    backdrop.addEventListener("mousedown", (e) => {
      if (e.target === backdrop) close();
    });
    return { root, close };
  },

  confirm(message, onYes, { danger = true, yesLabel = "Confirm" } = {}) {
    const m = this.modal({
      title: "Are you sure?",
      body: `<p>${this.esc(message)}</p>`,
      footer: `
        <button class="btn btn-ghost" type="button" data-act="no">Cancel</button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" type="button" data-act="yes">${this.esc(yesLabel)}</button>`,
    });
    m.root.querySelector('[data-act="no"]').onclick = m.close;
    m.root.querySelector('[data-act="yes"]').onclick = () => { m.close(); onYes(); };
  },

  loading(container, text = "Loading…") {
    container.innerHTML = `<div class="empty"><span class="spinner"></span><div style="margin-top:10px;font-size:12.5px">${this.esc(text)}</div></div>`;
  },

  debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  },
};

/* derive code text from an ipynb JSON string */
function notebookCellToCode(sourceJson) {
  try {
    const nb = JSON.parse(sourceJson);
    if (nb && Array.isArray(nb.cells)) {
      return nb.cells
        .filter((c) => c.cell_type === "code")
        .map((c) => (Array.isArray(c.source) ? c.source.join("") : String(c.source || "")))
        .join("\n");
    }
  } catch (e) { /* fall through */ }
  return sourceJson;
}

/* build an ipynb JSON string from code text */
function codeToNotebookJson(code) {
  const nb = {
    cells: [{
      cell_type: "code", execution_count: null, metadata: {}, outputs: [],
      source: String(code || "").split("\n").map((l) => l + "\n"),
    }],
    metadata: { kernelspec: { display_name: "Python 3", language: "python", name: "python3" } },
    nbformat: 4,
    nbformat_minor: 5,
  };
  return JSON.stringify(nb, null, 1);
}
