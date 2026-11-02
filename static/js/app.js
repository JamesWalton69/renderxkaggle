/* App bootstrap: auth gate, routing, global polling (badges, engine state). */
"use strict";

const App = {
  currentView: null,

  async boot() {
    let me;
    try {
      me = await API.get("/api/me");
    } catch (e) {
      me = { authenticated: false };
    }
    if (me.authenticated) {
      this.showApp();
    } else {
      this.showLogin();
    }
    window.addEventListener("hashchange", () => this.route());
    window.addEventListener("kc-unauthorized", () => this.showLogin());
    window.addEventListener("kc-approvals-changed", () => this.refreshBadges());
    window.addEventListener("kc-jobs-changed", () => this.refreshBadges());
    setInterval(() => this.refreshBadges(), 15000);
  },

  showLogin() {
    document.getElementById("app-shell").classList.add("hidden");
    document.getElementById("login-view").classList.remove("hidden");
    const form = document.getElementById("login-form");
    const err = document.getElementById("login-error");
    form.onsubmit = async (e) => {
      e.preventDefault();
      err.textContent = "";
      const btn = form.querySelector("button");
      btn.disabled = true;
      btn.textContent = "Signing in…";
      try {
        await API.post("/api/login", { password: document.getElementById("password").value });
        window.location.hash = "#/overview";
        this.showApp();
      } catch (ex) {
        err.textContent = ex.message;
      } finally {
        btn.disabled = false;
        btn.textContent = "Sign in";
      }
    };
  },

  showApp() {
    document.getElementById("login-view").classList.add("hidden");
    document.getElementById("app-shell").classList.remove("hidden");
    document.getElementById("logout-btn").onclick = async () => {
      await API.post("/api/logout", {}).catch(() => {});
      this.showLogin();
    };
    document.getElementById("menu-btn").onclick = () => {
      document.getElementById("sidebar").classList.toggle("open");
    };
    document.querySelectorAll(".nav-item").forEach((a) => {
      a.onclick = () => document.getElementById("sidebar").classList.remove("open");
    });
    document.getElementById("quick-run").onclick = () => {
      location.hash = "#/notebooks";
      setTimeout(() => {
        const v = window.Views.notebooks;
        if (v && v._openEditor) v._openEditor(null);
      }, 200);
    };
    if (!window.location.hash) window.location.hash = "#/overview";
    this.route();
    this.refreshBadges();
  },

  route() {
    const hash = window.location.hash.replace(/^#\/?/, "");
    const [name, query] = hash.split("?");
    const params = {};
    if (query) {
      for (const part of query.split("&")) {
        const [k, v] = part.split("=");
        if (k) params[k] = decodeURIComponent(v || "");
      }
    }
    const viewName = name || "overview";
    const view = window.Views[viewName];
    if (!view) { window.location.hash = "#/overview"; return; }
    if (this.currentView && this.currentView.destroy) this.currentView.destroy();
    this.currentView = view;
    document.getElementById("page-title").textContent = view.title;
    document.querySelectorAll(".nav-item").forEach((a) => {
      a.classList.toggle("active", a.dataset.view === viewName);
    });
    const root = document.getElementById("view");
    view.render(root, params);
  },

  async refreshBadges() {
    try {
      const [ov, approvals] = await Promise.all([
        API.get("/api/overview"),
        API.get("/api/approvals?status=pending"),
      ]);
      const n = approvals.length;
      const badgeSide = document.getElementById("approval-badge");
      const badgeTop = document.getElementById("approval-badge-top");
      if (badgeSide) { badgeSide.hidden = n === 0; badgeSide.textContent = n; }
      if (badgeTop) { badgeTop.hidden = n === 0; badgeTop.textContent = n; }
      const dot = document.getElementById("engine-dot");
      const label = document.getElementById("engine-label");
      if (dot) {
        dot.className = `engine-dot ${ov.engine_state}`;
        label.textContent = ov.engine_state === "running"
          ? `running job #${ov.active_job.id}`
          : ov.engine_state;
      }
      window.Views.overview._feed = (ov.recent_jobs || []).length
        ? [] : window.Views.overview._feed;
      // refresh feed objects of recent jobs
      if (ov.recent_jobs && ov.recent_jobs.length) {
        window.Views.overview._feed = ov.recent_jobs.map((j) => ({
          job_id: j.id, ts: j.finished_at || j.created_at,
          line: `job #${j.id} → ${j.status}${j.notebook_title ? ` (${j.notebook_title})` : ""}`,
        })).sort((a, b) => (a.ts < b.ts ? 1 : -1)).slice(0, 15);
      }
    } catch (e) { /* poll quietly */ }
  },
};

document.addEventListener("DOMContentLoaded", () => App.boot());
