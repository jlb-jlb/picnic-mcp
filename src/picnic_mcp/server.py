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
import sys

from .auth import load_token, login_main
from .client import PicnicAuthError, PicnicClient, PicnicError, flatten_search, summarize_cart

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "picnic-mcp", "version": "0.1.0"}
MAX_COUNT = 50

_client = None


def get_client() -> PicnicClient:
    """Create the shared client on first use, preferably from the stored session token."""
    global _client
    if _client is None:
        country = os.environ.get("PICNIC_COUNTRY", "nl")
        token = load_token(country)
        if token:
            _client = PicnicClient(country=country, token=token)
            return _client
        raise PicnicAuthError(
            "no stored session for this country, run `picnic-mcp login` once in a terminal "
            "(Picnic requires a two-factor code)"
        )
    return _client


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


def tool_get_delivery_slots(args):
    return get_client().get_delivery_slots()


def tool_get_order_history(args):
    return get_client().get_deliveries(["COMPLETED"])


_ID = {"type": "string", "description": "Picnic product id from search_products"}
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
    "get_delivery_slots": ("List the available delivery slots for the current cart.", {"type": "object", "properties": {}}, tool_get_delivery_slots),
    "get_order_history": ("List completed deliveries, useful for learning which products the user usually buys.", {"type": "object", "properties": {}}, tool_get_order_history),
}


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
        tools = [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]
        return _ok(msg_id, {"tools": tools})
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
    if sys.argv[1:2] == ["login"]:
        sys.exit(login_main())
    print("picnic-mcp ready on stdio", file=sys.stderr)
    serve()


if __name__ == "__main__":
    main()
