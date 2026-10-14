from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app import auth


class AccountIdentityTests(unittest.TestCase):
    def test_relogin_keeps_identity_but_reused_id_does_not(self):
        old = SimpleNamespace(id=2, hashed_password="old-salted-hash")
        new = SimpleNamespace(id=2, hashed_password="new-salted-hash")
        self.assertEqual(auth.storage_account_key(old), auth.storage_account_key(old))
        self.assertNotEqual(auth.storage_account_key(old), auth.storage_account_key(new))

    def test_independent_servers_and_password_hashes_are_not_exposed(self):
        user = SimpleNamespace(id=2, hashed_password="private-salted-hash")
        key = auth.storage_account_key(user)
        with patch.object(auth, "settings", SimpleNamespace(SECRET_KEY="independent-test-server")):
            self.assertNotEqual(key, auth.storage_account_key(user))
        self.assertEqual(len(key), 64)
        self.assertNotIn(user.hashed_password, key)
