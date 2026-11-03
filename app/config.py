"""Central configuration and constants."""
from __future__ import annotations

import os
from pathlib import Path

APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "kaggle_manager.db"
SECRET_PATH = DATA_DIR / ".secret_key"
OUTPUTS_DIR = DATA_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Job statuses (the app's own lifecycle).
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCESS = "success"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_INTERRUPTED = "interrupted"   # server restart / crash while running
JOB_UNCERTAIN = "uncertain"       # transient network failures while polling Kaggle
JOB_STOPPING = "stopping"         # stop-stub push sent; awaiting terminal state

TERMINAL_STATUSES = {JOB_SUCCESS, JOB_FAILED, JOB_CANCELLED, JOB_INTERRUPTED}
ACTIVE_STATUSES = {JOB_QUEUED, JOB_RUNNING, JOB_UNCERTAIN, JOB_STOPPING}

# Session auth.
SESSION_COOKIE = "kc_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days

# Limits.
MAX_LOG_LINES_PER_JOB = 2000

POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "6"))
WORKER_MAX_POLL_FAILURES = 3

# ---------------------------------------------------------------- Kaggle accounts (env-only)

def _parse_env_accounts() -> list[dict]:
    """Accounts from the KAGGLE_ACCOUNTS JSON env var, e.g.
    [{"name":"A","username":"u1","key":"k1"}, ...]. Falls back to the legacy
    KAGGLE_USERNAME / KAGGLE_KEY pair (and KAGGLE_API_TOKEN) if unset.
    """
    import json as _json

    raw = os.environ.get("KAGGLE_ACCOUNTS", "").strip()
    accounts: list[dict] = []
    if raw:
        try:
            data = _json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, list):
            for i, item in enumerate(data, start=1):
                if isinstance(item, dict) and item.get("username") and item.get("key"):
                    accounts.append({
                        "id": i,
                        "name": str(item.get("name") or item["username"]),
                        "username": str(item["username"]).strip(),
                        "key": str(item["key"]).strip(),
                    })
    if not accounts:
        u = os.environ.get("KAGGLE_USERNAME", "").strip()
        k = os.environ.get("KAGGLE_KEY", "").strip()
        if u and k:
            accounts.append({"id": 1, "name": u, "username": u, "key": k})
    return accounts


_env_accounts: list[dict] | None = None


def accounts() -> list[dict]:
    """Public sanitized roster [{id, name, username, is_default}]."""
    global _env_accounts
    if _env_accounts is None:
        _env_accounts = _parse_env_accounts()
    return [
        {"id": a["id"], "name": a["name"], "username": a["username"],
         "is_default": a["id"] == 1}
        for a in _env_accounts
    ]


def get_account(account_id) -> dict | None:
    """Resolve an account id (or None → default) to {username, key}."""
    global _env_accounts
    if _env_accounts is None:
        _env_accounts = _parse_env_accounts()
    if not _env_accounts:
        return None
    if account_id is None:
        return dict(_env_accounts[0])
    for a in _env_accounts:
        if a["id"] == int(account_id):
            return dict(a)
    return None
