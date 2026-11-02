/* Thin fetch wrapper. All calls are same-origin JSON. */
"use strict";

const API = {
  async request(path, opts = {}) {
    const { method = "GET", body, raw = false } = opts;
    const init = { method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(path, init);
    } catch (e) {
      throw new Error(`Network error: ${e.message}`);
    }
    if (resp.status === 401) {
      window.dispatchEvent(new CustomEvent("kc-unauthorized"));
      throw new Error("Not authenticated");
    }
    if (raw) return resp;
    let data = null;
    try { data = await resp.json(); } catch (e) { /* no body */ }
    if (!resp.ok) {
      const detail = (data && data.detail) || `HTTP ${resp.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  },
  get(path) { return this.request(path); },
  post(path, body) { return this.request(path, { method: "POST", body }); },
  put(path, body) { return this.request(path, { method: "PUT", body }); },
  del(path) { return this.request(path, { method: "DELETE" }); },
};
