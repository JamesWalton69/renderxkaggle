"""Tiny HTTP client for the Kaggle Cloud Manager REST API.

Handles admin login -> kc_session cookie, session refresh on 401, and
surfaces backend error `detail` strings verbatim as ManagerError.
"""
from __future__ import annotations

import httpx


class ManagerError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"manager {status}: {detail}")
        self.status = status
        self.detail = detail


class ManagerClient:
    def __init__(self, base_url: str, password: str, timeout: float = 60.0):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=20.0),
        )
        self._password = password
        self._cookie: str | None = None

    # ---------------------------------------------------------------- auth

    def _login(self) -> None:
        r = self.http.post("/api/login", json={"password": self._password})
        if r.status_code != 200:
            raise ManagerError(r.status_code, self._detail(r))
        for line in r.headers.get_list("set-cookie"):
            if line.lower().startswith("kc_session="):
                self._cookie = line.split(";", 1)[0]
                return
        raise ManagerError(0, "login succeeded but no kc_session cookie returned")

    def _ensure_auth(self) -> None:
        if self._cookie is None:
            self._login()

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        try:
            d = r.json().get("detail")
            if isinstance(d, str):
                return d
        except Exception:
            pass
        return f"HTTP {r.status_code}"

    def _call(self, method: str, path: str, json_body: dict | None = None) -> httpx.Response:
        for attempt in range(2):
            self._ensure_auth()
            headers = {"Cookie": self._cookie} if self._cookie else None
            r = self.http.request(method, path, json=json_body, headers=headers)
            if r.status_code == 401 and attempt == 0:
                self._cookie = None  # expired session -> re-login once
                continue
            return r
        return r  # unreachable

    def _json(self, method: str, path: str, json_body: dict | None = None):
        r = self._call(method, path, json_body)
        if r.status_code >= 400:
            raise ManagerError(r.status_code, self._detail(r))
        return r.json() if r.content else {}

    # ---------------------------------------------------------------- endpoints

    def overview(self) -> dict:
        return self._json("GET", "/api/overview")

    def notebooks(self) -> list:
        return self._json("GET", "/api/notebooks")

    def create_notebook(self, title: str, code: str, accelerator: str = "auto", internet: bool = True) -> dict:
        return self._json(
            "POST", "/api/notebooks",
            {"title": title, "code": code, "accelerator": accelerator, "internet": internet},
        )

    def run_notebook(self, notebook_id: int, comment: str | None = None) -> dict:
        body = {"comment": comment} if comment else {}
        return self._json("POST", f"/api/notebooks/{notebook_id}/run", body)

    def jobs(self, status: str | None = None, limit: int = 50) -> list:
        path = f"/api/jobs?limit={limit}" + (f"&status={status}" if status else "")
        return self._json("GET", path)

    def job(self, job_id: int) -> dict:
        return self._json("GET", f"/api/jobs/{job_id}")

    def cancel_job(self, job_id: int) -> dict:
        return self._json("POST", f"/api/jobs/{job_id}/cancel")

    def requeue_job(self, job_id: int) -> dict:
        return self._json("POST", f"/api/jobs/{job_id}/requeue")

    def accounts(self) -> list:
        return self._json("GET", "/api/accounts")

    def verify_account(self, account_id: int) -> dict:
        return self._json("GET", f"/api/accounts/{account_id}/verify")

    def close(self) -> None:
        self.http.close()
