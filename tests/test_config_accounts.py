"""Env-only Kaggle account parsing tests (offline)."""
import json
import os
import unittest

from app import config


class EnvAccountsTest(unittest.TestCase):
    def setUp(self):
        self._env_keys = ("KAGGLE_ACCOUNTS", "KAGGLE_USERNAME", "KAGGLE_KEY")
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        config._env_accounts = None

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config._env_accounts = None

    def _parse(self):
        config._env_accounts = None
        return config._parse_env_accounts()

    def test_json_list_of_accounts(self):
        os.environ["KAGGLE_ACCOUNTS"] = json.dumps([
            {"name": "A", "username": "u1", "key": "k1"},
            {"name": "B", "username": "u2", "key": "k2"},
        ])
        accounts = self._parse()
        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0]["id"], 1)
        self.assertEqual(accounts[0]["username"], "u1")
        self.assertEqual(accounts[0]["key"], "k1")
        self.assertEqual(accounts[1]["id"], 2)
        self.assertEqual(accounts[1]["name"], "B")

    def test_missing_name_defaults_to_username(self):
        os.environ["KAGGLE_ACCOUNTS"] = json.dumps([{"username": "u1", "key": "k1"}])
        accounts = self._parse()
        self.assertEqual(accounts[0]["name"], "u1")

    def test_invalid_json_falls_back_to_legacy_pair(self):
        os.environ["KAGGLE_ACCOUNTS"] = "not json"
        os.environ["KAGGLE_USERNAME"] = "legacy_u"
        os.environ["KAGGLE_KEY"] = "legacy_k"
        accounts = self._parse()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["username"], "legacy_u")

    def test_legacy_pair_only(self):
        os.environ["KAGGLE_USERNAME"] = "legacy_u"
        os.environ["KAGGLE_KEY"] = "legacy_k"
        accounts = self._parse()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["id"], 1)

    def test_entries_missing_key_skipped(self):
        os.environ["KAGGLE_ACCOUNTS"] = json.dumps([
            {"name": "bad", "username": "u1"},  # no key
            {"name": "ok", "username": "u2", "key": "k2"},
            "not-a-dict",
        ])
        accounts = self._parse()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["username"], "u2")

    def test_get_account_resolution(self):
        os.environ["KAGGLE_ACCOUNTS"] = json.dumps([
            {"username": "u1", "key": "k1"},
            {"username": "u2", "key": "k2"},
        ])
        self.assertEqual(config.get_account(2)["username"], "u2")
        self.assertEqual(config.get_account(None)["username"], "u1")  # default = first
        self.assertIsNone(config.get_account(99))
        roster = config.accounts()
        self.assertEqual(roster[0]["is_default"], True)
        self.assertEqual(roster[0]["username"], "u1")
        self.assertNotIn("key", roster[0])  # never leak the secret in the roster

    def test_nothing_set(self):
        self.assertEqual(self._parse(), [])
        self.assertIsNone(config.get_account(None))
        self.assertEqual(config.accounts(), [])


if __name__ == "__main__":
    unittest.main()
