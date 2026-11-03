"""Offline tests: patch the client with a fake, exercise every MCP tool.

No network, no live server, no credentials — pure routing/error tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server.server as srv
from mcp_server.manager_client import ManagerError

_FACTORY = srv._client  # the real factory function, before tests patch it


class FakeClient:
    def __init__(self):
        self.calls = []

    def overview(self):
        self.calls.append("overview")
        return {"engine_state": "idle", "total_jobs": 3}

    def notebooks(self):
        self.calls.append("notebooks")
        return [{"id": 1, "title": "n1"}]

    def create_notebook(self, title, code, accelerator="auto", internet=True):
        self.calls.append(("create", title, accelerator, internet))
        return {"id": 2}

    def run_notebook(self, notebook_id, comment=None):
        self.calls.append(("run", notebook_id, comment))
        return {"job_id": 7}

    def jobs(self, status=None, limit=50):
        self.calls.append(("jobs", status, limit))
        return [{"id": 7, "status": "queued"}]

    def job(self, job_id):
        self.calls.append(("job", job_id))
        return {"id": job_id, "logs": []}

    def cancel_job(self, job_id):
        self.calls.append(("cancel", job_id))
        return {"ok": True, "message": "stopping"}

    def requeue_job(self, job_id):
        self.calls.append(("requeue", job_id))
        return {"ok": True}

    def approvals(self):
        self.calls.append("approvals")
        return [{"id": 3, "tool": "run"}]

    def accounts(self):
        self.calls.append("accounts")
        return [{"id": 1, "name": "acc", "username": "u", "has_key": True, "is_default": True}]

    def decide_approval(self, approval_id, approve):
        self.calls.append(("decide", approval_id, approve))
        return {"ok": True}


def test_tools_route_to_client():
    f = FakeClient()
    srv._client = lambda: f

    assert srv.manager_status()["engine_state"] == "idle"
    assert srv.list_notebooks()["items"][0]["id"] == 1
    assert srv.create_notebook("t", "code", "gpu_t4", False)["id"] == 2
    assert srv.run_notebook(5)["job_id"] == 7
    assert srv.run_notebook(5, "why")["job_id"] == 7
    assert srv.list_jobs("queued")["items"][0]["status"] == "queued"
    assert srv.get_job(7)["id"] == 7
    assert srv.stop_or_cancel_job(1)["message"] == "stopping"
    assert srv.requeue_job(2)["ok"] is True
    assert srv.list_approvals()["items"][0]["id"] == 3
    assert srv.list_accounts()["items"][0]["is_default"] is True
    assert srv.decide_approval(3, True)["ok"] is True

    assert ("create", "t", "gpu_t4", False) in f.calls
    assert ("run", 5, None) in f.calls and ("run", 5, "why") in f.calls
    assert ("jobs", "queued", 50) in f.calls
    assert ("decide", 3, True) in f.calls
    assert ("cancel", 1) in f.calls and ("requeue", 2) in f.calls


def test_manager_error_surfaces_detail():
    class Bad(FakeClient):
        def overview(self):
            raise ManagerError(409, "job is running; cannot cancel")

    srv._client = Bad
    try:
        srv.manager_status()
        assert False, "expected ManagerError"
    except ManagerError as e:
        assert "job is running" in str(e)
        assert e.status == 409


def test_needs_password_env(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(srv, "_load_password", lambda: None)
    srv._inst = None
    srv._client = _FACTORY
    try:
        srv._client()
        assert False, "expected ManagerError"
    except ManagerError as e:
        assert "ADMIN_PASSWORD" in str(e)


def test_load_password_prefers_env_over_file(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text('ADMIN_PASSWORD=from-file\n', encoding="utf-8")
    monkeypatch.setattr(srv, "_ENV_FILE", f)
    monkeypatch.setenv("ADMIN_PASSWORD", "from-env")
    assert srv._load_password() == "from-env"


def test_load_password_falls_back_to_env_file(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text('ADMIN_PASSWORD="from-file"\n', encoding="utf-8")
    monkeypatch.setattr(srv, "_ENV_FILE", f)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert srv._load_password() == "from-file"
