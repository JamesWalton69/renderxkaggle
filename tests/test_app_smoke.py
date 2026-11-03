"""End-to-end HTTP smoke test (fully offline).

Uses Starlette's TestClient against the real FastAPI app. No external services
are contacted:
- an embedded Postgres (embedded-postgres) and no Kaggle credentials mean the
  worker's start attempt fails *locally* with CredentialsMissing (no network),
  which still validates the full job pipeline.
"""
import os
import tempfile
import unittest
from pathlib import Path

from pg_fixture import clear_tables, ensure_pg

ensure_pg()

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="kc-smoke-")
os.environ["ADMIN_PASSWORD"] = "smoke-test-password"
os.environ["POLL_SECONDS"] = "1"

from app import db  # noqa: E402

APP_OK = True
IMPORT_ERR = None
try:
    from fastapi.testclient import TestClient  # noqa: E402
    from app.main import app  # noqa: E402
except Exception as exc:  # local env may have mismatched fastapi/starlette
    APP_OK = False
    IMPORT_ERR = exc


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not APP_OK:
            raise unittest.SkipTest(
                f"fastapi/starlette mismatch in this environment: {IMPORT_ERR}"
            )
        db.init_db()
        clear_tables("notebooks", "jobs", "job_logs", "settings", "sessions", "kaggle_accounts")
        from app import config
        config.POLL_SECONDS = 0.2
        from app.main import _bootstrap_admin
        _bootstrap_admin()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_login_gate(self):
        r = self.client.post("/api/login", json={"password": "wrong"})
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/api/me")
        self.assertEqual(r.json()["authenticated"], False)

    def test_login_success_and_flows(self):
        r = self.client.post("/api/login", json={"password": "smoke-test-password"})
        self.assertEqual(r.status_code, 200, r.text)

        r = self.client.get("/api/me")
        self.assertEqual(r.json()["authenticated"], True)

        # settings
        r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["version"], "1.0.0")

        # create notebook
        r = self.client.post("/api/notebooks", json={
            "title": "Smoke NB", "description": "test",
            "code": "print(1+1)",
        })
        self.assertEqual(r.status_code, 200, r.text)
        nb_id = r.json()["id"]

        # schedule validation
        r = self.client.put(f"/api/notebooks/{nb_id}", json={"schedule": "0 9 * * 1"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put(f"/api/notebooks/{nb_id}", json={"schedule": "nonsense"})
        self.assertEqual(r.status_code, 400)

        # run → job queued, then worker processes it (fails locally, no network)
        r = self.client.post(f"/api/notebooks/{nb_id}/run", json={})
        self.assertEqual(r.status_code, 200, r.text)
        job_id = r.json()["job_id"]

        r = self.client.get("/api/jobs")
        jobs = r.json()
        self.assertTrue(any(j["id"] == job_id for j in jobs))

        # worker should have picked it up quickly (start failure → failed, local-only)
        app.state.worker.tick()
        r = self.client.get(f"/api/jobs/{job_id}")
        status = r.json()["status"]
        self.assertEqual(status, "failed")
        self.assertIn("Kaggle credentials", self.client.get(f"/api/jobs/{job_id}").json()["error"])

        # logs exist
        r = self.client.get(f"/api/jobs/{job_id}/logs")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(len(r.json()["logs"]) >= 1)

        # multi-account CRUD via Neon Postgres (including JSON / quoted input parsing)
        r = self.client.post("/api/accounts", json={
            "name": "Acct A", "username": '"user_a"', "api_key": '{"username": "user_a", "key": "key_a"}',
        })
        self.assertEqual(r.status_code, 200, r.text)
        account_id = r.json()["id"]

        r = self.client.get("/api/accounts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["username"], "user_a")
        self.assertEqual(r.json()[0]["is_default"], 1)
        self.assertNotIn("key_enc", r.json()[0])

        r = self.client.put(f"/api/accounts/{account_id}", json={"name": "Acct Updated"})
        self.assertEqual(r.status_code, 200)

        r = self.client.put(f"/api/accounts/{account_id}/default", json={})
        self.assertEqual(r.status_code, 200)

        r = self.client.delete(f"/api/accounts/{account_id}")
        self.assertEqual(r.status_code, 200)

        # overview
        r = self.client.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total_jobs"], 1)

        # static assets
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        r = self.client.get("/css/app.css")
        self.assertEqual(r.status_code, 200)

        # logout
        r = self.client.post("/api/logout", json={})
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/me")
        self.assertEqual(r.json()["authenticated"], False)


if __name__ == "__main__":
    unittest.main()
