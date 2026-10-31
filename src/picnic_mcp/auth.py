"""Interactive login with two-factor verification and token storage.

Run `picnic-mcp login` once in a terminal. It asks for the password with getpass
(so it never lands in shell history or the environment), has Picnic send a
one-time code, verifies it, and stores the session token. The MCP server then
reuses that token and never needs the password. The token is a bearer secret,
so it goes into the operating system's credential store (see secretstore.py) and is never printed.
"""

import getpass
import json
import os
import sys
from pathlib import Path

from . import secretstore
from .client import PicnicClient, PicnicError


SERVICE = "picnic-mcp"


def get_store():
    return secretstore.default_store()


def _account(country: str) -> str:
    return f"token-{country.lower()}"


def legacy_token_path(country: str) -> Path:
    """Where earlier versions kept the token as a plain file. Read once to migrate, then deleted."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "picnic-mcp" / f"token-{country.lower()}.json"


def _migrate_legacy(country: str, store) -> str | None:
    path = legacy_token_path(country)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        return None
    store.set(SERVICE, _account(country), token)
    try:
        path.unlink()
    except OSError:
        pass
    return token


def load_token(country: str) -> str | None:
    """The stored session token, or None. Raises SecretStoreError when no secure store is available."""
    store = get_store()
    token = store.get(SERVICE, _account(country))
    return token or _migrate_legacy(country, store)


def save_token(country: str, token: str) -> None:
    get_store().set(SERVICE, _account(country), token)


def forget_token(country: str) -> None:
    get_store().delete(SERVICE, _account(country))


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
    try:
        save_token(country, client.token)
    except secretstore.SecretStoreError as e:
        print(f"login worked, but the token could not be stored: {e}", file=sys.stderr)
        return 1
    print("logged in, session token stored in the system credential store", file=sys.stderr)
    return 0
