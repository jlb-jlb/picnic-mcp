import json
import os
import tempfile
import unittest
from unittest import mock

from picnic_mcp import auth
from picnic_mcp.client import PicnicClient, PicnicError
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


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "sub", "token.json")
        patcher = mock.patch.dict(os.environ, {"PICNIC_TOKEN_FILE": self.path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_then_load_roundtrip(self):
        auth.save_token("de", "abc")
        self.assertEqual(auth.load_token("de"), "abc")

    def test_missing_or_broken_file_means_no_token(self):
        self.assertIsNone(auth.load_token("de"))
        auth.save_token("de", "abc")
        with open(self.path, "w") as f:
            f.write("not json")
        self.assertIsNone(auth.load_token("de"))

    def test_default_path_is_per_country(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "base"}):
            os.environ.pop("PICNIC_TOKEN_FILE")
            self.assertNotEqual(auth.token_path("de"), auth.token_path("nl"))


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


class LoginCommandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = os.path.join(self.dir.name, "token.json")
        patcher = mock.patch.dict(
            os.environ,
            {"PICNIC_TOKEN_FILE": path, "PICNIC_COUNTRY": "de", "PICNIC_USER": "", "PICNIC_PASSWORD": ""},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = path

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


if __name__ == "__main__":
    unittest.main()
