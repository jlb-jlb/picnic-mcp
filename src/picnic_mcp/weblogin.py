"""Browser login: a short-lived local page where the user signs in to Picnic.

The page asks for e-mail and password, has Picnic send the one-time code, asks for the code, and
stores the resulting session token in the operating system's credential store. The password goes
from the browser to this process and straight on to Picnic. It is never logged, never written to
disk, never passed to Claude and not kept after the login call returns.

Safeguards:
* binds 127.0.0.1 only, on a random port
* every URL contains a random one-time secret, anything else gets a 404
* the Host header must be the loopback address (blocks DNS rebinding) and a browser's Origin on a
  POST must be this page (blocks cross-site form posts)
* strict response headers: no caching, no framing, no referrer, no scripts at all
* the request body is capped, failed attempts are limited, and the whole page expires
* nothing is logged
"""

import html
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .auth import save_token
from .client import PicnicClient, PicnicError
from .secretstore import SecretStoreError

TIMEOUT_SECONDS = 300
MAX_FAILURES = 5
MAX_BODY = 4096

_STYLE = (
    "body{font:16px system-ui,sans-serif;max-width:26rem;margin:4rem auto;padding:0 1rem;color:#222}"
    "h1{font-size:1.4rem}label{display:block;margin:1rem 0 .25rem}"
    "input[type=email],input[type=password],input[type=text]{width:100%;padding:.5rem;box-sizing:border-box;font-size:1rem}"
    "button{margin-top:1.25rem;padding:.6rem 1.2rem;font-size:1rem}.err{color:#a00}.note{color:#555;font-size:.9rem}"
)

_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}


def _page(title, body):
    return (
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style><h1>{html.escape(title)}</h1>{body}</html>"
    ).encode("utf-8")


class WebLogin:
    def __init__(self, country, client_factory=PicnicClient, save=save_token, timeout=TIMEOUT_SECONDS, max_failures=MAX_FAILURES):
        self.country = country
        self._client_factory = client_factory
        self._save = save
        self._timeout = timeout
        self._max_failures = max_failures
        self._secret = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._client = None
        self._failures = 0
        self._step = "credentials"
        self.state = "waiting"  # waiting, done, failed or expired
        self.error = None
        self._finished = threading.Event()
        self._httpd = None
        self._timer = None
        self.port = None

    # ----- lifecycle -----

    def start(self) -> str:
        """Start serving and return the URL to open."""
        login = self
        self._httpd = HTTPServer(("127.0.0.1", 0), _make_handler(login))
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self._timer = threading.Timer(self._timeout, lambda: self._finish("expired", "the login page expired"))
        self._timer.daemon = True
        self._timer.start()
        return f"http://127.0.0.1:{self.port}/{self._secret}/"

    def wait(self) -> str:
        self._finished.wait()
        return self.state

    def stop(self):
        self._finish(self.state if self.state != "waiting" else "failed", self.error or "stopped")

    def _finish(self, state, error=None):
        with self._lock:
            if self._finished.is_set():
                return
            self.state, self.error = state, error
            self._finished.set()
        if self._timer:
            self._timer.cancel()
        self._client = None
        if self._httpd:
            # shutdown() waits for serve_forever, so it cannot run on the request thread itself.
            threading.Thread(target=self._close, daemon=True).start()

    def _close(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    # ----- request checks -----

    def allowed_host(self, host):
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def allowed_origin(self, origin):
        return origin is None or origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")

    def route(self, path):
        """The part of the path after the secret, or None when the secret is wrong."""
        prefix = "/" + self._secret
        path = path.split("?", 1)[0]
        if not (path == prefix or path.startswith(prefix + "/")):
            return None
        return path[len(prefix):].strip("/")

    # ----- pages -----

    def page(self):
        if self.state == "done":
            return 200, _page("Logged in", "<p>You are signed in to Picnic. You can close this tab and go back to Claude.</p>")
        if self.state != "waiting":
            return 410, _page("Login closed", f"<p>{html.escape(self.error or 'This login page is closed.')} Start a new login from Claude.</p>")
        if self._step == "code":
            return 200, self._code_form()
        return 200, self._credentials_form()

    def _error(self, message):
        return f"<p class=err>{html.escape(message)}</p>" if message else ""

    def _credentials_form(self, message=None):
        body = (
            f"{self._error(message)}"
            "<p class=note>This page runs on your own computer and sends your details only to Picnic. "
            "Claude never sees your password.</p>"
            "<form method=post action=login>"
            "<label for=u>E-mail</label><input id=u name=user type=email autocomplete=username required>"
            "<label for=p>Password</label><input id=p name=password type=password autocomplete=current-password required>"
            "<label>Send the code by</label>"
            "<label><input type=radio name=channel value=SMS checked> SMS</label>"
            "<label><input type=radio name=channel value=EMAIL> E-mail</label>"
            "<button type=submit>Send code</button></form>"
        )
        return _page("Sign in to Picnic", body)

    def _code_form(self, message=None):
        body = (
            f"{self._error(message)}"
            "<p class=note>Picnic sent you a one-time code.</p>"
            "<form method=post action=code>"
            "<label for=c>Code</label><input id=c name=code type=text inputmode=numeric autocomplete=one-time-code required>"
            "<button type=submit>Log in</button></form>"
        )
        return _page("Enter the code", body)

    def submit(self, route, form):
        if self.state != "waiting":
            return self.page()
        if route == "login" and self._step == "credentials":
            return self._submit_credentials(form)
        if route == "code" and self._step == "code":
            return self._submit_code(form)
        return self.page()

    def _fail_attempt(self, form_html):
        self._failures += 1
        if self._failures >= self._max_failures:
            self._finish("failed", "too many failed attempts")
            return self.page()
        return 200, form_html

    def _submit_credentials(self, form):
        user = (form.get("user") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        channel = (form.get("channel") or ["SMS"])[0]
        if not user or not password or channel not in ("SMS", "EMAIL"):
            return self._fail_attempt(self._credentials_form("Enter e-mail and password."))
        client = self._client_factory(country=self.country)
        try:
            client.login(user, password)
            client.generate_2fa(channel)
        except (PicnicError, ValueError):
            return self._fail_attempt(self._credentials_form("Picnic did not accept the login. Check e-mail and password."))
        finally:
            del password  # not kept beyond this call
        self._client, self._step = client, "code"
        return 200, self._code_form()

    def _submit_code(self, form):
        code = (form.get("code") or [""])[0].strip()
        try:
            self._client.verify_2fa(code)
            self._save(self.country, self._client.token)
        except SecretStoreError as exc:
            self._finish("failed", f"the token could not be stored: {exc}")
            return self.page()
        except (PicnicError, ValueError):
            return self._fail_attempt(self._code_form("That code was not accepted."))
        self._finish("done")
        return self.page()


def _make_handler(login):
    class Handler(BaseHTTPRequestHandler):
        server_version = "picnic-login"
        timeout = 10  # a stalled connection must not block the single-threaded server
        sys_version = ""

        def log_message(self, *args):  # nothing is logged
            pass

        def _send(self, status, body):
            self.send_response(status)
            for key, value in _HEADERS.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self):
            self._send(404, _page("Not found", ""))

        def _checked_route(self):
            if not login.allowed_host(self.headers.get("Host")):
                return None
            return login.route(self.path)

        def do_GET(self):
            route = self._checked_route()
            if route != "":
                return self._not_found()
            self._send(*login.page())

        def do_POST(self):
            route = self._checked_route()
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            # Read (a bounded amount of) the body before replying, otherwise the browser sees a
            # reset connection instead of the answer.
            body = self.rfile.read(min(max(length, 0), 4 * MAX_BODY))
            if route not in ("login", "code") or not login.allowed_origin(self.headers.get("Origin")):
                return self._not_found()
            if not 0 <= length <= MAX_BODY:
                return self._send(413, _page("Too large", ""))
            form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            self._send(*login.submit(route, form))

    return Handler


_active = None


def begin(country, login_factory=WebLogin, open_browser=webbrowser.open):
    """Start a login page, replacing any page still open, and try to open it in the browser.

    Returns (login, url, opened). The URL is returned even when no browser could be opened, for
    example on a headless machine.
    """
    global _active
    if _active is not None:
        _active.stop()
    _active = login_factory(country)
    url = _active.start()
    try:
        opened = bool(open_browser(url))
    except Exception:  # a missing browser must not fail the login
        opened = False
    return _active, url, opened


def web_login_main(login_factory=WebLogin, open_browser=webbrowser.open) -> int:
    """`picnic-mcp login --web`: open the page and wait until the login is finished."""
    country = os.environ.get("PICNIC_COUNTRY", "nl")
    login, url, opened = begin(country, login_factory, open_browser)
    hint = "" if opened else " (open this address in your browser)"
    print(f"Login page: {url}{hint}", file=sys.stderr)
    state = login.wait()
    if state == "done":
        print("logged in, session token stored in the system credential store", file=sys.stderr)
        return 0
    print(f"login {state}: {login.error}", file=sys.stderr)
    return 1
