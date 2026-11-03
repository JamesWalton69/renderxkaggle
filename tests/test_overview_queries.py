"""SQL regression tests for API queries with Postgres type edge cases.

The overview chart filters TEXT `created_at` (UTC ISO strings) by age — the
cutoff must be a matching string parameter, not a PG timestamp expression.
"""
import unittest
from datetime import datetime, timedelta, timezone

from pg_fixture import clear_tables, ensure_pg

ensure_pg()

from app import db  # noqa: E402

SERIES_SQL = (
    "SELECT substr(created_at,1,10) AS day, COUNT(*) AS n FROM jobs "
    "WHERE created_at >= %s GROUP BY day ORDER BY day"
)


class OverviewQueriesTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        clear_tables("notebooks", "jobs", "job_logs")

    def _job_at(self, created: str) -> None:
        db.execute(
            "INSERT INTO jobs(notebook_id, kind, status, created_at) VALUES(%s,%s,%s,%s)",
            (None, "manual", "success", created),
        )

    def test_series_counts_only_recent_jobs(self):
        now = datetime.now(timezone.utc)
        self._job_at((now - timedelta(days=2)).isoformat(timespec="seconds"))  # recent → counted
        self._job_at((now - timedelta(days=20)).isoformat(timespec="seconds"))  # older than 13 days → excluded
        cutoff = (now - timedelta(days=13)).isoformat(timespec="seconds")
        rows = db.fetch_all(SERIES_SQL, (cutoff,))
        recent_day = (now - timedelta(days=2)).date().isoformat()
        self.assertEqual(rows, [{"day": recent_day, "n": 1}])


if __name__ == "__main__":
    unittest.main()
