"""MCP server exposing the Kaggle Cloud Manager as tools — a router that lets
any MCP-capable agent run Kaggle work through the manager's REST API.

The server holds the admin credentials (env) and handles login/session
internally; agents never see the password or cookie.
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from .manager_client import ManagerClient, ManagerError

mcp = MCPServer("kaggle-cloud-manager")

_inst: ManagerClient | None = None


def _client() -> ManagerClient:
    global _inst
    if _inst is None:
        password = os.environ.get("ADMIN_PASSWORD")
        if not password:
            raise ManagerError(0, "ADMIN_PASSWORD env var is not set")
        _inst = ManagerClient(
            os.environ.get("MANAGER_URL", "https://renderxkaggle.onrender.com"),
            password,
        )
    return _inst


@mcp.tool()
def manager_status() -> dict:
    """Current engine status: job counts, active job, pending approvals, recent jobs."""
    return _client().overview()


@mcp.tool()
def list_notebooks() -> list:
    """List all notebooks with last-run status."""
    return _client().notebooks()


@mcp.tool()
def create_notebook(title: str, code: str, accelerator: str = "auto", internet: bool = True) -> dict:
    """Create a new notebook. accelerator must be one of: auto, cpu, gpu_t4,
    gpu_t4_x2, gpu_p100, gpu_v100, gpu_l4, gpu_a100, gpu_h100, tpu_v3_8."""
    return _client().create_notebook(title, code, accelerator, internet)


@mcp.tool()
def run_notebook(notebook_id: int, comment: str | None = None) -> dict:
    """Queue a run of a notebook. Returns the new job id."""
    return _client().run_notebook(notebook_id, comment)


@mcp.tool()
def list_jobs(status: str | None = None, limit: int = 50) -> list:
    """List jobs, optionally filtered by status (queued|running|stopping|success|
    failed|cancelled|interrupted|uncertain)."""
    return _client().jobs(status, limit)


@mcp.tool()
def get_job(job_id: int) -> dict:
    """Full job detail including log lines."""
    return _client().job(job_id)


@mcp.tool()
def stop_or_cancel_job(job_id: int) -> dict:
    """Cancel a queued/uncertain job, or stop a running/stopping job (outputs are
    snapshotted first, then an immediate-exit stub is pushed to Kaggle).
    Idempotent."""
    return _client().cancel_job(job_id)


@mcp.tool()
def requeue_job(job_id: int) -> dict:
    """Requeue a finished job (success/failed/cancelled/interrupted)."""
    return _client().requeue_job(job_id)


@mcp.tool()
def list_approvals() -> list:
    """Pending AI-assistant approvals awaiting a human decision."""
    return _client().approvals()


@mcp.tool()
def decide_approval(approval_id: int, approve: bool) -> dict:
    """Approve (approve=true) or reject a pending approval request."""
    return _client().decide_approval(approval_id, approve)


if __name__ == "__main__":
    mcp.run()
