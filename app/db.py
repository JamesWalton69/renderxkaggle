"""PostgreSQL persistence layer (psycopg2).

Every operation opens a short-lived connection from config.DATABASE_URL.
Neon (serverless Postgres) keeps the data durable outside Render's ephemeral
instance filesystem.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from . import config

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
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug        TEXT NOT NULL UNIQUE,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL DEFAULT '{}',   -- ipynb JSON
  schedule    TEXT NOT NULL DEFAULT '',     -- cron "m h dom mon dow"; '' = off
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  last_run_at TEXT,
  account_id  INTEGER,
  accelerator TEXT NOT NULL DEFAULT 'auto',
  internet    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS jobs (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  notebook_id   INTEGER,
  kind          TEXT NOT NULL DEFAULT 'manual',   -- manual | schedule
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
  id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job_id INTEGER NOT NULL,
  ts     TEXT NOT NULL,
  line   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id);
CREATE TABLE IF NOT EXISTS kaggle_accounts (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name       TEXT NOT NULL,
  username   TEXT NOT NULL,
  key_enc    TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        config.DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=10,
    )


def init_db() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- primitives

def _rows(conn: psycopg2.extensions.connection, sql: str, params: Iterable = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def _one(conn: psycopg2.extensions.connection, sql: str, params: Iterable = ()) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None


def _exec(conn: psycopg2.extensions.connection, sql: str, params: Iterable = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        # statements ending in "RETURNING id" hand back the new id
        if cur.description and cur.description[0].name.lower() == "id":
            row = cur.fetchone()
            return int(row["id"]) if row else 0
        return 0


def fetch_all(sql: str, params: Iterable = ()) -> list[dict]:
    conn = get_conn()
    try:
        return _rows(conn, sql, params)
    finally:
        conn.close()


def fetch_one(sql: str, params: Iterable = ()) -> Optional[dict]:
    conn = get_conn()
    try:
        return _one(conn, sql, params)
    finally:
        conn.close()


def execute(sql: str, params: Iterable = ()) -> int:
    conn = get_conn()
    try:
        result = _exec(conn, sql, params)
        conn.commit()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: Any = "") -> Any:
    row = fetch_one("SELECT value FROM settings WHERE key=%s", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key,value) VALUES(%s,%s) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        (key, json.dumps(value)),
    )


# ---------------------------------------------------------------- helpers

def truncate_job_logs(conn: psycopg2.extensions.connection, job_id: int) -> None:
    """Keep the most recent MAX_LOG_LINES_PER_JOB lines per job."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM job_logs WHERE job_id=%s AND id NOT IN ("
            "  SELECT id FROM job_logs WHERE job_id=%s ORDER BY id DESC LIMIT %s)",
            (job_id, job_id, config.MAX_LOG_LINES_PER_JOB),
        )


def add_job_log(job_id: int, line: str) -> None:
    execute(
        "INSERT INTO job_logs(job_id, ts, line) VALUES(%s,%s,%s)",
        (job_id, now_iso(), line[:2000]),
    )
    # trim occasionally
    conn = get_conn()
    try:
        truncate_job_logs(conn, job_id)
        conn.commit()
    finally:
        conn.close()


def enqueue_job(notebook_id: int, kind: str = "manual") -> int:
    return execute(
        "INSERT INTO jobs(notebook_id, kind, status, created_at) VALUES(%s,%s,%s,%s) RETURNING id",
        (notebook_id, kind, config.JOB_QUEUED, now_iso()),
    )


# ---------------------------------------------------------------- Kaggle accounts

def list_accounts() -> list[dict]:
    try:
        rows = fetch_all(
            "SELECT id, name, username, is_default FROM kaggle_accounts "
            "ORDER BY is_default DESC, id"
        )
        if rows:
            for r in rows:
                r["has_key"] = True
                r["origin"] = "dashboard"
            return rows
    except Exception:
        pass
    env_accs = config.accounts()
    for a in env_accs:
        a["has_key"] = True
        a["origin"] = "env"
    return env_accs


def get_account(account_id) -> Optional[dict]:
    """Return decrypted credentials for an account, falling back to env."""
    if account_id is not None:
        try:
            row = fetch_one(
                "SELECT * FROM kaggle_accounts WHERE id=%s", (int(account_id),)
            )
            if row:
                from . import security
                try:
                    key = security.decrypt_secret(row["key_enc"])
                except Exception:
                    key = ""
                return {"id": row["id"], "name": row["name"], "username": row["username"], "key": key}
        except Exception:
            pass
    else:
        try:
            row = fetch_one(
                "SELECT * FROM kaggle_accounts WHERE is_default=1 LIMIT 1"
            )
            if not row:
                row = fetch_one("SELECT * FROM kaggle_accounts ORDER BY id LIMIT 1")
            if row:
                from . import security
                try:
                    key = security.decrypt_secret(row["key_enc"])
                except Exception:
                    key = ""
                return {"id": row["id"], "name": row["name"], "username": row["username"], "key": key}
        except Exception:
            pass
    return config.get_account(account_id)


def add_account(name: str, username: str, key: str) -> int:
    from . import security
    has_any = fetch_one("SELECT 1 FROM kaggle_accounts LIMIT 1")
    is_default = 1 if not has_any else 0
    return execute(
        "INSERT INTO kaggle_accounts(name, username, key_enc, is_default, created_at) "
        "VALUES(%s,%s,%s,%s,%s) RETURNING id",
        (name, username, security.encrypt_secret(key), is_default, now_iso()),
    )


def update_account(account_id: int, name: str = "", username: str = "", key: str = "") -> None:
    updates, values = [], []
    if name:
        updates.append("name=%s")
        values.append(name)
    if username:
        updates.append("username=%s")
        values.append(username)
    if key:
        from . import security
        updates.append("key_enc=%s")
        values.append(security.encrypt_secret(key))
    if updates:
        values.append(account_id)
        execute(f"UPDATE kaggle_accounts SET {', '.join(updates)} WHERE id=%s", tuple(values))


def set_default_account(account_id: int) -> None:
    execute("UPDATE kaggle_accounts SET is_default=0")
    execute(
        "UPDATE kaggle_accounts SET is_default=1 WHERE id=%s",
        (account_id,),
    )


def delete_account(account_id: int) -> None:
    row = fetch_one(
        "SELECT is_default FROM kaggle_accounts WHERE id=%s", (account_id,)
    )
    execute("DELETE FROM kaggle_accounts WHERE id=%s", (account_id,))
    if row and row["is_default"]:
        nxt = fetch_one(
            "SELECT id FROM kaggle_accounts ORDER BY id LIMIT 1"
        )
        if nxt:
            execute(
                "UPDATE kaggle_accounts SET is_default=1 WHERE id=%s",
                (nxt["id"],),
            )

