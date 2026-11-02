"""Worker state-machine tests — offline, using a fake Kaggle facade."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import config, db
from app.kaggle_client import ApiError
from app.worker import Worker, _now


class FakeKaggle:
    def __init__(self, status="queued"):
        self.status = {"status": status, "message": "", "failure_message": ""}
        self.run_count = 0
        self.download_count = 0
        self.stop_count = 0
        self.fail_status_calls = 0
        self.last_run_account = None
        self.last_status_account = None
        self.last_stop_account = None

    def ensure_notebook(self, nb, account=None):
        return f"owner/{nb['slug']}"

    def run_notebook(self, nb, account=None):
        self.run_count += 1
        self.last_run_account = account
        return f"owner/{nb['slug']}"

    def stop_notebook(self, nb, account=None):
        self.stop_count += 1
        self.last_stop_account = account
        return f"owner/{nb['slug']}"

    def get_status(self, ref, account=None):
        self.last_status_account = account
        if self.fail_status_calls:
            self.fail_status_calls -= 1
            raise ApiError("temporary network issue", 503)
        return dict(self.status)

    def download_output(self, ref, dest, account=None):
        self.download_count += 1
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "result.txt").write_text("ok")
        return ["result.txt"]


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        self.facade = FakeKaggle()
        self.worker = Worker(self.facade, db_path=self.db_path, poll_seconds=0.1)

    def tearDown(self):
        self.tmp.cleanup()

    def _notebook(self, title="demo", schedule=""):
        now = db.now_iso()
        return db.execute(
            "INSERT INTO notebooks(slug,title,description,source,schedule,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (title, title, "", "{}", schedule, now, now),
            db_path=self.db_path,
        )

    def _enqueue(self, notebook_id, kind="manual"):
        return db.enqueue_job(notebook_id, kind=kind, db_path=self.db_path)

    def test_serial_execution(self):
        nb = self._notebook()
        j1 = self._enqueue(nb)
        j2 = self._enqueue(nb)
        self.worker.tick()
        row1 = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j1,), db_path=self.db_path)
        row2 = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j2,), db_path=self.db_path)
        self.assertEqual(row1["status"], "running")   # first started
        self.assertEqual(row2["status"], "queued")    # second waits — one active at a time
        self.assertEqual(self.facade.run_count, 1)

        # complete the first
        self.facade.status["status"] = "complete"
        self.worker.tick()
        row1 = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j1,), db_path=self.db_path)
        self.assertEqual(row1["status"], "success")
        self.assertEqual(row1["kaggle_status"], "complete")
        self.assertIsNotNone(row1["finished_at"])
        self.assertEqual(self.facade.download_count, 1)

        # second now starts (facade must be back to queued for a fresh job)
        self.facade.status["status"] = "queued"
        self.worker.tick()
        row2 = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j2,), db_path=self.db_path)
        self.assertEqual(row2["status"], "running")

    def test_start_failure_marks_failed(self):
        nb = self._notebook()

        class BoomKaggle(FakeKaggle):
            def run_notebook(self, nb, account=None):
                raise ApiError("invalid slug", 400)

        self.worker.facade = BoomKaggle()
        j = self._enqueue(nb)
        self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "failed")
        self.assertIn("invalid slug", row["error"])

    def test_remote_error_marks_failed(self):
        nb = self._notebook()
        j = self._enqueue(nb)
        self.worker.tick()
        self.facade.status = {"status": "error", "message": "", "failure_message": "kernel died"}
        self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "failed")
        self.assertIn("kernel died", row["error"])

    def test_uncertain_after_repeated_poll_failures(self):
        nb = self._notebook()
        j = self._enqueue(nb)
        self.worker.tick()
        self.facade.fail_status_calls = 999  # keep failing
        for _ in range(config.WORKER_MAX_POLL_FAILURES + 1):
            self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "uncertain")
        # it retries after cooldown and recovers
        self.facade.fail_status_calls = 0
        self.facade.status["status"] = "complete"
        from app.worker import _job_meta
        meta = _job_meta(row)
        meta["retry_at"] = "2000-01-01T00:00:00"
        db.execute("UPDATE jobs SET meta=? WHERE id=?", (__import__("json").dumps(meta), j), db_path=self.db_path)
        self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "success")

    def test_restart_reconcile_interrupts_running(self):
        nb = self._notebook()
        j = self._enqueue(nb)
        self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "running")
        # simulate restart
        self.worker.reconcile()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "interrupted")

    def test_schedule_fires_once_and_deduplicates(self):
        nb = self._notebook(schedule="* * * * *")
        self.worker.tick()
        jobs = db.fetch_all("SELECT * FROM jobs", db_path=self.db_path)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["kind"], "schedule")
        # while that job is pending, no duplicate scheduled job
        self.worker.tick()
        jobs = db.fetch_all("SELECT * FROM jobs", db_path=self.db_path)
        self.assertEqual(len(jobs), 1)

    def test_two_accounts_route_creds(self):
        aid = db.add_account("Account A", "user_a", "key_a", db_path=self.db_path)
        now = db.now_iso()
        nb = db.execute(
            "INSERT INTO notebooks(slug,title,description,source,account_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("accnb", "Acc NB", "", "{}", aid, now, now),
            db_path=self.db_path,
        )
        j = self._enqueue(nb)
        self.worker.tick()
        self.assertEqual(self.facade.last_run_account["username"], "user_a")
        self.assertEqual(self.facade.last_run_account["key"], "key_a")
        # polling uses the same account
        job = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(job["status"], "running")
        meta = __import__("json").loads(job["meta"])
        self.assertEqual(meta["account_id"], aid)
        self.worker.tick()
        self.assertEqual(self.facade.last_status_account["username"], "user_a")

    def test_stop_running_job(self):
        from app.worker import stop_job
        nb = self._notebook()
        j = self._enqueue(nb)
        self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "running")
        # stop it: stub is pushed, job flips to stopping
        msg = stop_job(self.facade, j, db_path=self.db_path)
        self.assertIn("stop signal sent", msg)
        self.assertEqual(self.facade.stop_count, 1)
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "stopping")
        self.assertEqual(self.facade.download_count, 1)  # pre-stop snapshot
        # repeated poll failures must NOT demote a stopping job to uncertain
        self.facade.fail_status_calls = 999
        for _ in range(config.WORKER_MAX_POLL_FAILURES + 1):
            self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "stopping")
        # Kaggle reaches terminal state → job ends cancelled, outputs preserved
        self.facade.fail_status_calls = 0
        self.facade.status["status"] = "complete"
        self.worker.tick()
        row = db.fetch_one("SELECT * FROM jobs WHERE id=?", (j,), db_path=self.db_path)
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["error"], "stopped by user")
        self.assertEqual(self.facade.download_count, 2)  # pre-stop + final download
        meta = __import__("json").loads(row["meta"])
        self.assertIn("result.txt", meta.get("files", []))

    def test_stop_requires_running(self):
        from app.worker import stop_job
        nb = self._notebook()
        j = self._enqueue(nb)  # queued, no ref yet
        with self.assertRaises(ValueError):
            stop_job(self.facade, j, db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
