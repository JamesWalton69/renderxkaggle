/* Settings view: Kaggle accounts (Neon Postgres), admin password, backup export. */
"use strict";

window.Views = window.Views || {};

window.Views.settings = {
  title: "Settings",

  render(root) {
    UI.loading(root);
    this._load(root);
  },

  async _load(root) {
    let s, accounts;
    try {
      [s, accounts] = await Promise.all([
        API.get("/api/settings"),
        API.get("/api/accounts"),
      ]);
    } catch (e) {
      root.innerHTML = `<div class="empty">${UI.esc(e.message)}</div>`;
      return;
    }

    root.innerHTML = `
      <div class="settings-grid">
        <div class="card">
          <div class="card-title">Kaggle accounts · ${accounts.length}</div>
          ${accounts.length ? `<div style="margin-bottom:14px">
            ${accounts.map((a) => `
              <div class="kv">
                <span class="k">${UI.esc(a.name)} <span class="mono" style="color:var(--faint)">(${UI.esc(a.username)})</span>
                  ${a.is_default ? `<span class="badge badge-violet">default</span>` : ""}</span>
                <span class="v" style="display:flex;gap:8px;align-items:center">
                  <button class="btn btn-ghost btn-sm" data-act="verify" data-id="${a.id}" type="button">Test</button>
                  ${a.origin === "dashboard" || !a.origin ? `
                    ${a.is_default ? "" : `<button class="btn btn-ghost btn-sm" data-act="default" data-id="${a.id}" type="button">Make default</button>`}
                    <button class="btn btn-danger btn-sm" data-act="delacc" data-id="${a.id}" type="button">Remove</button>
                  ` : `<span class="mono" style="color:var(--faint)">env</span>`}
                </span>
              </div>`).join("")}
          </div>` : `<p style="color:var(--muted);font-size:12.5px;margin-bottom:14px">No accounts registered yet.</p>`}

          <div style="border-top:1px solid var(--border);padding-top:14px;margin-top:6px">
            <div style="font-weight:600;font-size:13px;margin-bottom:10px">Add Kaggle account</div>
            <div class="form-row">
              <div class="field"><label>Account name</label><input id="acc-name" placeholder="e.g. Primary"></div>
              <div class="field"><label>Kaggle username</label><input id="acc-user" placeholder="e.g. arydds34df"></div>
            </div>
            <div class="field">
              <label>API key</label>
              <input id="acc-key" type="password" placeholder="32-char key or paste kaggle.json content" autocomplete="new-password">
              <div style="color:var(--faint);font-size:11.5px;margin-top:4px">
                From downloaded <span class="mono">kaggle.json</span> (<a href="https://www.kaggle.com/settings" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:underline">kaggle.com/settings</a> → <em>Create New Token</em>). Do not use your Kaggle login password.
              </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
              <button class="btn btn-primary" id="acc-add" type="button">＋ Add account</button>
            </div>
            <div class="form-error" id="acc-err" style="margin-top:8px"></div>
          </div>
          <p style="color:var(--faint);font-size:12px;margin-top:14px">Accounts are stored durably in Neon Postgres. API keys are encrypted at rest with AES-GCM. Select which account runs each notebook in the notebook editor.</p>
        </div>

        <div class="card">
          <div class="card-title">Admin password</div>
          <div class="field"><label>Current password</label><input id="s-pw-cur" type="password" autocomplete="current-password"></div>
          <div class="field"><label>New password (min 8 chars)</label><input id="s-pw-new" type="password" autocomplete="new-password"></div>
          <button class="btn btn-primary" id="s-pw-save" type="button">Change password</button>
          <div class="form-error" id="s-pw-err"></div>
        </div>

        <div class="card">
          <div class="card-title">System</div>
          <div class="kv"><span class="k">version</span><span class="v">${UI.esc(s.version)}</span></div>
          <div class="kv"><span class="k">data directory</span><span class="v mono">${UI.esc(s.data_dir)}</span></div>
          <div class="kv"><span class="k">worker poll</span><span class="v">${s.poll_seconds}s</span></div>
          <div class="kv"><span class="k">queue mode</span><span class="v">one active execution</span></div>
          <div style="margin-top:14px">
            <a class="btn btn-ghost" href="/api/export" download>⬇ Export backup (JSON)</a>
          </div>
          <p style="color:var(--faint);font-size:12px;margin-top:12px">Storage is backed by Neon Postgres. Secrets are excluded from backup exports.</p>
        </div>
      </div>`;

    const tryAutoFill = (text) => {
      if (!text || !text.includes("{") || !text.includes("}")) return;
      try {
        const start = text.indexOf("{");
        const end = text.lastIndexOf("}") + 1;
        const obj = JSON.parse(text.slice(start, end));
        if (obj && typeof obj === "object") {
          if (obj.username) {
            const uField = document.getElementById("acc-user");
            if (uField) uField.value = obj.username;
            const nField = document.getElementById("acc-name");
            if (nField && !nField.value) nField.value = obj.username;
          }
          if (obj.key) {
            const kField = document.getElementById("acc-key");
            if (kField) kField.value = obj.key;
          }
          UI.toast("Auto-filled from kaggle.json");
        }
      } catch (e) {}
    };

    const keyInput = root.querySelector("#acc-key");
    const userInput = root.querySelector("#acc-user");
    if (keyInput) keyInput.oninput = (e) => tryAutoFill(e.target.value);
    if (userInput) userInput.oninput = (e) => tryAutoFill(e.target.value);

    root.querySelector("#acc-add").onclick = () => this._addAccount(root);
    root.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = () => {
        const id = Number(btn.dataset.id);
        if (btn.dataset.act === "verify") this._verifyAccount(id, btn);
        if (btn.dataset.act === "default") this._setDefault(id, root);
        if (btn.dataset.act === "delacc") this._deleteAccount(id, root);
      };
    });
    root.querySelector("#s-pw-save").onclick = () => this._savePw(root);
  },

  async _verifyAccount(id, btn) {
    const orig = btn.textContent;
    btn.textContent = "Testing...";
    btn.disabled = true;
    try {
      const res = await API.get(`/api/accounts/${id}/verify`);
      if (res.ok) {
        UI.toast(`Account @${res.username} is valid (${res.notebooks_count} notebooks found)`);
      } else {
        UI.toast(res.error, "error", 8000);
      }
    } catch (e) {
      UI.toast(e.message, "error", 8000);
    } finally {
      btn.textContent = orig;
      btn.disabled = false;
    }
  },

  async _addAccount(root) {
    const name = document.getElementById("acc-name").value.trim();
    const username = document.getElementById("acc-user").value.trim();
    const key = document.getElementById("acc-key").value.trim();
    const err = document.getElementById("acc-err");
    const btn = document.getElementById("acc-add");
    err.textContent = "";
    if (!username || !key) {
      err.textContent = "Kaggle username and API key are required.";
      return;
    }
    const orig = btn.textContent;
    btn.textContent = "Verifying & Adding...";
    btn.disabled = true;
    try {
      const v = await API.post("/api/accounts/verify", { username, key });
      if (!v.ok) {
        err.textContent = v.error || "Failed to authenticate with Kaggle.";
        btn.textContent = orig;
        btn.disabled = false;
        return;
      }
      await API.post("/api/accounts", { name: name || username, username, api_key: key });
      UI.toast(`Account @${username} added and verified successfully!`);
      this._load(root);
    } catch (e) {
      err.textContent = e.message;
      btn.textContent = orig;
      btn.disabled = false;
    }
  },

  async _setDefault(id, root) {
    try {
      await API.put(`/api/accounts/${id}/default`, {});
      UI.toast("Default account updated");
      this._load(root);
    } catch (e) { UI.toast(e.message, "error"); }
  },

  _deleteAccount(id, root) {
    UI.confirm("Remove this Kaggle account? Existing jobs will keep running until finished.", async () => {
      try {
        await API.del(`/api/accounts/${id}`);
        UI.toast("Account removed");
        this._load(root);
      } catch (e) { UI.toast(e.message, "error"); }
    });
  },

  async _savePw(root) {
    try {
      await API.post("/api/settings/password", {
        current_password: document.getElementById("s-pw-cur").value,
        new_password: document.getElementById("s-pw-new").value,
      });
      UI.toast("Password changed");
      document.getElementById("s-pw-cur").value = "";
      document.getElementById("s-pw-new").value = "";
      document.getElementById("s-pw-err").textContent = "";
    } catch (e) {
      document.getElementById("s-pw-err").textContent = e.message;
    }
  },
};
