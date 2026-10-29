"""Interactive login with two-factor verification and token storage.

Run `picnic-mcp login` once in a terminal. It asks for the password with getpass
(so it never lands in shell history or the environment), has Picnic send a
one-time code, verifies it, and stores the session token. The MCP server then
reuses that token and never needs the password. The token is a bearer secret,
so it is written to a per-user file and never printed.
"""

import getpass
import json
import os
import sys
from pathlib import Path

from .client import PicnicClient, PicnicError


def token_path(country: str) -> Path:
    override = os.environ.get("PICNIC_TOKEN_FILE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "picnic-mcp" / f"token-{country.lower()}.json"


def load_token(country: str) -> str | None:
    try:
        data = json.loads(token_path(country).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


def save_token(country: str, token: str) -> Path:
    path = token_path(country)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    try:
        path.chmod(0o600)  # owner-only where the platform supports it
    except OSError:
        pass
    return path


def login_main(argv=None, client_factory=PicnicClient, ask=input, ask_secret=getpass.getpass) -> int:
    country = os.environ.get("PICNIC_COUNTRY", "nl")
    user = os.environ.get("PICNIC_USER") or ask("Picnic e-mail: ").strip()
    password = os.environ.get("PICNIC_PASSWORD") or ask_secret("Picnic password: ")
    client = client_factory(country=country)
    try:
        client.login(user, password)
        channel = (ask("Send the code by SMS or EMAIL? [SMS]: ").strip().upper() or "SMS")
        client.generate_2fa(channel)
        client.verify_2fa(ask("Code: "))
    except (PicnicError, ValueError) as e:
        print(f"login failed: {e}", file=sys.stderr)
        return 1
    path = save_token(country, client.token)
    print(f"logged in, session token stored in {path}", file=sys.stderr)
    return 0
