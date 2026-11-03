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


def _save_job_meta(job_id: int, meta: dict) -> None:
    db.execute("UPDATE jobs SET meta=%s WHERE id=%s", (json.dumps(meta), job_id))


class Worker:
    def __init__(self, facade, poll_seconds: Optional[float] = None):
        self.facade = facade
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
            "SELECT id FROM jobs WHERE status IN (%s,%s)",
            (config.JOB_RUNNING, config.JOB_STOPPING),
            
        )
        for row in rows:
            db.execute(
                "UPDATE jobs SET status=%s, error='interrupted by server restart' WHERE id=%s",
                (config.JOB_INTERRUPTED, row["id"]),
                
            )
            db.add_job_log(row["id"], "server restarted — execution interrupted; requeue to retry",)

    # ------------------------------------------------------------- tick

    def tick(self) -> None:
        self._maybe_start_next()
        self._poll_active()
        self._fire_schedules()

    # ------------------------------------------------------------- queue

    def _maybe_start_next(self) -> None:
        active = db.fetch_one(
            "SELECT id FROM jobs WHERE status IN (%s,%s) LIMIT 1",
            (config.JOB_RUNNING, config.JOB_STOPPING),
            
        )
        if active:
            return

        # 1) normal queue (FIFO)
        next_job = db.fetch_one(
            "SELECT * FROM jobs WHERE status=%s ORDER BY id LIMIT 1",
            (config.JOB_QUEUED,),
            
        )
        if next_job:
            self._start_execution(next_job)
            return

        # 2) uncertain jobs whose retry cooldown elapsed
        now = _now()
        stale = db.fetch_all(
            "SELECT * FROM jobs WHERE status=%s",
            (config.JOB_UNCERTAIN,),
            
        )
        for job in stale:
            meta = _job_meta(job)
            if meta.get("retry_at", "") <= now:
                db.execute(
                    "UPDATE jobs SET status=%s, error='' WHERE id=%s",
                    (config.JOB_RUNNING, job["id"]),
                    
                )
                db.add_job_log(job["id"], "retrying uncertain execution",)
                self._start_execution(job)
                return

    def _start_execution(self, job: dict) -> None:
        nb = db.fetch_one(
            "SELECT * FROM notebooks WHERE id=%s", (job.get("notebook_id"),)
        )
        meta = _job_meta(job)
        meta.setdefault("notebook_title", nb["title"] if nb else "deleted notebook")
        meta.setdefault("notebook_slug", nb["slug"] if nb else "unknown")
        if nb and nb.get("account_id"):
            meta["account_id"] = nb["account_id"]
        ref = meta.get("ref", "")
        if ref:
            db.execute(
                "UPDATE jobs SET status=%s, started_at=COALESCE(started_at,%s), kaggle_status='queued' WHERE id=%s",
                (config.JOB_RUNNING, _now(), job["id"]),
                
            )
            db.add_job_log(job["id"], f"resuming execution of {ref}",)
            return

        account = db.get_account(meta.get("account_id"))
        try:
            if nb is None:
                raise ApiError("its notebook was deleted; job cannot run")
            ref = self.facade.ensure_notebook(nb, account=account)
            self.facade.run_notebook(nb, account=account)
        except ApiError as exc:
            db.execute(
                "UPDATE jobs SET status=%s, kaggle_status='error', error=%s, finished_at=%s WHERE id=%s",
                (config.JOB_FAILED, exc.message[:1000], _now(), job["id"]),
                
            )
            db.add_job_log(job["id"], f"failed to start: {exc.message}",)
            return
        except Exception as exc:
            db.execute(
                "UPDATE jobs SET status=%s, kaggle_status='error', error=%s, finished_at=%s WHERE id=%s",
                (config.JOB_FAILED, f"unexpected: {exc}"[:1000], _now(), job["id"]),
                
            )
            db.add_job_log(job["id"], f"failed to start (unexpected): {exc}",)
            return

        meta["ref"] = ref
        _save_job_meta(job["id"], meta,)
        db.execute(
            "UPDATE jobs SET status=%s, started_at=COALESCE(started_at,%s), kaggle_status='queued' WHERE id=%s",
            (config.JOB_RUNNING, _now(), job["id"]),
            
        )
        db.execute(
            "UPDATE notebooks SET last_run_at=%s WHERE id=%s",
            (_now(), job.get("notebook_id")),
            
        )
        db.add_job_log(job["id"], f"submitted to Kaggle: {ref} (job queued remotely)",)

    # ------------------------------------------------------------- polling

    def _poll_active(self) -> None:
        running = db.fetch_all(
            "SELECT * FROM jobs WHERE status IN (%s,%s)",
            (config.JOB_RUNNING, config.JOB_STOPPING),
            
        )
        for job in running:
            self._poll_job(job)

    def _poll_job(self, job: dict) -> None:
        meta = _job_meta(job)
        ref = meta.get("ref", "")
        job_id = job["id"]
        if not ref:
            db.execute(
                "UPDATE jobs SET status=%s, finished_at=%s, error='missing remote ref' WHERE id=%s",
                (config.JOB_INTERRUPTED, _now(), job_id),
                
            )
            db.add_job_log(job_id, "no remote reference stored; job interrupted",)
            return

        try:
            status = self.facade.get_status(ref, account=db.get_account(meta.get("account_id")))
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
            db.add_job_log(job_id, msg,)
        meta["last_kaggle_status"] = ks
        _save_job_meta(job_id, meta,)
        db.execute(
            "UPDATE jobs SET kaggle_status=%s WHERE id=%s",
            (ks, job_id),
            
        )

        if ks == "complete":
            self._finalize_success(job, ref)
        elif ks == "error":
            failure = status.get("failure_message") or status.get("message") or "Kaggle reported an error"
            if job["status"] == config.JOB_STOPPING:
                db.execute(
                    "UPDATE jobs SET status=%s, kaggle_status='error', error=%s, finished_at=%s WHERE id=%s",
                    (config.JOB_CANCELLED, "stopped by user (kernel ended with error after stop)", _now(), job_id),
                    
                )
                db.add_job_log(job_id, f"stop completed (kernel errored after stop): {failure[:300]}",)
            else:
                db.execute(
                    "UPDATE jobs SET status=%s, kaggle_status='error', error=%s, finished_at=%s WHERE id=%s",
                    (config.JOB_FAILED, failure[:1000], _now(), job_id),
                    
                )
                db.add_job_log(job_id, f"failed: {failure[:500]}",)

    def _poll_failure(self, job: dict, message: str) -> None:
        job_id = job["id"]
        meta = _job_meta(job)
        meta["failures"] = meta.get("failures", 0) + 1
        if job["status"] == config.JOB_STOPPING:
            # keep waiting for the terminal state; never demote to uncertain
            # (the retry path would re-push the notebook, undoing the stop)
            _save_job_meta(job_id, meta,)
            db.add_job_log(job_id, f"stop poll warning ({meta['failures']}): {message}",)
            return
        if meta["failures"] >= config.WORKER_MAX_POLL_FAILURES:
            meta["retry_at"] = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(timespec="seconds")
            _save_job_meta(job_id, meta,)
            db.execute(
                "UPDATE jobs SET status=%s, error=%s WHERE id=%s",
                (config.JOB_UNCERTAIN, message[:1000], job_id),
                
            )
            db.add_job_log(job_id, f"polling failed repeatedly; job marked uncertain — {message}",)
        else:
            _save_job_meta(job_id, meta,)
            db.add_job_log(job_id, f"poll warning ({meta['failures']}/{config.WORKER_MAX_POLL_FAILURES}): {message}",)

    def _finalize_success(self, job: dict, ref: str) -> None:
        job_id = job["id"]
        dest = config.OUTPUTS_DIR / f"job-{job_id}"
        try:
            meta0 = _job_meta(job)
            files = self.facade.download_output(
                ref, dest,
                account=db.get_account(meta0.get("account_id")),
            )
        except Exception as exc:
            db.execute(
                "UPDATE jobs SET status=%s, kaggle_status='complete', error=%s, finished_at=%s WHERE id=%s",
                (config.JOB_INTERRUPTED, f"output download failed: {exc}"[:1000], _now(), job_id),
                
            )
            db.add_job_log(job_id, f"execution completed but output download failed: {exc}",)
            return
        meta = _job_meta(job)
        meta["files"] = [str(Path(f).name) for f in files]
        meta["output_dir"] = str(dest)
        _save_job_meta(job_id, meta,)
        if job["status"] == config.JOB_STOPPING:
            db.execute(
                "UPDATE jobs SET status=%s, kaggle_status='complete', error='stopped by user', finished_at=%s WHERE id=%s",
                (config.JOB_CANCELLED, _now(), job_id),
                
            )
            db.add_job_log(job_id, "execution stopped by user — pre-stop outputs preserved",)
            return
        db.execute(
            "UPDATE jobs SET status=%s, kaggle_status='complete', error='', finished_at=%s WHERE id=%s",
            (config.JOB_SUCCESS, _now(), job_id),
            
        )
        db.add_job_log(job_id, "execution complete — output saved",)
        for f in meta["files"]:
            db.add_job_log(job_id, f"  output: {f}",)

    # ------------------------------------------------------------- scheduler

    def _fire_schedules(self) -> None:
        now = datetime.now(timezone.utc)
        rows = db.fetch_all(
            "SELECT * FROM notebooks WHERE enabled=1 AND schedule != ''",
            
        )
        for nb in rows:
            try:
                if not scheduler.cron_matches(nb["schedule"], now):
                    continue
            except ValueError:
                continue
            active = db.fetch_one(
                "SELECT 1 FROM jobs WHERE notebook_id=%s AND status IN ('queued','running','uncertain') LIMIT 1",
                (nb["id"],),
                
            )
            if active:
                continue
            jid = db.enqueue_job(nb["id"], kind="schedule",)
            db.execute(
                "UPDATE jobs SET meta=%s WHERE id=%s",
                (json.dumps({"notebook_title": nb["title"], "notebook_slug": nb["slug"]}), jid),
                
            )
            db.add_job_log(jid, f"scheduled run fired ({nb['schedule']})",)


# ---------------------------------------------------------------- stop (shared)

def stop_job(facade, job_id: int) -> str:
    """Stop a running job: snapshot outputs, then push an immediate-exit stub.

    Used by the API cancel endpoint and the agent tool. The worker keeps
    polling and finalizes the job as cancelled once Kaggle reaches a terminal
    state. Returns a human-readable outcome.
    """
    job = db.fetch_one("SELECT * FROM jobs WHERE id=%s", (int(job_id),),)
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
        "SELECT * FROM notebooks WHERE id=%s", (job.get("notebook_id"),)
    )
    if nb is None:
        nb = {
            "slug": meta.get("notebook_slug", "notebook"),
            "title": meta.get("notebook_title", "Notebook"),
            "source": "{}", "accelerator": "auto", "internet": 1,
        }
    account = db.get_account(meta.get("account_id"))
    dest = config.OUTPUTS_DIR / f"job-{job_id}"
    try:
        facade.download_output(ref, dest, account=account)  # pre-stop snapshot
        db.add_job_log(job_id, "pre-stop output snapshot saved",)
    except Exception as exc:
        db.add_job_log(job_id, f"pre-stop snapshot unavailable: {exc}",)
    try:
        facade.stop_notebook(nb, account=account)
    except Exception as exc:
        raise ValueError(f"stop signal failed: {exc}")
    db.execute(
        "UPDATE jobs SET status=%s, kaggle_status='stopping', error='stopping…' WHERE id=%s",
        (config.JOB_STOPPING, job_id),
        
    )
    db.add_job_log(job_id, "stop signal sent — waiting for Kaggle to finalize",)
    return f"stop signal sent for job {job_id}"
