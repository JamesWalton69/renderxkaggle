"""Background worker: persistent FIFO queue, one active execution at a time,
restart recovery, and cron-based scheduling.

Design
------
- Serialization comes from the database, not from memory: the worker only
  starts a new job when no other job is in `running`. If the process dies,
  startup reconciliation flips stray `running` jobs to `interrupted`, and the
  owner can requeue them (dashboard or MCP). `uncertain` jobs (transient
  network failures while polling Kaggle) auto-retry after a cooldown.
- Every mutation is logged to `job_logs`.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import config, db, scheduler
from .kaggle_client import ApiError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_meta(job: dict) -> dict:
    try:
        meta = json.loads(job.get("meta") or "{}")
        return meta if isinstance(meta, dict) else {}
    except ValueError:
        return {}


def _save_job_meta(job_id: int, meta: dict, db_path=None) -> None:
    db.execute("UPDATE jobs SET meta=? WHERE id=?", (json.dumps(meta), job_id), db_path=db_path)


class Worker:
    def __init__(self, facade, db_path=None, poll_seconds: Optional[float] = None):
        self.facade = facade
        self.db_path = db_path
        self.poll_seconds = poll_seconds or config.POLL_SECONDS
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        await asyncio.to_thread(self.reconcile)
        self._task = asyncio.create_task(self._loop(), name="kaggle-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                await asyncio.to_thread(self.tick)
            except Exception as exc:  # never let a tick kill the loop
                print(f"[worker] tick error: {exc}")

    def reconcile(self) -> None:
        """After a (re)start: interrupt jobs that were mid-flight."""
        rows = db.fetch_all(
            "SELECT id FROM jobs WHERE status IN (?,?)",
            (config.JOB_RUNNING, config.JOB_STOPPING),
            db_path=self.db_path,
        )
        for row in rows:
            db.execute(
                "UPDATE jobs SET status=?, error='interrupted by server restart' WHERE id=?",
                (config.JOB_INTERRUPTED, row["id"]),
                db_path=self.db_path,
            )
            db.add_job_log(row["id"], "server restarted — execution interrupted; requeue to retry", db_path=self.db_path)

    # ------------------------------------------------------------- tick

    def tick(self) -> None:
        self._maybe_start_next()
        self._poll_active()
        self._fire_schedules()

    # ------------------------------------------------------------- queue

    def _maybe_start_next(self) -> None:
        active = db.fetch_one(
            "SELECT id FROM jobs WHERE status IN (?,?) LIMIT 1",
            (config.JOB_RUNNING, config.JOB_STOPPING),
            db_path=self.db_path,
        )
        if active:
            return

        # 1) normal queue (FIFO)
        next_job = db.fetch_one(
            "SELECT * FROM jobs WHERE status=? ORDER BY id LIMIT 1",
            (config.JOB_QUEUED,),
            db_path=self.db_path,
        )
        if next_job:
            self._start_execution(next_job)
            return

        # 2) uncertain jobs whose retry cooldown elapsed
        now = _now()
        stale = db.fetch_all(
            "SELECT * FROM jobs WHERE status=?",
            (config.JOB_UNCERTAIN,),
            db_path=self.db_path,
        )
        for job in stale:
            meta = _job_meta(job)
            if meta.get("retry_at", "") <= now:
                db.execute(
                    "UPDATE jobs SET status=?, error='' WHERE id=?",
                    (config.JOB_RUNNING, job["id"]),
                    db_path=self.db_path,
                )
                db.add_job_log(job["id"], "retrying uncertain execution", db_path=self.db_path)
                self._start_execution(job)
                return

    def _start_execution(self, job: dict) -> None:
        nb = db.fetch_one(
            "SELECT * FROM notebooks WHERE id=?", (job.get("notebook_id"),), db_path=self.db_path
        )
        meta = _job_meta(job)
        meta.setdefault("notebook_title", nb["title"] if nb else "deleted notebook")
        meta.setdefault("notebook_slug", nb["slug"] if nb else "unknown")
        if nb and nb.get("account_id"):
            meta["account_id"] = nb["account_id"]
        ref = meta.get("ref", "")
        if ref:
            db.execute(
                "UPDATE jobs SET status=?, started_at=COALESCE(started_at,?), kaggle_status='queued' WHERE id=?",
                (config.JOB_RUNNING, _now(), job["id"]),
                db_path=self.db_path,
            )
            db.add_job_log(job["id"], f"resuming execution of {ref}", db_path=self.db_path)
            return

        account = config.get_account(meta.get("account_id"))
        try:
            if nb is None:
                raise ApiError("its notebook was deleted; job cannot run")
            ref = self.facade.ensure_notebook(nb, account=account)
            self.facade.run_notebook(nb, account=account)
        except ApiError as exc:
            db.execute(
                "UPDATE jobs SET status=?, kaggle_status='error', error=?, finished_at=? WHERE id=?",
                (config.JOB_FAILED, exc.message[:1000], _now(), job["id"]),
                db_path=self.db_path,
            )
            db.add_job_log(job["id"], f"failed to start: {exc.message}", db_path=self.db_path)
            return
        except Exception as exc:
            db.execute(
                "UPDATE jobs SET status=?, kaggle_status='error', error=?, finished_at=? WHERE id=?",
                (config.JOB_FAILED, f"unexpected: {exc}"[:1000], _now(), job["id"]),
                db_path=self.db_path,
            )
            db.add_job_log(job["id"], f"failed to start (unexpected): {exc}", db_path=self.db_path)
            return

        meta["ref"] = ref
        _save_job_meta(job["id"], meta, db_path=self.db_path)
        db.execute(
            "UPDATE jobs SET status=?, started_at=COALESCE(started_at,?), kaggle_status='queued' WHERE id=?",
            (config.JOB_RUNNING, _now(), job["id"]),
            db_path=self.db_path,
        )
        db.execute(
            "UPDATE notebooks SET last_run_at=? WHERE id=?",
            (_now(), job.get("notebook_id")),
            db_path=self.db_path,
        )
        db.add_job_log(job["id"], f"submitted to Kaggle: {ref} (job queued remotely)", db_path=self.db_path)

    # ------------------------------------------------------------- polling

    def _poll_active(self) -> None:
        running = db.fetch_all(
            "SELECT * FROM jobs WHERE status IN (?,?)",
            (config.JOB_RUNNING, config.JOB_STOPPING),
            db_path=self.db_path,
        )
        for job in running:
            self._poll_job(job)

    def _poll_job(self, job: dict) -> None:
        meta = _job_meta(job)
        ref = meta.get("ref", "")
        job_id = job["id"]
        if not ref:
            db.execute(
                "UPDATE jobs SET status=?, finished_at=?, error='missing remote ref' WHERE id=?",
                (config.JOB_INTERRUPTED, _now(), job_id),
                db_path=self.db_path,
            )
            db.add_job_log(job_id, "no remote reference stored; job interrupted", db_path=self.db_path)
            return

        try:
            status = self.facade.get_status(ref, account=config.get_account(meta.get("account_id")))
        except ApiError as exc:
            self._poll_failure(job, f"status error: {exc.message}")
            return
        except Exception as exc:
            self._poll_failure(job, f"status error: {exc}")
            return

        meta["failures"] = 0
        ks = status.get("status", "")
        last = meta.get("last_kaggle_status", "")
        if ks and ks != last:
            msg = f"kaggle status → {ks}: {status.get('message','')[:300]}"
            db.add_job_log(job_id, msg, db_path=self.db_path)
        meta["last_kaggle_status"] = ks
        _save_job_meta(job_id, meta, db_path=self.db_path)
        db.execute(
            "UPDATE jobs SET kaggle_status=? WHERE id=?",
            (ks, job_id),
            db_path=self.db_path,
        )

        if ks == "complete":
            self._finalize_success(job, ref)
        elif ks == "error":
            failure = status.get("failure_message") or status.get("message") or "Kaggle reported an error"
            if job["status"] == config.JOB_STOPPING:
                db.execute(
                    "UPDATE jobs SET status=?, kaggle_status='error', error=?, finished_at=? WHERE id=?",
                    (config.JOB_CANCELLED, "stopped by user (kernel ended with error after stop)", _now(), job_id),
                    db_path=self.db_path,
                )
                db.add_job_log(job_id, f"stop completed (kernel errored after stop): {failure[:300]}", db_path=self.db_path)
            else:
                db.execute(
                    "UPDATE jobs SET status=?, kaggle_status='error', error=?, finished_at=? WHERE id=?",
                    (config.JOB_FAILED, failure[:1000], _now(), job_id),
                    db_path=self.db_path,
                )
                db.add_job_log(job_id, f"failed: {failure[:500]}", db_path=self.db_path)

    def _poll_failure(self, job: dict, message: str) -> None:
        job_id = job["id"]
        meta = _job_meta(job)
        meta["failures"] = meta.get("failures", 0) + 1
        if job["status"] == config.JOB_STOPPING:
            # keep waiting for the terminal state; never demote to uncertain
            # (the retry path would re-push the notebook, undoing the stop)
            _save_job_meta(job_id, meta, db_path=self.db_path)
            db.add_job_log(job_id, f"stop poll warning ({meta['failures']}): {message}", db_path=self.db_path)
            return
        if meta["failures"] >= config.WORKER_MAX_POLL_FAILURES:
            meta["retry_at"] = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(timespec="seconds")
            _save_job_meta(job_id, meta, db_path=self.db_path)
            db.execute(
                "UPDATE jobs SET status=?, error=? WHERE id=?",
                (config.JOB_UNCERTAIN, message[:1000], job_id),
                db_path=self.db_path,
            )
            db.add_job_log(job_id, f"polling failed repeatedly; job marked uncertain — {message}", db_path=self.db_path)
        else:
            _save_job_meta(job_id, meta, db_path=self.db_path)
            db.add_job_log(job_id, f"poll warning ({meta['failures']}/{config.WORKER_MAX_POLL_FAILURES}): {message}", db_path=self.db_path)

    def _finalize_success(self, job: dict, ref: str) -> None:
        job_id = job["id"]
        dest = config.OUTPUTS_DIR / f"job-{job_id}"
        try:
            meta0 = _job_meta(job)
            files = self.facade.download_output(
                ref, dest,
                account=config.get_account(meta0.get("account_id")),
            )
        except Exception as exc:
            db.execute(
                "UPDATE jobs SET status=?, kaggle_status='complete', error=?, finished_at=? WHERE id=?",
                (config.JOB_INTERRUPTED, f"output download failed: {exc}"[:1000], _now(), job_id),
                db_path=self.db_path,
            )
            db.add_job_log(job_id, f"execution completed but output download failed: {exc}", db_path=self.db_path)
            return
        meta = _job_meta(job)
        meta["files"] = [str(Path(f).name) for f in files]
        meta["output_dir"] = str(dest)
        _save_job_meta(job_id, meta, db_path=self.db_path)
        if job["status"] == config.JOB_STOPPING:
            db.execute(
                "UPDATE jobs SET status=?, kaggle_status='complete', error='stopped by user', finished_at=? WHERE id=?",
                (config.JOB_CANCELLED, _now(), job_id),
                db_path=self.db_path,
            )
            db.add_job_log(job_id, "execution stopped by user — pre-stop outputs preserved", db_path=self.db_path)
            return
        db.execute(
            "UPDATE jobs SET status=?, kaggle_status='complete', error='', finished_at=? WHERE id=?",
            (config.JOB_SUCCESS, _now(), job_id),
            db_path=self.db_path,
        )
        db.add_job_log(job_id, "execution complete — output saved", db_path=self.db_path)
        for f in meta["files"]:
            db.add_job_log(job_id, f"  output: {f}", db_path=self.db_path)

    # ------------------------------------------------------------- scheduler

    def _fire_schedules(self) -> None:
        now = datetime.now(timezone.utc)
        rows = db.fetch_all(
            "SELECT * FROM notebooks WHERE enabled=1 AND schedule != ''",
            db_path=self.db_path,
        )
        for nb in rows:
            try:
                if not scheduler.cron_matches(nb["schedule"], now):
                    continue
            except ValueError:
                continue
            active = db.fetch_one(
                "SELECT 1 FROM jobs WHERE notebook_id=? AND status IN ('queued','running','uncertain') LIMIT 1",
                (nb["id"],),
                db_path=self.db_path,
            )
            if active:
                continue
            jid = db.enqueue_job(nb["id"], kind="schedule", db_path=self.db_path)
            db.execute(
                "UPDATE jobs SET meta=? WHERE id=?",
                (json.dumps({"notebook_title": nb["title"], "notebook_slug": nb["slug"]}), jid),
                db_path=self.db_path,
            )
            db.add_job_log(jid, f"scheduled run fired ({nb['schedule']})", db_path=self.db_path)


# ---------------------------------------------------------------- stop (shared)

def stop_job(facade, job_id: int, db_path=None) -> str:
    """Stop a running job: snapshot outputs, then push an immediate-exit stub.

    Used by the API cancel endpoint and the agent tool. The worker keeps
    polling and finalizes the job as cancelled once Kaggle reaches a terminal
    state. Returns a human-readable outcome.
    """
    job = db.fetch_one("SELECT * FROM jobs WHERE id=?", (int(job_id),), db_path=db_path)
    if not job:
        raise ValueError("job not found")
    if job["status"] not in (config.JOB_RUNNING, config.JOB_STOPPING):
        raise ValueError(f"job is {job['status']} — only running jobs can be stopped")
    if job["status"] == config.JOB_STOPPING:
        return f"job {job_id} is already stopping"
    meta = _job_meta(job)
    ref = meta.get("ref", "")
    if not ref:
        raise ValueError("job has no remote reference yet; wait for it to start")
    nb = db.fetch_one(
        "SELECT * FROM notebooks WHERE id=?", (job.get("notebook_id"),), db_path=db_path
    )
    if nb is None:
        nb = {
            "slug": meta.get("notebook_slug", "notebook"),
            "title": meta.get("notebook_title", "Notebook"),
            "source": "{}", "accelerator": "auto", "internet": 1,
        }
    account = config.get_account(meta.get("account_id"))
    dest = config.OUTPUTS_DIR / f"job-{job_id}"
    try:
        facade.download_output(ref, dest, account=account)  # pre-stop snapshot
        db.add_job_log(job_id, "pre-stop output snapshot saved", db_path=db_path)
    except Exception as exc:
        db.add_job_log(job_id, f"pre-stop snapshot unavailable: {exc}", db_path=db_path)
    try:
        facade.stop_notebook(nb, account=account)
    except Exception as exc:
        raise ValueError(f"stop signal failed: {exc}")
    db.execute(
        "UPDATE jobs SET status=?, kaggle_status='stopping', error='stopping…' WHERE id=?",
        (config.JOB_STOPPING, job_id),
        db_path=db_path,
    )
    db.add_job_log(job_id, "stop signal sent — waiting for Kaggle to finalize", db_path=db_path)
    return f"stop signal sent for job {job_id}"
