import unittest

from app import security


class TestSecurity(unittest.TestCase):
    def test_password_roundtrip(self):
        h = security.hash_password("hunter2hunter2")
        self.assertTrue(security.verify_password("hunter2hunter2", h))
        self.assertFalse(security.verify_password("wrong", h))
        self.assertFalse(security.verify_password("hunter2hunter2", "garbage"))

    def test_password_salts(self):
        self.assertNotEqual(
            security.hash_password("samepass"),
            security.hash_password("samepass"),
        )

    def test_session_token_hash(self):
        raw, hashed = security.new_session_token()
        self.assertNotEqual(raw, hashed)
        self.assertEqual(security.hash_token(raw), hashed)

    def test_secret_roundtrip(self):
        try:
            enc = security.encrypt_secret("super-secret-key")
        except ImportError:
            self.skipTest("cryptography not installed")
        self.assertNotEqual(enc, "super-secret-key")
        self.assertTrue(enc.startswith("v1:"))
        self.assertEqual(security.decrypt_secret(enc), "super-secret-key")
        self.assertEqual(security.decrypt_secret(""), "")


if __name__ == "__main__":
    unittest.main()
