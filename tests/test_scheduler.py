import unittest
from datetime import datetime

from app.scheduler import cron_matches, cron_is_valid


class TestCron(unittest.TestCase):
    def test_every_minute(self):
        self.assertTrue(cron_matches("* * * * *", datetime(2026, 11, 3, 12, 0)))
        self.assertTrue(cron_matches("* * * * *", datetime(2026, 11, 3, 23, 59)))

    def test_hourly_at_minute_0(self):
        c = "0 * * * *"
        self.assertTrue(cron_matches(c, datetime(2026, 11, 3, 9, 0)))
        self.assertFalse(cron_matches(c, datetime(2026, 11, 3, 9, 5)))

    def test_weekly_monday_9am(self):
        c = "0 9 * * 1"  # Monday 09:00 (py weekday 0 = Monday)
        self.assertTrue(cron_matches(c, datetime(2026, 11, 2, 9, 0)))  # Monday
        self.assertFalse(cron_matches(c, datetime(2026, 11, 3, 9, 0)))  # Tuesday
        self.assertFalse(cron_matches(c, datetime(2026, 11, 2, 10, 0)))

    def test_steps(self):
        self.assertTrue(cron_matches("*/15 * * * *", datetime(2026, 1, 1, 0, 15)))
        self.assertFalse(cron_matches("*/15 * * * *", datetime(2026, 1, 1, 0, 20)))

    def test_ranges_and_lists(self):
        self.assertTrue(cron_matches("10-20 8-9 1,15 * *", datetime(2026, 1, 15, 8, 15)))
        self.assertFalse(cron_matches("10-20 8-9 1,15 * *", datetime(2026, 1, 16, 8, 15)))

    def test_invalid(self):
        self.assertNotEqual(cron_is_valid("not a cron"), "")
        self.assertNotEqual(cron_is_valid("0 9 * *"), "")      # 4 fields
        self.assertNotEqual(cron_is_valid("99 * * * *"), "")   # out of range
        self.assertEqual(cron_is_valid(""), "")
        self.assertEqual(cron_is_valid("0 9 * * 1"), "")


if __name__ == "__main__":
    unittest.main()
