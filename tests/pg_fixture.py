"""Offline Postgres fixture: boots an embedded-postgres (real Postgres binaries
bundled in the wheel) in a temp dir, points DATABASE_URL at it, and creates the
schema.

Call ensure_pg() BEFORE importing app.db-backed modules so config reads the
test URL. Requires `pip install -r requirements-dev.txt` (embedded-postgres).
"""
import os
import tempfile
from pathlib import Path

try:
    from embedded_postgres import get_server
except Exception:  # pragma: no cover — missing dev dependency
    get_server = None

_EPG = None


def ensure_pg() -> None:
    """Boot (once) a throwaway Postgres and point DATABASE_URL at it."""
    global _EPG
    if _EPG is not None:
        return
    if get_server is None:
        raise RuntimeError(
            "embedded-postgres is not installed — run: pip install -r requirements-dev.txt"
        )
    tmp = tempfile.mkdtemp(prefix="kc-pg-")
    _EPG = get_server(Path(tmp) / "pgdata", cleanup_mode="delete")
    _EPG.ensure_postgres_running()
    os.environ["DATABASE_URL"] = _EPG.get_uri(database="postgres")
    from app import db
    db.init_db()


def clear_tables(*names: str) -> None:
    """Empty tables and reset their identity sequences (for test isolation)."""
    from app import db
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            for n in names:
                cur.execute(f"TRUNCATE TABLE {n} RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()
