"""Minimal MCP server over stdio, standard library only.

Transport is newline-delimited JSON-RPC 2.0 on stdin/stdout. Only stdout carries
protocol messages, diagnostics go to stderr. The session comes from the token that
`picnic-mcp login` stores (country from PICNIC_COUNTRY). The password is never
needed by the server and nothing secret is logged.

There is intentionally no checkout tool. The server fills the cart and the user
confirms the order in the Picnic app.
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from . import finder, weblogin
from .icon import ICON_PNG_BASE64
from .auth import forget_token, load_token, login_main
from .secretstore import SecretStoreError
from .client import (
    PicnicAuthError,
    PicnicClient,
    PicnicError,
    flatten_recipe_search,
    flatten_search,
    summarize_deliveries,
    summarize_slots,
    parse_recipe,
    summarize_cart,
)

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {
    "name": "picnic-mcp",
    "title": "Picnic",
    "version": "0.2.0",
    # Clients that support server icons show this in the connector list.
    "icons": [{"src": "data:image/png;base64," + ICON_PNG_BASE64, "mimeType": "image/png", "sizes": ["128x128"]}],
}
MAX_COUNT = 50

_client = None


def get_client() -> PicnicClient:
    """Create the shared client on first use, preferably from the stored session token."""
    global _client
    if _client is None:
        country = os.environ.get("PICNIC_COUNTRY", "nl")
        try:
            token = load_token(country)
        except SecretStoreError as exc:
            raise PicnicAuthError(str(exc)) from exc
        if token:
            _client = PicnicClient(country=country, token=token)
            return _client
        raise PicnicAuthError(
            "no stored session for this country. Call the start_login tool now, without asking the user first: it "
            "opens a sign-in page in their browser where they enter the password and the two-factor code. Only if that "
            f"fails, the user can run this in a terminal: {_login_command()}"
        )
    return _client


def _login_command() -> str:
    """A terminal command that works even when picnic-mcp is not on PATH (uv and plugin installs)."""
    launcher = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if launcher and os.path.isfile(launcher) and not launcher.lower().endswith(".py"):
        return f'{"& " if sys.platform == "win32" else ""}"{launcher}" login --web'
    return "uv run picnic-mcp login --web"


def _count(args) -> int:
    count = args.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
        raise ValueError(f"count must be an integer from 1 to {MAX_COUNT}")
    return count


def _product_id(args) -> str:
    pid = args.get("product_id")
    if not isinstance(pid, str) or not pid.strip():
        raise ValueError("product_id must be a non-empty string")
    return pid.strip()


def tool_search_products(args):
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    limit = args.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("limit must be an integer from 1 to 30")
    return flatten_search(get_client().search(query.strip()), limit)


def tool_get_cart(args):
    return summarize_cart(get_client().get_cart())


def tool_add_to_cart(args):
    return summarize_cart(get_client().add_product(_product_id(args), _count(args)))


def tool_remove_from_cart(args):
    return summarize_cart(get_client().remove_product(_product_id(args), _count(args)))


def tool_clear_cart(args):
    return summarize_cart(get_client().clear_cart())


def _limit(args, default, maximum):
    limit = args.get("limit", default)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be an integer from 1 to {maximum}")
    return limit


def tool_get_delivery_slots(args):
    return summarize_slots(get_client().get_delivery_slots(), _limit(args, 10, 40))


def tool_get_order_history(args):
    return summarize_deliveries(get_client().get_deliveries(["COMPLETED"]), _limit(args, 10, 50))


def tool_start_login(args):
    country = os.environ.get("PICNIC_COUNTRY", "nl")
    _login, url, opened = weblogin.begin(country)
    return {
        "url": url,
        "browser_opened": opened,
        "expires_in_seconds": weblogin.TIMEOUT_SECONDS,
        "next": "Tell the user to sign in on that page and enter the code Picnic sends. "
        "Then retry the request. Do not ask for or handle the password yourself.",
    }


MAX_FINDER_ITEMS = 20


def _finder_item(item, default_preference):
    if not isinstance(item, dict):
        raise ValueError("each item must be an object with a query")
    query = item.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    exclude = item.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
        raise ValueError("exclude must be a list of words")
    result = finder.find(
        flatten_search(get_client().search(query.strip()), 40),
        query.strip(),
        item.get("preference", default_preference),
        item.get("amount"),
        exclude,
    )
    return result


def tool_find_ingredients(args):
    items = args.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_FINDER_ITEMS:
        raise ValueError(f"items must be a list of 1 to {MAX_FINDER_ITEMS} objects")
    default = args.get("preference", "cheapest")
    if default not in finder.PREFERENCES:
        raise ValueError(f"preference must be one of {', '.join(finder.PREFERENCES)}")
    get_client()  # create the client once before the worker threads share it

    def one(item):
        try:
            return _finder_item(item, default)
        except PicnicAuthError:
            raise
        except (ValueError, PicnicError) as exc:
            # One bad ingredient should not hide the others.
            return {"query": item.get("query") if isinstance(item, dict) else None, "error": str(exc)}

    # Each ingredient is a separate search request, so run a few at a time. map keeps the input order.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, items))


MAX_INGREDIENTS = 40
MAX_PORTIONS = 20
_RECIPE_ID = re.compile(r"^[0-9a-f]{24,32}$")


def _ingredients(args) -> dict:
    """Validate the ingredient list and merge duplicate products by summing quantities."""
    items = args.get("ingredients")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_INGREDIENTS:
        raise ValueError(f"ingredients must be a list of 1 to {MAX_INGREDIENTS} items")
    merged = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each ingredient must be an object with product_id and quantity")
        pid = _product_id(item)
        quantity = item.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_COUNT:
            raise ValueError(f"quantity must be an integer from 1 to {MAX_COUNT}")
        merged[pid] = merged.get(pid, 0) + quantity
    return merged


def tool_create_recipe(args):
    name = args.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("name must be a non-empty string of at most 80 characters")
    portions = args.get("portions", 2)
    if isinstance(portions, bool) or not isinstance(portions, int) or not 1 <= portions <= MAX_PORTIONS:
        raise ValueError(f"portions must be an integer from 1 to {MAX_PORTIONS}")
    ingredients = _ingredients(args)
    result = get_client().create_recipe(name.strip(), portions, ingredients)
    recipe_id = result.get("sellingGroupId") if isinstance(result, dict) else None
    if not recipe_id:
        raise PicnicError("Picnic did not return a recipe id")
    return {"recipe_id": recipe_id, "name": name.strip(), "portions": portions, "ingredient_count": len(ingredients)}


def tool_delete_recipe(args):
    recipe_id = args.get("recipe_id")
    if not isinstance(recipe_id, str) or not _RECIPE_ID.match(recipe_id.strip()):
        raise ValueError("recipe_id must be the hex id returned by create_recipe")
    get_client().delete_recipe(recipe_id.strip())
    return {"deleted": recipe_id.strip()}


def _recipe_id(args) -> str:
    recipe_id = args.get("recipe_id")
    if not isinstance(recipe_id, str) or not _RECIPE_ID.match(recipe_id.strip()):
        raise ValueError("recipe_id must be a hex id from search_recipes or create_recipe")
    return recipe_id.strip()


def tool_search_recipes(args):
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    limit = args.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be an integer from 1 to 20")
    return flatten_recipe_search(get_client().search_recipes(query.strip()), limit)


def tool_get_recipe(args):
    recipe_id = _recipe_id(args)
    client = get_client()
    recipe = parse_recipe(client.get_recipe_page(recipe_id), client.get_recipe_title(recipe_id))
    return {"id": recipe_id, **recipe}


def tool_add_recipe_to_cart(args):
    recipe_id = _recipe_id(args)
    portions = args.get("portions", 2)
    if isinstance(portions, bool) or not isinstance(portions, int) or not 1 <= portions <= MAX_PORTIONS:
        raise ValueError(f"portions must be an integer from 1 to {MAX_PORTIONS}")
    skip = args.get("skip_ingredient_ids", [])
    if not isinstance(skip, list) or not all(isinstance(s, str) for s in skip):
        raise ValueError("skip_ingredient_ids must be a list of ingredient ids from get_recipe")
    include = args.get("include_ingredient_ids", [])
    if not isinstance(include, list) or not all(isinstance(s, str) for s in include):
        raise ValueError("include_ingredient_ids must be a list of ingredient ids from get_recipe")
    client = get_client()
    recipe = parse_recipe(client.get_recipe_page(recipe_id))
    known = {i["ingredient_id"] for i in recipe["ingredients"]}
    unknown = [s for s in include if s not in known]
    if unknown:
        raise ValueError(f"unknown ingredient ids for this recipe: {unknown}")
    chosen = [
        i["ingredient_id"]
        for i in recipe["ingredients"]
        if (i["selected"] or i["ingredient_id"] in include) and i["ingredient_id"] not in skip
    ]
    if not chosen:
        raise ValueError("this recipe has no ingredients to add")
    client.add_recipe_to_cart(recipe_id, portions, chosen)
    # The task response carries the cart in the app's internal state format, so read the real cart.
    return summarize_cart(client.get_cart())


def tool_remove_recipe_from_cart(args):
    client = get_client()
    client.remove_recipe_from_cart(_recipe_id(args))
    return summarize_cart(client.get_cart())


_ID = {"type": "string", "description": "Picnic product id from search_products"}
_RECIPE = {"type": "string", "description": "Recipe id from search_recipes or create_recipe"}
_COUNT = {"type": "integer", "minimum": 1, "maximum": MAX_COUNT, "default": 1}

TOOLS = {
    "search_products": (
        "Search the Picnic catalogue. Returns id, name, price in cents, unit size and unit price.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term, e.g. 'haferflocken'"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
            },
            "required": ["query"],
        },
        tool_search_products,
    ),
    "find_ingredients": (
        "Find the best Picnic product for each ingredient of a recipe or shopping list, by a preference. "
        "Per item it searches, keeps products whose name contains every query word, and ranks them. "
        "Preferences: 'cheapest' (lowest total cost for the amount, so a 250 g pack beats a 1 kg pack "
        "when 200 g is needed), 'best_value' (lowest price per kg or litre), 'organic' (only Bio products, "
        "cheapest first), 'conventional', 'smallest_pack', 'largest_pack'. Give amount like '300 g', "
        "'0.5 l' or '3' (pieces) to get the packs to buy, the total cost and the leftover. Organic is "
        "recognised from the product name (Bio, Bioland, Demeter, Naturland). Set preference per item to "
        "mix, for example organic vegetables and cheapest pasta. exclude drops products whose name "
        "contains a word, such as 'mix' or 'stifte'. Returns best and a few alternatives per item and "
        "does not touch the cart: confirm the choices, then call add_to_cart with packs as count.",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_FINDER_ITEMS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Ingredient search term, e.g. 'karotten'"},
                            "amount": {"type": ["string", "number"], "description": "Amount needed, e.g. '300 g'"},
                            "preference": {"type": "string", "enum": list(finder.PREFERENCES)},
                            "exclude": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["query"],
                    },
                },
                "preference": {"type": "string", "enum": list(finder.PREFERENCES), "default": "cheapest"},
            },
            "required": ["items"],
        },
        tool_find_ingredients,
    ),
    "start_login": (
        "Open a sign-in page for Picnic in the user's browser. The page runs on the user's own computer "
        "(127.0.0.1, one-time address, expires after 5 minutes), asks for e-mail, password and the code "
        "Picnic sends by SMS or e-mail, and stores the session token in the system credential store. The "
        "password never reaches Claude. Use it when another tool reports that no session is stored.",
        {"type": "object", "properties": {}},
        tool_start_login,
    ),
    "get_cart": ("Show the current cart with item counts and the total price in cents.", {"type": "object", "properties": {}}, tool_get_cart),
    "add_to_cart": (
        "Add a product to the cart. Does not place an order.",
        {"type": "object", "properties": {"product_id": _ID, "count": _COUNT}, "required": ["product_id"]},
        tool_add_to_cart,
    ),
    "remove_from_cart": (
        "Remove a quantity of a product from the cart.",
        {"type": "object", "properties": {"product_id": _ID, "count": _COUNT}, "required": ["product_id"]},
        tool_remove_from_cart,
    ),
    "clear_cart": ("Remove every item from the cart.", {"type": "object", "properties": {}}, tool_clear_cart),
    "search_recipes": (
        "Search Picnic's own recipes and the user's recipes. Returns id, name, duration and labels such as 'Vegan'.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term, e.g. 'linsen'"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            "required": ["query"],
        },
        tool_search_recipes,
    ),
    "get_recipe": (
        "Show a recipe: name, description, preparation steps, ingredients with the Picnic product each maps to, "
        "amounts, and nutrition per portion (energy, fat, carbohydrates, sugar, protein, salt, fibre). Each "
        "ingredient has a kind: 'needed' (added to the cart by default), 'probably_at_home' (the app's 'Bestimmt "
        "noch zuhause' section, such as onions), 'pantry' (oil, salt, spices, also listed in own_ingredients) or "
        "'alternative' (a variant of a needed ingredient). Only 'needed' ones are selected by default.",
        {"type": "object", "properties": {"recipe_id": _RECIPE}, "required": ["recipe_id"]},
        tool_get_recipe,
    ),
    "add_recipe_to_cart": (
        "Add a recipe's needed ingredients to the cart, like the app's 'add all' button. Ingredients the user "
        "probably has at home, pantry items and alternatives are left out by default. Use include_ingredient_ids (from get_recipe) to add ones "
        "the user lacks, and skip_ingredient_ids to leave out needed ones the user already has. Does not place an order.",
        {
            "type": "object",
            "properties": {
                "recipe_id": _RECIPE,
                "portions": {"type": "integer", "minimum": 1, "maximum": MAX_PORTIONS, "default": 2},
                "skip_ingredient_ids": {"type": "array", "items": {"type": "string"}},
                "include_ingredient_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["recipe_id"],
        },
        tool_add_recipe_to_cart,
    ),
    "remove_recipe_from_cart": (
        "Take a recipe's ingredients out of the cart again.",
        {"type": "object", "properties": {"recipe_id": _RECIPE}, "required": ["recipe_id"]},
        tool_remove_recipe_from_cart,
    ),
    "create_recipe": (
        "Save a recipe of the user's own in the Picnic app ('Eigenes Rezept'), so its ingredients can be "
        "added to the cart there in one tap. Find each ingredient with search_products first and pass its id. "
        "Quantity is the number of packages of that product. Does not touch the cart or place an order.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Recipe title, at most 80 characters"},
                "portions": {"type": "integer", "minimum": 1, "maximum": MAX_PORTIONS, "default": 2},
                "ingredients": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_INGREDIENTS,
                    "items": {
                        "type": "object",
                        "properties": {"product_id": _ID, "quantity": _COUNT},
                        "required": ["product_id"],
                    },
                },
            },
            "required": ["name", "ingredients"],
        },
        tool_create_recipe,
    ),
    "delete_recipe": (
        "Delete a recipe the user created with create_recipe. Only for user-created recipes, never Picnic's own.",
        {
            "type": "object",
            "properties": {"recipe_id": {"type": "string", "description": "Id returned by create_recipe"}},
            "required": ["recipe_id"],
        },
        tool_delete_recipe,
    ),
    "get_delivery_slots": (
        "List bookable delivery windows for the current cart, soonest first, with the time to order by. "
        "The tool only reads the slots, it does not book one.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 40, "default": 10}}},
        tool_get_delivery_slots,
    ),
    "get_order_history": (
        "List the user's recent completed deliveries, newest first, with order time, delivery window and total "
        "paid in cents. It does not list the products in each order.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}},
        tool_get_order_history,
    ),
}


# (title, read only, destructive, idempotent). Every tool talks to Picnic, so open world is always true.
# Destructive means the call removes or deletes something. Adding to the cart is not destructive.
_ANNOTATIONS = {
    "search_products": ("Search products", True, False, True),
    "find_ingredients": ("Find the best product per ingredient", True, False, True),
    "start_login": ("Sign in to Picnic", False, False, False),
    "get_cart": ("Show the cart", True, False, True),
    "add_to_cart": ("Add a product to the cart", False, False, False),
    "remove_from_cart": ("Remove a product from the cart", False, True, False),
    "clear_cart": ("Empty the cart", False, True, True),
    "search_recipes": ("Search recipes", True, False, True),
    "get_recipe": ("Show a recipe", True, False, True),
    "add_recipe_to_cart": ("Add a recipe to the cart", False, False, False),
    "remove_recipe_from_cart": ("Remove a recipe from the cart", False, True, True),
    "create_recipe": ("Save your own recipe", False, False, False),
    "delete_recipe": ("Delete your own recipe", False, True, True),
    "get_delivery_slots": ("Show delivery slots", True, False, True),
    "get_order_history": ("Show past orders", True, False, True),
}
assert set(_ANNOTATIONS) == set(TOOLS), "every tool needs annotations"


def _tool_listing():
    listing = []
    for name, (description, schema, _) in TOOLS.items():
        title, read_only, destructive, idempotent = _ANNOTATIONS[name]
        annotations = {
            "title": title,
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": True,
        }
        listing.append(
            {"name": name, "title": title, "description": description, "inputSchema": schema, "annotations": annotations}
        )
    return listing


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in TOOLS or not isinstance(args, dict):
        return None
    try:
        data = TOOLS[name][2](args)
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
    except (ValueError, PicnicError) as e:
        # Tool errors are reported to the model, not as protocol errors.
        kind = "authentication error" if isinstance(e, PicnicAuthError) else "error"
        return {"content": [{"type": "text", "text": f"{kind}: {e}"}], "isError": True}


def handle(msg):
    """Return the response for one JSON-RPC message, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    is_notification = "id" not in msg

    if method == "initialize":
        client_version = params.get("protocolVersion") if isinstance(params, dict) else None
        return _ok(
            msg_id,
            {
                "protocolVersion": client_version or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": _tool_listing()})
    if method == "tools/call":
        result = _call_tool(params if isinstance(params, dict) else {})
        if result is None:
            return _error(msg_id, -32602, "unknown tool or invalid arguments")
        return _ok(msg_id, result)
    if is_notification:
        return None
    return _error(msg_id, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = handle(json.loads(line))
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> None:
    if sys.argv[1:2] == ["logout"]:
        country = os.environ.get("PICNIC_COUNTRY", "nl")
        try:
            forget_token(country)
        except SecretStoreError as exc:
            print(f"could not reach the credential store: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"removed the stored session for {country}", file=sys.stderr)
        sys.exit(0)
    if sys.argv[1:2] == ["login"]:
        sys.exit(weblogin.web_login_main() if sys.argv[2:3] == ["--web"] else login_main())
    # MCP messages are UTF-8 JSON. On Windows the pipes default to the ANSI code page, which turns
    # "ö" into U+FFFD in both directions, so fix the encoding before the first message.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    print("picnic-mcp ready on stdio", file=sys.stderr)
    serve()


if __name__ == "__main__":
    main()
