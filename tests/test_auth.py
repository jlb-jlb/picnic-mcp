import json
import os
import tempfile
import unittest
from unittest import mock

from picnic_mcp import auth
from picnic_mcp.client import PicnicClient, PicnicError
from picnic_mcp.secretstore import SecretStoreError
from test_client import FakeOpener, FakeResponse


class TwoFactorClientTests(unittest.TestCase):
    def test_generate_and_verify_use_the_2fa_endpoints(self):
        op = FakeOpener(FakeResponse({}), FakeResponse({}))
        c = PicnicClient(opener=op, token="t")
        c.generate_2fa("EMAIL")
        c.verify_2fa(" 123456 ")
        paths = [r.full_url.split("/api/15")[1] for r in op.requests]
        self.assertEqual(paths, ["/user/2fa/generate", "/user/2fa/verify"])
        self.assertEqual(json.loads(op.requests[0].data), {"channel": "EMAIL"})
        self.assertEqual(json.loads(op.requests[1].data), {"otp": "123456"})

    def test_unknown_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            PicnicClient(opener=FakeOpener()).generate_2fa("FAX")

    def test_a_given_token_is_sent_without_logging_in(self):
        op = FakeOpener(FakeResponse({}))
        PicnicClient(opener=op, token="saved").get_cart()
        self.assertEqual(op.requests[0].get_header("X-picnic-auth"), "saved")


class FakeStore:
    def __init__(self):
        self.items = {}

    def get(self, service, account):
        return self.items.get((service, account))

    def set(self, service, account, value):
        self.items[(service, account)] = value

    def delete(self, service, account):
        self.items.pop((service, account), None)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = {"LOCALAPPDATA": self.dir.name, "PICNIC_COUNTRY": "de", "PICNIC_USER": "", "PICNIC_PASSWORD": ""}
        for patcher in (mock.patch.object(auth, "get_store", return_value=self.store), mock.patch.dict(os.environ, env)):
            patcher.start()
            self.addCleanup(patcher.stop)


class TokenStoreTests(StoreTestCase):
    def test_save_then_load_roundtrip(self):
        auth.save_token("de", "abc")
        self.assertEqual(auth.load_token("de"), "abc")

    def test_tokens_are_kept_per_country(self):
        auth.save_token("de", "abc")
        self.assertIsNone(auth.load_token("nl"))

    def test_forget_removes_the_token(self):
        auth.save_token("de", "abc")
        auth.forget_token("de")
        self.assertIsNone(auth.load_token("de"))

    def test_nothing_stored_means_no_token(self):
        self.assertIsNone(auth.load_token("de"))

    def test_a_missing_store_is_reported_not_swallowed(self):
        with mock.patch.object(auth, "get_store", side_effect=SecretStoreError("no keyring")):
            with self.assertRaises(SecretStoreError):
                auth.load_token("de")

    def test_an_old_token_file_moves_into_the_store_and_is_deleted(self):
        legacy = auth.legacy_token_path("de")
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"token": "old"}), encoding="utf-8")
        self.assertEqual(auth.load_token("de"), "old")
        self.assertFalse(legacy.exists())
        self.assertEqual(self.store.get("picnic-mcp", "token-de"), "old")

    def test_a_broken_old_file_is_ignored(self):
        legacy = auth.legacy_token_path("de")
        legacy.parent.mkdir(parents=True)
        legacy.write_text("not json", encoding="utf-8")
        self.assertIsNone(auth.load_token("de"))


class FakeLoginClient:
    def __init__(self, country, fail_at=None):
        self.token = "new-token"
        self.calls = []
        self.fail_at = fail_at

    def _step(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise PicnicError("boom")

    def login(self, user, password):
        self._step("login")

    def generate_2fa(self, channel):
        self._step(f"generate:{channel}")

    def verify_2fa(self, code):
        self._step(f"verify:{code}")


class LoginCommandTests(StoreTestCase):
    def run_login(self, answers, **kw):
        made = []

        def factory(country):
            made.append(FakeLoginClient(country, **kw))
            return made[0]

        answers = iter(answers)
        code = auth.login_main(client_factory=factory, ask=lambda _p: next(answers), ask_secret=lambda _p: "pw")
        return code, made[0]

    def test_full_flow_stores_the_token(self):
        code, client = self.run_login(["me@x.de", "", "654321"])
        self.assertEqual(code, 0)
        self.assertEqual(client.calls, ["login", "generate:SMS", "verify:654321"])
        self.assertEqual(auth.load_token("de"), "new-token")

    def test_failure_stores_nothing_and_returns_nonzero(self):
        code, _ = self.run_login(["me@x.de", "email", "1"], fail_at="verify:1")
        self.assertEqual(code, 1)
        self.assertIsNone(auth.load_token("de"))

    def test_an_unavailable_store_fails_the_login_with_the_reason(self):
        with mock.patch.object(auth, "save_token", side_effect=SecretStoreError("no keyring")):
            code, _ = self.run_login(["me@x.de", "", "1"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
