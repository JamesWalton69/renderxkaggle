"""SQLite persistence layer (stdlib only).

Every operation opens a short-lived connection; this is cheap and safe for the
low concurrency of a single-process app. WAL mode keeps readers unblocked.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from . import config, security

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notebooks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT NOT NULL UNIQUE,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL DEFAULT '{}',   -- ipynb JSON
  schedule    TEXT NOT NULL DEFAULT '',     -- cron "m h dom mon dow"; '' = off
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  notebook_id   INTEGER,
  kind          TEXT NOT NULL DEFAULT 'manual',   -- manual | schedule | agent
  status        TEXT NOT NULL DEFAULT 'queued',
  kaggle_status TEXT NOT NULL DEFAULT '',
  error         TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  started_at    TEXT,
  finished_at   TEXT,
  meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_notebook ON jobs(notebook_id, id);
CREATE TABLE IF NOT EXISTS job_logs (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  ts     TEXT NOT NULL,
  line   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id);
CREATE TABLE IF NOT EXISTS approvals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  tool          TEXT NOT NULL,
  params        TEXT NOT NULL DEFAULT '{}',
  reason        TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  decided_at    TEXT,
  decided_by    TEXT,
  result_note   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  role  TEXT NOT NULL,            -- user | assistant | note
  content TEXT NOT NULL,
  ts    TEXT NOT NULL,
  meta  TEXT NOT NULL DEFAULT '{}' -- tool events for UI
);
CREATE TABLE IF NOT EXISTS kaggle_accounts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  username   TEXT NOT NULL,
  key_enc   TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path=None) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()
    _seed_defaults(db_path)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notebooks)")]
    if "account_id" not in cols:
        conn.execute("ALTER TABLE notebooks ADD COLUMN account_id INTEGER")
    if "accelerator" not in cols:
        conn.execute("ALTER TABLE notebooks ADD COLUMN accelerator TEXT NOT NULL DEFAULT 'auto'")
    if "internet" not in cols:
        conn.execute("ALTER TABLE notebooks ADD COLUMN internet INTEGER NOT NULL DEFAULT 1")
    conn.commit()


def _seed_defaults(db_path=None) -> None:
    """Bootstrap settings from environment when the DB is empty."""
    for key, value in {**config.AI_DEFAULTS, **config.KAGGLE_DEFAULTS}.items():
        if value and not get_setting(key, db_path=db_path, default=""):
            set_setting(key, value, db_path=db_path)


# ---------------------------------------------------------------- primitives

def _rows(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> list[dict]:
    cur = conn.execute(sql, tuple(params))
    return [dict(r) for r in cur.fetchall()]


def _one(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> Optional[dict]:
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    return dict(row) if row else None


def _exec(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> int:
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur.lastrowid or 0


def fetch_all(sql: str, params: Iterable = (), db_path=None) -> list[dict]:
    conn = get_conn(db_path)
    try:
        return _rows(conn, sql, params)
    finally:
        conn.close()


def fetch_one(sql: str, params: Iterable = (), db_path=None) -> Optional[dict]:
    conn = get_conn(db_path)
    try:
        return _one(conn, sql, params)
    finally:
        conn.close()


def execute(sql: str, params: Iterable = (), db_path=None) -> int:
    conn = get_conn(db_path)
    try:
        return _exec(conn, sql, params)
    finally:
        conn.close()


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: Any = "", db_path=None) -> Any:
    row = fetch_one("SELECT value FROM settings WHERE key=?", (key,), db_path=db_path)
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set_setting(key: str, value: Any, db_path=None) -> None:
    execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
        db_path=db_path,
    )


# ---------------------------------------------------------------- helpers

def truncate_job_logs(conn: sqlite3.Connection, job_id: int) -> None:
    """Keep the most recent MAX_LOG_LINES_PER_JOB lines per job."""
    conn.execute(
        "DELETE FROM job_logs WHERE job_id=? AND id NOT IN ("
        "  SELECT id FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?)",
        (job_id, job_id, config.MAX_LOG_LINES_PER_JOB),
    )


def add_job_log(job_id: int, line: str, db_path=None) -> None:
    execute(
        "INSERT INTO job_logs(job_id, ts, line) VALUES(?,?,?)",
        (job_id, now_iso(), line[:2000]),
        db_path=db_path,
    )
    # trim occasionally (caller may also trim)
    conn = get_conn(db_path)
    try:
        truncate_job_logs(conn, job_id)
        conn.commit()
    finally:
        conn.close()


def enqueue_job(notebook_id: int, kind: str = "manual", db_path=None) -> int:
    return execute(
        "INSERT INTO jobs(notebook_id, kind, status, created_at) VALUES(?,?,?,?)",
        (notebook_id, kind, config.JOB_QUEUED, now_iso()),
        db_path=db_path,
    )


# ---------------------------------------------------------------- Kaggle accounts

def list_accounts(db_path=None) -> list[dict]:
    return fetch_all(
        "SELECT id, name, username, is_default FROM kaggle_accounts "
        "ORDER BY is_default DESC, id",
        db_path=db_path,
    )


def get_account(account_id, db_path=None) -> Optional[dict]:
    """Return decrypted credentials for a registered account, or None."""
    if not account_id:
        return None
    row = fetch_one(
        "SELECT * FROM kaggle_accounts WHERE id=?", (int(account_id),), db_path=db_path
    )
    if not row:
        return None
    try:
        key = security.decrypt_secret(row["key_enc"])
    except Exception:
        key = ""
    return {"id": row["id"], "name": row["name"], "username": row["username"], "key": key}


def add_account(name: str, username: str, key: str, db_path=None) -> int:
    has_any = fetch_one("SELECT 1 FROM kaggle_accounts LIMIT 1", db_path=db_path)
    is_default = 1 if not has_any else 0
    return execute(
        "INSERT INTO kaggle_accounts(name, username, key_enc, is_default, created_at) "
        "VALUES(?,?,?,?,?)",
        (name, username, security.encrypt_secret(key), is_default, now_iso()),
        db_path=db_path,
    )


def set_default_account(account_id: int, db_path=None) -> None:
    execute("UPDATE kaggle_accounts SET is_default=0", db_path=db_path)
    execute(
        "UPDATE kaggle_accounts SET is_default=1 WHERE id=?",
        (account_id,),
        db_path=db_path,
    )


def delete_account(account_id: int, db_path=None) -> None:
    row = fetch_one(
        "SELECT is_default FROM kaggle_accounts WHERE id=?", (account_id,), db_path=db_path
    )
    execute("DELETE FROM kaggle_accounts WHERE id=?", (account_id,), db_path=db_path)
    if row and row["is_default"]:
        nxt = fetch_one(
            "SELECT id FROM kaggle_accounts ORDER BY id LIMIT 1", db_path=db_path
        )
        if nxt:
            execute(
                "UPDATE kaggle_accounts SET is_default=1 WHERE id=?",
                (nxt["id"],),
                db_path=db_path,
            )
