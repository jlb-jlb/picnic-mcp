import http.client
import unittest
import urllib.parse

from picnic_mcp import weblogin
from picnic_mcp.client import PicnicError
from picnic_mcp.secretstore import SecretStoreError


class FakeClient:
    instances = []

    def __init__(self, country, fail_login=False, fail_code=False):
        self.token = "tok-123"
        self.calls = []
        self.fail_login, self.fail_code = fail_login, fail_code
        FakeClient.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if self.fail_login:
            raise PicnicError("HTTP 401")

    def generate_2fa(self, channel):
        self.calls.append(("generate", channel))

    def verify_2fa(self, code):
        self.calls.append(("verify", code))
        if self.fail_code:
            raise PicnicError("HTTP 400")


def factory(**flags):
    def make(country):
        return FakeClient(country, **flags)

    return make


class Page:
    """A running login page plus a small HTTP helper that talks to it like a browser."""

    def __init__(self, test, client_factory=None, save=None, **kw):
        self.saved = []
        FakeClient.instances.clear()
        self.login = weblogin.WebLogin(
            "de", client_factory=client_factory or factory(), save=save or (lambda c, t: self.saved.append((c, t))), **kw
        )
        self.url = self.login.start()
        test.addCleanup(self.login.stop)
        parts = urllib.parse.urlsplit(self.url)
        self.port, self.base = parts.port, parts.path

    def request(self, method, path=None, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        hdrs.update(headers or {})
        data = urllib.parse.urlencode(body) if body is not None else None
        if data is not None:
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        conn.request(method, path if path is not None else self.base, body=data, headers=hdrs)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        result = (response.status, text, dict(response.getheaders()))
        conn.close()
        return result

    def post(self, step, body, **kw):
        return self.request("POST", self.base + step, body, **kw)


class FlowTests(unittest.TestCase):
    def test_full_login_stores_the_token_and_closes(self):
        page = Page(self)
        status, text, _ = page.request("GET")
        self.assertEqual(status, 200)
        self.assertIn('type=password', text)
        status, text, _ = page.post("login", {"user": "me@x.de", "password": "pw", "channel": "SMS"})
        self.assertIn("one-time code", text)
        status, text, _ = page.post("code", {"code": " 654321 "})
        self.assertIn("You are signed in", text)
        self.assertEqual(page.login.wait(), "done")
        self.assertEqual(page.saved, [("de", "tok-123")])
        self.assertEqual(
            FakeClient.instances[0].calls,
            [("login", "me@x.de", "pw"), ("generate", "SMS"), ("verify", "654321")],
        )

    def test_the_password_is_never_in_a_page_or_kept_on_the_object(self):
        page = Page(self)
        _, text, _ = page.post("login", {"user": "me@x.de", "password": "hunter2-secret", "channel": "EMAIL"})
        self.assertNotIn("hunter2-secret", text)
        self.assertNotIn("hunter2-secret", repr(vars(page.login)))

    def test_wrong_credentials_show_a_generic_error_and_stay_on_step_one(self):
        page = Page(self, client_factory=factory(fail_login=True))
        status, text, _ = page.post("login", {"user": "me@x.de", "password": "bad", "channel": "SMS"})
        self.assertEqual(status, 200)
        self.assertIn("did not accept", text)
        self.assertNotIn("HTTP 401", text)
        self.assertEqual(page.login.state, "waiting")

    def test_wrong_code_can_be_retried_and_nothing_is_stored(self):
        page = Page(self, client_factory=factory(fail_code=True))
        page.post("login", {"user": "me@x.de", "password": "pw", "channel": "SMS"})
        _, text, _ = page.post("code", {"code": "000000"})
        self.assertIn("not accepted", text)
        self.assertEqual(page.saved, [])
        self.assertEqual(page.login.state, "waiting")

    def test_too_many_failures_close_the_page(self):
        page = Page(self, client_factory=factory(fail_login=True), max_failures=2)
        for _ in range(2):
            page.post("login", {"user": "a@b.de", "password": "x", "channel": "SMS"})
        self.assertEqual(page.login.wait(), "failed")
        self.assertIn("too many", page.login.error)

    def test_a_store_failure_fails_the_login_with_the_reason(self):
        def broken(country, token):
            raise SecretStoreError("no keyring")

        page = Page(self, save=broken)
        page.post("login", {"user": "me@x.de", "password": "pw", "channel": "SMS"})
        _, text, _ = page.post("code", {"code": "1"})
        self.assertEqual(page.login.wait(), "failed")
        self.assertIn("no keyring", text)

    def test_the_page_expires(self):
        page = Page(self, timeout=0.05)
        self.assertEqual(page.login.wait(), "expired")


class SafeguardTests(unittest.TestCase):
    def test_binds_loopback_only(self):
        page = Page(self)
        self.assertEqual(page.login._httpd.server_address[0], "127.0.0.1")
        self.assertTrue(page.url.startswith("http://127.0.0.1:"))

    def test_the_secret_in_the_address_is_long_and_random(self):
        a, b = Page(self), Page(self)
        self.assertNotEqual(a.base, b.base)
        self.assertGreaterEqual(len(a.base.strip("/")), 40)

    def test_wrong_secret_or_bare_root_gets_404(self):
        page = Page(self)
        for path in ("/", "/wrong/", page.base[:-4] + "xxxx/", "/favicon.ico"):
            self.assertEqual(page.request("GET", path)[0], 404, path)
        self.assertEqual(page.request("POST", "/wrong/login", {"user": "a"})[0], 404)

    def test_foreign_host_header_is_refused_against_dns_rebinding(self):
        page = Page(self)
        self.assertEqual(page.request("GET", headers={"Host": "evil.example"})[0], 404)
        self.assertEqual(page.request("GET", headers={"Host": f"evil.example:{page.port}"})[0], 404)

    def test_cross_site_post_is_refused(self):
        page = Page(self)
        status, _, _ = page.post("login", {"user": "a@b.de", "password": "x", "channel": "SMS"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 404)
        self.assertEqual(FakeClient.instances, [])

    def test_same_origin_post_is_allowed(self):
        page = Page(self)
        status, _, _ = page.post("login", {"user": "a@b.de", "password": "x", "channel": "SMS"}, headers={"Origin": f"http://127.0.0.1:{page.port}"})
        self.assertEqual(status, 200)

    def test_null_origin_is_accepted_only_when_the_browser_says_same_origin(self):
        # Firefox sends "Origin: null" on a form post under some referrer policies.
        body = {"user": "a@b.de", "password": "x", "channel": "SMS"}
        page = Page(self)
        self.assertEqual(page.post("login", body, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})[0], 200)
        for headers in ({"Origin": "null"}, {"Origin": "null", "Sec-Fetch-Site": "cross-site"}):
            page = Page(self)
            self.assertEqual(page.post("login", body, headers=headers)[0], 404, headers)

    def test_referrer_policy_keeps_the_secret_off_other_sites(self):
        page = Page(self)
        _, _, headers = page.request("GET")
        self.assertEqual(headers.get("Referrer-Policy"), "same-origin")

    def test_oversized_body_is_refused(self):
        page = Page(self)
        status, _, _ = page.post("login", {"user": "a" * 10000})
        self.assertEqual(status, 413)

    def test_code_step_cannot_be_skipped(self):
        page = Page(self)
        page.post("code", {"code": "1"})
        self.assertEqual(page.saved, [])
        self.assertEqual(FakeClient.instances, [])

    def test_response_headers_forbid_caching_framing_and_scripts(self):
        page = Page(self)
        _, text, headers = page.request("GET")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("<script", text.lower())

    def test_user_input_is_escaped_in_error_pages(self):
        page = Page(self, client_factory=factory(fail_login=True))
        _, text, _ = page.post("login", {"user": "<b>x</b>", "password": "p", "channel": "SMS"})
        self.assertNotIn("<b>x</b>", text)

    def test_nothing_is_logged(self):
        page = Page(self)
        import io, contextlib

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            page.request("GET")
            page.request("GET", "/nope")
        self.assertEqual(err.getvalue(), "")


class BeginTests(unittest.TestCase):
    def test_a_new_login_replaces_the_open_one_and_survives_no_browser(self):
        opened = []
        first, _, _ = weblogin.begin("de", open_browser=lambda url: opened.append(url) or True)
        second, url, ok = weblogin.begin("de", open_browser=lambda url: (_ for _ in ()).throw(RuntimeError("no browser")))
        self.addCleanup(second.stop)
        self.assertEqual(first.wait(), "failed")
        self.assertFalse(ok)
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        self.assertEqual(len(opened), 1)


if __name__ == "__main__":
    unittest.main()
