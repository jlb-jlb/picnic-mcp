"""Minimal Picnic API client using only the Python standard library.

Endpoints and request shapes were derived by reading the public source of the
unofficial python-picnic-api project as a reference. No code is imported from it.
The Picnic API is private and can change or block access at any time.
"""

import hashlib
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://storefront-prod.{country}.picnicinternational.com/api/{version}"
API_VERSION = "15"
CLIENT_ID = 30100
AUTH_HEADER = "x-picnic-auth"
# Matches the maintained python-picnic-api2 client. The older okhttp/3.9.0 got an empty 403 on login (cause unconfirmed).
USER_AGENT = "okhttp/4.9.0"
# The page endpoints (search, article details) only answer when these app headers are present.
PICNIC_AGENT = "30100;1.206.1-#15408"
SUPPORTED_COUNTRIES = ("nl", "de", "be", "fr")


class PicnicError(Exception):
    """Any failure talking to the Picnic API."""


class PicnicAuthError(PicnicError):
    """Login failed or the session token is no longer valid."""


class PicnicClient:
    def __init__(self, country: str = "nl", timeout: float = 20.0, opener=None):
        country = country.lower()
        if country not in SUPPORTED_COUNTRIES:
            raise ValueError(f"country must be one of {SUPPORTED_COUNTRIES}")
        self._base = BASE_URL.format(country=country, version=API_VERSION)
        self._timeout = timeout
        self._token = None
        # Random per-instance device id, sent as the app's x-picnic-did header.
        self._device_id = secrets.token_hex(8).upper()
        # Injectable so tests can replace the network layer.
        self._open = opener or urllib.request.build_opener().open

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _request(self, method: str, path: str, body=None):
        url = self._base + path
        data = None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Content-Type": "application/json; charset=UTF-8",
            "x-picnic-agent": PICNIC_AGENT,
            "x-picnic-did": self._device_id,
        }
        if self._token:
            headers[AUTH_HEADER] = self._token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(req, timeout=self._timeout) as resp:
                token = resp.headers.get(AUTH_HEADER)
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            token = None
            if e.code in (401, 403):
                raise PicnicAuthError(f"HTTP {e.code}: {raw[:200]!r}") from e
            raise PicnicError(f"HTTP {e.code}: {raw[:200]!r}") from e
        except urllib.error.URLError as e:
            raise PicnicError(f"network error: {e.reason}") from e
        if token:
            self._token = token
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise PicnicError("response was not valid JSON") from e
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict) and err.get("code") in ("AUTH_ERROR", "AUTH_INVALID_CRED"):
                raise PicnicAuthError(err.get("message") or err.get("code"))
        return payload

    def _get(self, path: str):
        return self._request("GET", path)

    def _post(self, path: str, body=None):
        return self._request("POST", path, body)

    def login(self, username: str, password: str):
        # The API expects the MD5 hex digest of the password as "secret".
        secret = hashlib.md5(password.encode("utf-8")).hexdigest()
        result = self._post("/user/login", {"key": username, "secret": secret, "client_id": CLIENT_ID})
        if not self.authenticated:
            raise PicnicAuthError("login returned no auth token (account may require two-factor verification)")
        return result

    def get_user(self):
        return self._get("/user")

    def search(self, term: str):
        # The old /search endpoint is gone. The page endpoint returns a UI tree of tiles.
        return self._get("/pages/search-page-results?" + urllib.parse.urlencode({"search_term": term}))

    def get_article(self, article_id: str):
        return self._get("/articles/" + urllib.parse.quote(str(article_id), safe=""))

    def get_cart(self):
        return self._get("/cart")

    def add_product(self, product_id: str, count: int = 1):
        return self._post("/cart/add_product", {"product_id": str(product_id), "count": int(count)})

    def remove_product(self, product_id: str, count: int = 1):
        return self._post("/cart/remove_product", {"product_id": str(product_id), "count": int(count)})

    def clear_cart(self):
        return self._post("/cart/clear")

    def get_delivery_slots(self):
        return self._get("/cart/delivery_slots")

    def get_deliveries(self, filters=None):
        # Unsummarised deliveries were removed by Picnic, only the summary endpoint remains.
        return self._post("/deliveries/summary", list(filters or []))


# Matches both a real nested key and an id inside an embedded JSON string, where json.dumps
# escapes the quotes with backslashes.
_SOLE_ARTICLE_ID = re.compile(r'\\*"sole_article_id\\*"\s*:\s*\\*"(\w+)')


def flatten_search(payload, limit: int = 10):
    """Reduce a search page to a short list of product dicts.

    The search page is a UI tree whose shape varies by country and A/B test, so
    this walks the whole tree for SELLING_UNIT_TILE nodes instead of using fixed
    paths. Each tile carries a sellingUnit dict with the product data. The cart
    takes the article id, which sits in a serialised "sole_article_id" string
    inside the tile, so that is reported as "id" when present.
    """
    out = []
    seen = set()

    def walk(node):
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            unit = node.get("sellingUnit")
            if node.get("type") == "SELLING_UNIT_TILE" and isinstance(unit, dict):
                match = _SOLE_ARTICLE_ID.search(json.dumps(node))
                unit_id = unit.get("id")
                product_id = match.group(1) if match else unit_id
                if product_id and product_id not in seen:
                    seen.add(product_id)
                    out.append(
                        {
                            "id": product_id,
                            "selling_unit_id": unit_id,
                            "name": unit.get("name"),
                            "price_cents": unit.get("display_price"),
                            "unit_quantity": unit.get("unit_quantity"),
                            "unit_price": unit.get("unit_quantity_sub"),
                            "price_ranges": unit.get("price_ranges"),
                        }
                    )
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return out


def summarize_cart(cart):
    """Reduce a raw cart response to items and the total."""
    items = []
    for line in cart.get("items", []) if isinstance(cart, dict) else []:
        for art in line.get("items", []):
            items.append(
                {
                    "id": art.get("id"),
                    "name": art.get("name"),
                    "count": next(
                        (d.get("quantity") for d in art.get("decorators", []) if d.get("type") == "QUANTITY"),
                        None,
                    ),
                    "price_cents": art.get("display_price", art.get("price")),
                }
            )
    return {"items": items, "total_price_cents": cart.get("total_price") if isinstance(cart, dict) else None}
