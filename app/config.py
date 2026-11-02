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
MAX_CHAT_HISTORY = 60
MAX_AGENT_TURNS = 14
LLM_TIMEOUT_SECONDS = 120

POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "6"))
WORKER_MAX_POLL_FAILURES = 3

AI_DEFAULTS = {
    "ai_base_url": os.environ.get("AI_BASE_URL", ""),
    "ai_api_key": os.environ.get("AI_API_KEY", ""),
    "ai_model": os.environ.get("AI_MODEL", ""),
    "ai_temperature": os.environ.get("AI_TEMPERATURE", "0.2"),
}

KAGGLE_DEFAULTS = {
    "kaggle_username": os.environ.get("KAGGLE_USERNAME", ""),
    "kaggle_key": os.environ.get("KAGGLE_KEY", ""),
}
