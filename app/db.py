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
