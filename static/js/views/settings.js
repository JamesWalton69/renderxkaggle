/* Settings view: Kaggle accounts (env), admin password, backup export. */
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
          <div class="card-title">Kaggle accounts ·${accounts.length}</div>
          ${accounts.length ? `<div style="margin-bottom:12px">
            ${accounts.map((a) => `
              <div class="kv">
                <span class="k">${UI.esc(a.name)} <span class="mono" style="color:var(--faint)">(${UI.esc(a.username)})</span>
                  ${a.is_default ? `<span class="badge badge-violet">default</span>` : ""}</span>
                <span class="v mono" style="color:var(--faint)">env</span>
              </div>`).join("")}
          </div>` : `<p style="color:var(--muted);font-size:12.5px;margin-bottom:12px">No accounts configured.</p>`}
          <p style="color:var(--faint);font-size:12px;margin-top:12px">Accounts are configured only via environment variables on the server — <span class="mono">KAGGLE_ACCOUNTS</span> (JSON list) or <span class="mono">KAGGLE_USERNAME</span> / <span class="mono">KAGGLE_KEY</span>. Credentials are never stored by this dashboard. Pick the account that runs each notebook in the notebook editor.</p>
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
          <p style="color:var(--faint);font-size:12px;margin-top:12px">Free-tier Render storage is not persistent across redeploys — export backups regularly. Secrets are excluded from exports.</p>
        </div>
      </div>`;

    root.querySelector("#s-pw-save").onclick = () => this._savePw(root);
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
