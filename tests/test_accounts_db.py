"""Tests for Kaggle accounts in Neon Postgres with AES-GCM encryption & env fallback."""
import os
import unittest

from pg_fixture import clear_tables, ensure_pg

ensure_pg()

from app import config, db  # noqa: E402


class AccountsDbTestCase(unittest.TestCase):
    def setUp(self):
        db.init_db()
        clear_tables("kaggle_accounts", "settings")

    def test_add_list_get_accounts(self):
        # 1. Initially empty -> falls back to config.accounts()
        self.assertEqual(db.list_accounts(), [])
        self.assertIsNone(db.get_account(None))

        # 2. Add first account -> automatically is_default=1
        aid1 = db.add_account("Primary", "user1", "secret_key_1")
        self.assertGreater(aid1, 0)

        # 3. Add second account -> is_default=0
        aid2 = db.add_account("Secondary", "user2", "secret_key_2")
        self.assertGreater(aid2, 0)

        # 4. list_accounts() returns both with is_default and origin='dashboard'
        accs = db.list_accounts()
        self.assertEqual(len(accs), 2)
        self.assertEqual(accs[0]["id"], aid1)
        self.assertEqual(accs[0]["is_default"], 1)
        self.assertEqual(accs[0]["origin"], "dashboard")
        self.assertNotIn("key_enc", accs[0])

        # 5. get_account decrypts API key accurately
        acc1 = db.get_account(aid1)
        self.assertIsNotNone(acc1)
        self.assertEqual(acc1["username"], "user1")
        self.assertEqual(acc1["key"], "secret_key_1")

        acc2 = db.get_account(aid2)
        self.assertIsNotNone(acc2)
        self.assertEqual(acc2["username"], "user2")
        self.assertEqual(acc2["key"], "secret_key_2")

        # 6. Default account resolution (when account_id is None)
        def_acc = db.get_account(None)
        self.assertIsNotNone(def_acc)
        self.assertEqual(def_acc["id"], aid1)

        # 7. Switch default account to aid2
        db.set_default_account(aid2)
        def_acc_updated = db.get_account(None)
        self.assertEqual(def_acc_updated["id"], aid2)

        # 8. Delete aid2 -> aid1 becomes default
        db.delete_account(aid2)
        remaining = db.list_accounts()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], aid1)
        self.assertEqual(remaining[0]["is_default"], 1)

        # 9. Delete aid1 -> list falls back to empty
        db.delete_account(aid1)
        self.assertEqual(db.list_accounts(), [])

    def test_update_account(self):
        aid = db.add_account("Test", "user_old", "key_old")
        db.update_account(aid, name="NewName", username="user_new", key="key_new")
        acc = db.get_account(aid)
        self.assertEqual(acc["name"], "NewName")
        self.assertEqual(acc["username"], "user_new")
        self.assertEqual(acc["key"], "key_new")

    def test_kaggle_client_auth_sanitization_and_no_x_kaggle_auth(self):
        from app.kaggle_client import KaggleFacade
        client = KaggleFacade()

        # Auth cleans quotes and whitespace
        auth = client._auth({"username": ' "quoted_user" ', "key": " 'quoted_key' "})
        self.assertEqual(auth, ("basic", "quoted_user", "quoted_key"))

        # _client sets basic auth and does not include conflicting X-Kaggle-Authorization
        c = client._client(auth)
        self.assertNotIn("X-Kaggle-Authorization", c.headers)
        self.assertTrue(c.follow_redirects)
        self.assertIsNotNone(c.auth)


if __name__ == "__main__":
    unittest.main()
