/* Assistant (chat) view — talks to the autonomous agent. */
"use strict";

window.Views = window.Views || {};

window.Views.chat = {
  title: "Assistant",
  _timer: null,
  _sending: false,

  render(root) {
    root.innerHTML = `
      <div class="card chat-panel">
        <div class="chat-scroll" id="chat-scroll"></div>
        <div class="chat-input-row">
          <textarea id="chat-input" placeholder="Tell me what to do… e.g. “create a notebook that fetches a dataset and sum it, then run it”"></textarea>
          <button class="btn btn-primary" id="chat-send" type="button">Send</button>
        </div>
        <div class="form-error" id="chat-error" style="min-height:0"></div>
      </div>`;
    this._scroller = root.querySelector("#chat-scroll");
    this._input = root.querySelector("#chat-input");
    this._error = root.querySelector("#chat-error");
    this._loadHistory();
    this._sendBtn = root.querySelector("#chat-send");
    this._sendBtn.onclick = () => this._send();
    this._input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); }
    });
    this._timer = setInterval(() => this._loadHistory(true), 8000);
  },

  destroy() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  },

  async _loadHistory(silent) {
    try {
      const data = await API.get("/api/agent/history");
      this._paint(data);
    } catch (e) {
      if (!silent) this._error.textContent = e.message;
    }
  },

  _paint(messages) {
    const scroller = this._scroller;
    if (!scroller) return;
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
    scroller.innerHTML = messages.map((m) => {
      const meta = m.meta || {};
      if (m.role === "note") {
        let label = meta.tool ? `tool · ${UI.esc(meta.tool)}` : "system";
        return `<div class="msg note"><div>${UI.esc(m.content)}</div><div class="msg-time">${label} · ${UI.fmtTime(m.ts)}</div></div>`;
      }
      if (m.role === "user") {
        return `<div class="msg user">${UI.esc(m.content)}<div class="msg-time" style="color:rgba(255,255,255,.6)">${UI.fmtTime(m.ts)}</div></div>`;
      }
      // assistant
      const chips = this._chips(meta);
      return `<div class="msg assistant">${UI.esc(m.content)}${chips}<div class="msg-time">${UI.fmtTime(m.ts)}</div></div>`;
    }).join("") || `<div class="empty" style="padding:30px"><div class="big">🤖</div>Ask me anything about your Kaggle notebooks.<br>I plan each task myself and ask for approval before consequential actions.</div>`;
    if (atBottom) scroller.scrollTop = scroller.scrollHeight;
  },

  _chips(meta) {
    const chips = [];
    if (meta.tool && meta.approval_id) {
      chips.push(`<span class="chip wait">⏸ awaiting approval · ${UI.esc(meta.tool)}</span>`);
    }
    if (Array.isArray(meta.events)) {
      for (const ev of meta.events) {
        chips.push(`<span class="chip ${ev.status === "wait" ? "wait" : "done"}">${UI.esc(ev.tool)}</span>`);
      }
    }
    return chips.length ? `<div style="margin-top:8px">${chips.join("")}</div>` : "";
  },

  async _send() {
    const text = this._input.value.trim();
    if (!text || this._sending) return;
    this._sending = true;
    this._sendBtn.disabled = true;
    this._error.textContent = "";
    try {
      const result = await API.post("/api/agent/message", { text });
      this._input.value = "";
      await this._loadHistory();
      if (result.pending_approval) {
        UI.toast(`Approval required for this action — see Approvals tab`, "warn");
        window.dispatchEvent(new CustomEvent("kc-approvals-changed"));
      }
    } catch (e) {
      this._error.textContent = e.message;
    } finally {
      this._sending = false;
      this._sendBtn.disabled = false;
      this._input.focus();
    }
  },
};
