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


_HIDDEN_HEADERS = {AUTH_HEADER, "set-cookie", "cookie", "authorization"}


def _safe_headers(headers) -> dict:
    """Response headers for error messages, without anything auth-related."""
    if not headers:
        return {}
    return {k.lower(): v for k, v in headers.items() if k.lower() not in _HIDDEN_HEADERS}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect. urllib would resend the auth header to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PicnicClient:
    def __init__(self, country: str = "nl", timeout: float = 20.0, opener=None, token: str | None = None):
        country = country.lower()
        if country not in SUPPORTED_COUNTRIES:
            raise ValueError(f"country must be one of {SUPPORTED_COUNTRIES}")
        self._base = BASE_URL.format(country=country, version=API_VERSION)
        self._timeout = timeout
        self._token = token
        # Random per-instance device id, sent as the app's x-picnic-did header.
        self._device_id = secrets.token_hex(8).upper()
        # Injectable so tests can replace the network layer.
        self._open = opener or urllib.request.build_opener(_NoRedirect).open

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
            detail = f"HTTP {e.code} on {method} {path.split('?')[0]}: {raw[:200]!r} headers={_safe_headers(e.headers)}"
            if e.code in (401, 403):
                raise PicnicAuthError(detail) from e
            raise PicnicError(detail) from e
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

    @property
    def token(self) -> str | None:
        return self._token

    def generate_2fa(self, channel: str = "SMS"):
        """Ask Picnic to send a one-time code. Needed once per session on 2FA accounts."""
        if channel not in ("SMS", "EMAIL"):
            raise ValueError("channel must be SMS or EMAIL")
        return self._post("/user/2fa/generate", {"channel": channel})

    def verify_2fa(self, code: str):
        """Confirm the one-time code. The session token is then fully authorised."""
        return self._post("/user/2fa/verify", {"otp": str(code).strip()})

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

    def create_recipe(self, name: str, portions: int, ingredients: dict):
        """Create a user-defined recipe ("Eigenes Rezept"). Ingredients map selling-unit id to quantity.

        The app posts to a "task" endpoint with the data wrapped in a payload. The response holds
        the new recipe's id as "sellingGroupId".
        """
        payload = {
            "id": None,
            "name": name,
            "portions": int(portions),
            "selling_unit_quantities_by_id": {str(k): int(v) for k, v in ingredients.items()},
            "selling_unit_sources": {},
            "selling_units": [str(k) for k in ingredients],
        }
        return self._post("/pages/task/create-user-defined-recipe", {"payload": payload})

    def delete_recipe(self, recipe_id: str):
        """Delete a user-defined recipe. Picnic's own recipes cannot be deleted this way."""
        return self._post("/pages/task/delete-user-defined-sellable", {"payload": {"sellable_id": str(recipe_id)}})

    def search_recipes(self, term: str):
        # Recipe search is the normal search endpoint with is_recipe=true.
        query = urllib.parse.urlencode({"is_recipe": "true", "search_term": term})
        return self._get("/pages/search-page-results?" + query)

    def get_recipe_page(self, recipe_id: str):
        return self._get("/pages/selling-group-details-page?selling_group_id=" + urllib.parse.quote(str(recipe_id), safe=""))

    def get_recipe_title(self, recipe_id: str):
        # A small page, much cheaper than the full details page, that holds just the title.
        return self._get("/pages/selling-group-title-section?selling_group_id=" + urllib.parse.quote(str(recipe_id), safe=""))

    def add_recipe_to_cart(self, recipe_id: str, portions: int, ingredient_ids):
        """Put the selected ingredients of a recipe in the cart. day_offset is null outside the meal planner."""
        payload = {
            "day_offset": None,
            "portions": int(portions),
            "selected_components_ids": list(ingredient_ids),
            "selling_group_id": str(recipe_id),
        }
        return self._post("/pages/task/assign-selling-group-to-basket", {"payload": payload})

    def remove_recipe_from_cart(self, recipe_id: str):
        return self._post("/pages/task/remove-selling-group-from-basket", {"payload": {"selling_group_id": str(recipe_id)}})

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


_RECIPE_LINK = re.compile(r"selling_group_id=([0-9a-f]{24,32})")
_COLOUR_MARKUP = re.compile(r"#\([^)]*\)")
_DURATION = re.compile(r"\d.*(Minute|Stunde|Min\.|Std)")
# Ingredient entries sit in an analytics context, serialised inside a string in the page.
_INGREDIENT = re.compile(
    r'\{"checked": (true|false), "ingredient_id": "([\w-]+)", "quantity": (\d+), '
    r'"selling_unit_id": "(\w+)", "status": "(\w+)"'
)
_AMOUNT_LINE = re.compile(r'"markdown": "\*\*([^*"]+)\*\* ([^"]+)"')
# The app sorts a recipe's ingredients into groups: what the dish needs, items people usually have
# ("Bestimmt noch zuhause"), alternatives to a needed item, and pantry items ("Eigene Zutaten").
_KINDS = {
    "CORE": "needed",
    "CORE_STOCKABLE": "probably_at_home",
    "VARIATION": "alternative",
    "CUPBOARD": "pantry",
}
_OWN_INGREDIENTS = re.compile(r'"markdown": "\*\*Eigene Zutaten:\*\* ([^"]+)"')
_STEP_LABEL = re.compile(r"^Schritt \d+$")
_NUTRITION_ROW = re.compile(r'"accessibilityLabel": "([^",]{2,60}), ([^"]{1,30})"')


def _plain(text: str) -> str:
    return _COLOUR_MARKUP.sub("", text).replace(" ", " ").strip()


def _markdowns(node, out=None):
    """Every "markdown" text in a layout subtree, in document order."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "markdown" and isinstance(value, str):
                out.append(value)
            else:
                _markdowns(value, out)
    elif isinstance(node, list):
        for item in node:
            _markdowns(item, out)
    return out


def flatten_recipe_search(payload, limit: int = 10):
    """Reduce a recipe search page to recipe tiles with id, name, duration and labels."""
    out = []
    seen = set()

    def walk(node):
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            if str(node.get("id", "")).startswith("core-wide-recipe-tile__"):
                link = _RECIPE_LINK.search(json.dumps(node, ensure_ascii=False))
                if link and link.group(1) not in seen:
                    seen.add(link.group(1))
                    raw = _markdowns(node)
                    labels = [_plain(t) for t in raw if t.startswith("#(#ffffff)")]
                    rest = [_plain(t) for t in raw if not t.startswith("#(#ffffff)") and _plain(t)]
                    duration = next((t for t in rest if _DURATION.search(t)), None)
                    names = [t for t in rest if t != duration]
                    out.append(
                        {
                            "id": link.group(1),
                            "name": names[0] if names else None,
                            "duration": duration,
                            "labels": labels + names[1:],
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


def _instruction_steps(page):
    """The preparation steps from the instructions block, as plain sentences."""
    blocks = []

    def find(node):
        if isinstance(node, dict):
            if node.get("id") == "instructions-block":
                blocks.append(node)
                return
            for value in node.values():
                find(value)
        elif isinstance(node, list):
            for item in node:
                find(item)

    find(page)
    steps = []
    texts = _markdowns(blocks[0]) if blocks else []
    for label, text in zip(texts, texts[1:]):
        if _STEP_LABEL.match(label):
            steps.append(_plain(text).replace("**", ""))
    return steps


def parse_recipe(page, title_page=None):
    """Reduce a recipe details page to name, ingredients, steps, amounts and nutrition per portion."""
    flat = json.dumps(page, ensure_ascii=False).replace('\\"', '"')
    tile_names = {}
    tile_groups = {}

    def walk(node, group=None):
        if isinstance(node, dict):
            node_id = str(node.get("id", ""))
            if node_id.startswith("sellable-components-") and node_id.endswith("-list"):
                group = node_id[len("sellable-components-"):-len("-list")]
            if node_id.startswith("core-wide-selling-unit-tile-"):
                tile_id = node_id[len("core-wide-selling-unit-tile-"):]
                tile_names[tile_id] = " ".join(t for t in (_plain(m) for m in _markdowns(node)) if t)
                tile_groups[tile_id] = group
                return
            for value in node.values():
                walk(value, group)
        elif isinstance(node, list):
            for item in node:
                walk(item, group)

    walk(page)
    ingredients = []
    seen = set()
    for checked, ingredient_id, quantity, selling_unit_id, status in _INGREDIENT.findall(flat):
        if ingredient_id in seen:
            continue
        seen.add(ingredient_id)
        ingredients.append(
            {
                "ingredient_id": ingredient_id,
                "selling_unit_id": selling_unit_id,
                "quantity": int(quantity),
                "product": tile_names.get(ingredient_id),
                "kind": _KINDS.get(tile_groups.get(ingredient_id)),
                "selected": checked == "true" and status == "ACTIVE",
            }
        )
    amounts = []
    for name, amount in _AMOUNT_LINE.findall(flat):
        if not name.endswith(":") and (name, _plain(amount)) not in amounts:
            amounts.append((name, _plain(amount)))
    start = flat.find("hrwerte")
    nutrition = {}
    if start >= 0:
        for label, value in _NUTRITION_ROW.findall(flat[start : start + 12000]):
            if any(ch.isdigit() for ch in value) and label not in nutrition:
                nutrition[label] = value
    # The title page lists a category tag, the title, then a description. Own recipes have only the title.
    name = description = None
    if title_page is not None:
        texts = [t for t in (_plain(m) for m in _markdowns(title_page)) if t and t != "Eigenes Rezept"]
        if len(texts) >= 3:
            name, description = texts[1], texts[2]
        elif texts:
            name = texts[-1]
    return {
        "name": name,
        "description": description,
        "ingredients": ingredients,
        "amounts": [{"ingredient": n, "amount": a} for n, a in amounts],
        "instructions": _instruction_steps(page),
        "own_ingredients": (_OWN_INGREDIENTS.search(flat).group(1) if _OWN_INGREDIENTS.search(flat) else None),
        "nutrition_per_portion": nutrition,
    }


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
