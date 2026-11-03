"""FastAPI application: REST API + static dashboard.

Single-process design (one uvicorn worker) — the background job worker and the
queue state live in this process, backed by Postgres (Neon). Do not run multiple
workers; the README documents why.
"""
from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, kaggle_client, scheduler, security, worker as worker_mod
from .kaggle_client import KaggleFacade

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ------------------------------------------------------------------ auth

def _session_ok(request: Request) -> bool:
    raw = request.cookies.get(config.SESSION_COOKIE)
    if not raw:
        return False
    row = db.fetch_one(
        "SELECT 1 FROM sessions WHERE token_hash=%s", (security.hash_token(raw),)
    )
    return row is not None


def require_auth(request: Request) -> None:
    if not _session_ok(request):
        raise HTTPException(status_code=401, detail="not authenticated")


# ------------------------------------------------------------------ bootstrap

def _bootstrap_admin() -> None:
    existing = db.get_setting("admin_password_hash", "")
    if existing:
        return
    import os
    password = os.environ.get("ADMIN_PASSWORD", "")
    bootstrap_file = config.DATA_DIR / "admin_bootstrap.txt"
    if not password:
        password = secrets.token_urlsafe(12)
        bootstrap_file.write_text(
            f"Generated admin password: {password}\nchange it in Settings.\n"
        )
    db.set_setting("admin_password_hash", security.hash_password(password))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _bootstrap_admin()
    facade = KaggleFacade()
    app.state.facade = facade
    app.state.worker = worker_mod.Worker(facade)
    await app.state.worker.start()
    try:
        yield
    finally:
        await app.state.worker.stop()


# FastAPI >= 0.110 accepts lifespan=; older builds need the legacy startup hook.
# This fallback keeps the app runnable on both without degrading the deploy path.
try:
    app = FastAPI(title="Kaggle Cloud Manager", version=config.APP_VERSION, lifespan=lifespan)
except TypeError:
    app = FastAPI(title="Kaggle Cloud Manager", version=config.APP_VERSION)
    _lifespan_cm = lifespan(app)

    @app.on_event("startup")
    async def _startup():  # legacy hook
        await _lifespan_cm.__aenter__()

    @app.on_event("shutdown")
    async def _shutdown():  # legacy hook
        await _lifespan_cm.__aexit__(None, None, None)


# ------------------------------------------------------------------ helpers

def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "•" * 6 + value[-3:]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_public(job: dict) -> dict:
    try:
        meta = json.loads(job.get("meta") or "{}")
    except ValueError:
        meta = {}
    return {
        "id": job["id"],
        "notebook_id": job.get("notebook_id"),
        "notebook_title": meta.get("notebook_title"),
        "kind": job["kind"],
        "status": job["status"],
        "kaggle_status": job["kaggle_status"],
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "meta": meta,
    }


# ------------------------------------------------------------------ health / auth

@app.get("/healthz")
def healthz():
    return {"ok": True, "version": config.APP_VERSION}


@app.post("/api/login")
def login(body: dict, response: Response):
    password = str(body.get("password", ""))
    stored = db.get_setting("admin_password_hash", "")
    if not stored or not security.verify_password(password, stored):
        import time
        time.sleep(0.5)
        raise HTTPException(status_code=401, detail="invalid password")
    raw, hashed = security.new_session_token()
    db.execute(
        "INSERT INTO sessions(token_hash, created_at) VALUES(%s,%s)",
        (hashed, _now()),
    )
    response.set_cookie(
        config.SESSION_COOKIE, raw, httponly=True, samesite="lax",
        max_age=config.SESSION_MAX_AGE,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    raw = request.cookies.get(config.SESSION_COOKIE)
    if raw:
        db.execute("DELETE FROM sessions WHERE token_hash=%s", (security.hash_token(raw),))
    response.delete_cookie(config.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"authenticated": _session_ok(request)}


# ------------------------------------------------------------------ settings

@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_settings():
    return {
        "data_dir": str(config.DATA_DIR),
        "version": config.APP_VERSION,
        "poll_seconds": config.POLL_SECONDS,
    }


@app.post("/api/settings/password", dependencies=[Depends(require_auth)])
def change_password(body: dict):
    current = str(body.get("current_password", ""))
    new = str(body.get("new_password", ""))
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="new password must be at least 8 characters")
    stored = db.get_setting("admin_password_hash", "")
    if not stored or not security.verify_password(current, stored):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    db.set_setting("admin_password_hash", security.hash_password(new))
    return {"ok": True}


# ------------------------------------------------------------------ overview

@app.get("/api/overview", dependencies=[Depends(require_auth)])
def overview():
    rows = db.fetch_all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    done = counts.get("success", 0) + counts.get("failed", 0)
    active = db.fetch_one(
        "SELECT * FROM jobs WHERE status IN ('queued','running','uncertain') ORDER BY id LIMIT 1"
    )
    recent = db.fetch_all("SELECT * FROM jobs ORDER BY id DESC LIMIT 10")
    series_rows = db.fetch_all(
        "SELECT substr(created_at,1,10) AS day, COUNT(*) AS n FROM jobs "
        "WHERE created_at >= now() - interval '13 days' GROUP BY day ORDER BY day"
    )
    nb_count = db.fetch_one("SELECT COUNT(*) AS n FROM notebooks")
    at = "active" if active and active["status"] == "running" else ("queued" if active and active["status"] == "queued" else "idle")
    return {
        "counts": counts,
        "total_jobs": total,
        "success_rate": round(100 * counts.get("success", 0) / done) if done else None,
        "notebooks": nb_count["n"],
        "active_job": _job_public(active) if active else None,
        "engine_state": at,
        "recent_jobs": [_job_public(j) for j in recent],
        "series": series_rows,
        "version": config.APP_VERSION,
        "uptime_s": None,
    }


# ------------------------------------------------------------------ notebooks

@app.get("/api/notebooks", dependencies=[Depends(require_auth)])
def list_notebooks():
    rows = db.fetch_all(
        "SELECT n.*, "
        " (SELECT status FROM jobs j WHERE j.notebook_id=n.id ORDER BY j.id DESC LIMIT 1) AS last_job_status, "
        " (SELECT MAX(id) FROM jobs j WHERE j.notebook_id=n.id) AS last_job_id "
        "FROM notebooks n ORDER BY n.updated_at DESC"
    )
    return rows


@app.post("/api/notebooks", dependencies=[Depends(require_auth)])
def create_notebook(body: dict):
    title = str(body.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    code = str(body.get("code", "")).strip() or "print('Hello from Kaggle Cloud Manager')"
    cells = [{
        "cell_type": "code", "execution_count": None, "metadata": {},
        "outputs": [], "source": code.splitlines(keepends=True),
    }]
    source = json.dumps({
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                    "name": "python3"}},
        "nbformat": 4, "nbformat_minor": 5,
    })
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "notebook"
    if db.fetch_one("SELECT 1 FROM notebooks WHERE slug=%s", (slug,)):
        slug = f"{slug}-{datetime.now().strftime('%H%M%S')}"
    now = _now()
    account_id = body.get("account_id")
    if account_id is not None and config.get_account(account_id) is None:
        account_id = None
    accelerator = str(body.get("accelerator", "auto")).strip().lower() or "auto"
    if accelerator not in kaggle_client.ACCELERATORS:
        raise HTTPException(status_code=400, detail=f"unknown accelerator '{accelerator}'")
    internet = 1 if body.get("internet", True) else 0
    nid = db.execute(
        "INSERT INTO notebooks(slug, title, description, source, account_id, accelerator, internet, created_at, updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (slug, title, str(body.get("description", "")), source, account_id, accelerator, internet, now, now),
    )
    return {"id": nid, "slug": slug}


@app.get("/api/notebooks/{nid}", dependencies=[Depends(require_auth)])
def get_notebook(nid: int):
    row = db.fetch_one("SELECT * FROM notebooks WHERE id=%s", (nid,))
    if not row:
        raise HTTPException(status_code=404, detail="notebook not found")
    return row


@app.put("/api/notebooks/{nid}", dependencies=[Depends(require_auth)])
def update_notebook(nid: int, body: dict):
    if not db.fetch_one("SELECT 1 FROM notebooks WHERE id=%s", (nid,)):
        raise HTTPException(status_code=404, detail="notebook not found")
    updates, values = [], []
    if "title" in body:
        updates.append("title=%s")
        values.append(str(body["title"]).strip())
    if "description" in body:
        updates.append("description=%s")
        values.append(str(body["description"]))
    if "schedule" in body:
        cron = str(body["schedule"]).strip()
        err = scheduler.cron_is_valid(cron)
        if err:
            raise HTTPException(status_code=400, detail=err)
        updates.append("schedule=%s")
        values.append(cron)
    if "account_id" in body:
        account_id = body["account_id"]
        if account_id is None:
            updates.append("account_id=%s")
            values.append(None)
        elif config.get_account(account_id) is not None:
            updates.append("account_id=%s")
            values.append(int(account_id))
        else:
            raise HTTPException(status_code=400, detail="unknown Kaggle account")
    if "accelerator" in body:
        accelerator = str(body["accelerator"]).strip().lower()
        if accelerator not in kaggle_client.ACCELERATORS:
            raise HTTPException(status_code=400, detail=f"unknown accelerator '{accelerator}'")
        updates.append("accelerator=%s")
        values.append(accelerator)
    if "internet" in body:
        updates.append("internet=%s")
        values.append(1 if body["internet"] else 0)
    if "source" in body:
        try:
            json.loads(str(body["source"]))
        except ValueError:
            raise HTTPException(status_code=400, detail="source must be valid notebook JSON")
        updates.append("source=%s")
        values.append(str(body["source"]))
    if not updates:
        return {"ok": True}
    updates.append("updated_at=%s")
    values.append(_now())
    values.append(nid)
    db.execute(f"UPDATE notebooks SET {', '.join(updates)} WHERE id=%s", values)
    return {"ok": True}


@app.delete("/api/notebooks/{nid}", dependencies=[Depends(require_auth)])
def delete_notebook(nid: int):
    active = db.fetch_one(
        "SELECT 1 FROM jobs WHERE notebook_id=%s AND status IN ('queued','running','uncertain') LIMIT 1",
        (nid,),
    )
    if active:
        raise HTTPException(status_code=409, detail="notebook has active/queued jobs")
    db.execute("DELETE FROM notebooks WHERE id=%s", (nid,))
    return {"ok": True}


@app.post("/api/notebooks/{nid}/run", dependencies=[Depends(require_auth)])
def run_notebook(nid: int, body: dict):
    nb = db.fetch_one("SELECT * FROM notebooks WHERE id=%s", (nid,))
    if not nb:
        raise HTTPException(status_code=404, detail="notebook not found")
    jid = db.enqueue_job(nid, kind="manual")
    title = nb["title"]
    slug = nb["slug"]
    db.execute(
        "UPDATE jobs SET meta=%s WHERE id=%s",
        (json.dumps({"notebook_title": title, "notebook_slug": slug,
                     "comment": str(body.get("comment", ""))}), jid),
    )
    return {"job_id": jid, "status": "queued"}


# ------------------------------------------------------------------ jobs

@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def list_jobs(status: str | None = None, limit: int = 50):
    limit = max(1, min(limit, 200))
    if status:
        rows = db.fetch_all(
            "SELECT * FROM jobs WHERE status=%s ORDER BY id DESC LIMIT %s", (status, limit)
        )
    else:
        rows = db.fetch_all("SELECT * FROM jobs ORDER BY id DESC LIMIT %s", (limit,))
    return [_job_public(j) for j in rows]


@app.get("/api/jobs/{jid}", dependencies=[Depends(require_auth)])
def get_job(jid: int):
    row = db.fetch_one("SELECT * FROM jobs WHERE id=%s", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    job = _job_public(row)
    job["logs"] = db.fetch_all(
        "SELECT ts, line FROM job_logs WHERE job_id=%s ORDER BY id", (jid,)
    )
    return job


@app.get("/api/jobs/{jid}/logs", dependencies=[Depends(require_auth)])
def get_job_logs(jid: int):
    rows = db.fetch_all(
        "SELECT ts, line FROM job_logs WHERE job_id=%s ORDER BY id DESC LIMIT 500",
        (jid,),
    )[::-1]
    return {"logs": rows}


@app.post("/api/jobs/{jid}/cancel", dependencies=[Depends(require_auth)])
async def cancel_job(jid: int):
    job = db.fetch_one("SELECT * FROM jobs WHERE id=%s", (jid,))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in (config.JOB_QUEUED, config.JOB_UNCERTAIN):
        db.execute(
            "UPDATE jobs SET status=%s, finished_at=%s, error='cancelled by user' WHERE id=%s",
            (config.JOB_CANCELLED, _now(), jid),
        )
        db.add_job_log(jid, "job cancelled by user")
        return {"ok": True, "message": "job cancelled"}
    if job["status"] in (config.JOB_RUNNING, config.JOB_STOPPING):
        import asyncio
        try:
            message = await asyncio.to_thread(
                worker_mod.stop_job, app.state.facade, jid
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "message": message}
    raise HTTPException(status_code=409, detail=f"job is {job['status']}; cannot cancel")


@app.post("/api/jobs/{jid}/requeue", dependencies=[Depends(require_auth)])
def requeue_job(jid: int):
    job = db.fetch_one("SELECT * FROM jobs WHERE id=%s", (jid,))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in config.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"job is {job['status']}")
    db.execute(
        "UPDATE jobs SET status=%s, error='', finished_at=NULL WHERE id=%s",
        (config.JOB_QUEUED, jid),
    )
    db.add_job_log(jid, "job requeued by user")
    return {"ok": True}


@app.get("/api/jobs/{jid}/output", dependencies=[Depends(require_auth)])
def job_output(jid: int):
    job = db.fetch_one("SELECT * FROM jobs WHERE id=%s", (jid,))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        meta = json.loads(job["meta"] or "{}")
    except ValueError:
        meta = {}
    files = meta.get("files", [])
    output_dir = Path(meta.get("output_dir", ""))
    return {
        "files": files,
        "output_dir": str(output_dir) if output_dir.exists() else None,
    }


@app.get("/api/jobs/{jid}/output/{filename}", dependencies=[Depends(require_auth)])
def job_output_file(jid: int, filename: str):
    job = db.fetch_one("SELECT * FROM jobs WHERE id=%s", (jid,))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        meta = json.loads(job["meta"] or "{}")
    except ValueError:
        meta = {}
    output_dir = Path(meta.get("output_dir", ""))
    safe = Path(filename).name
    path = output_dir / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, filename=safe)


@app.get("/api/logs", dependencies=[Depends(require_auth)])
def global_logs(limit: int = 200):
    limit = max(1, min(limit, 500))
    rows = db.fetch_all(
        "SELECT l.job_id, l.ts, l.line FROM job_logs l "
        "ORDER BY l.id DESC LIMIT %s",
        (limit,),
    )[::-1]
    return {"logs": rows}


# ------------------------------------------------------------------ Kaggle accounts

@app.get("/api/accounts", dependencies=[Depends(require_auth)])
def list_accounts():
    """Accounts come only from environment variables (KAGGLE_ACCOUNTS or
    KAGGLE_USERNAME/KAGGLE_KEY). Read-only — no dashboard credential entry."""
    return config.accounts()


# ------------------------------------------------------------------ backup

@app.get("/api/export", dependencies=[Depends(require_auth)])
def export():
    data = {
        "app": "kaggle-cloud-manager",
        "version": config.APP_VERSION,
        "exported_at": _now(),
        "notebooks": db.fetch_all("SELECT id, slug, title, description, source, schedule, enabled, created_at, updated_at, last_run_at FROM notebooks"),
        "jobs": db.fetch_all("SELECT id, notebook_id, kind, status, kaggle_status, error, created_at, started_at, finished_at, meta FROM jobs"),
        "job_logs": db.fetch_all("SELECT id, job_id, ts, line FROM job_logs"),
        "settings": [{"key": r["key"], "value": "***" if "key" in r["key"] else r["value"]}
                     for r in db.fetch_all("SELECT key, value FROM settings")],
    }
    import urllib.parse
    filename = f"kaggle_manager_backup_{_now().replace(':','-')}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{urllib.parse.quote(filename)}"'},
    )


# ------------------------------------------------------------------ static

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
