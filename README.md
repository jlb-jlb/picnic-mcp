# picnic-mcp

An MCP server that lets Claude search Picnic groceries and fill your cart. It uses only the Python standard library, so there is nothing to install from PyPI and no third-party code to trust. The Picnic API client and the MCP stdio protocol layer are both written from scratch in this repo.

## Status

Unit tests cover the client and the protocol layer offline. A first live test connected and logged in. Search was initially broken because it used a retired endpoint and is fixed in the current version, which has not been confirmed live yet, nor has adding to the cart with the new article ids. Picnic has no public API, so the endpoints come from reading the public source of the maintained unofficial `python-picnic-api2` project as a reference (nothing is imported from it). Check its `src/python_picnic_api2` folder first when an endpoint stops working. Picnic can change or block this API at any time, and using it may violate their terms of service. Use it for personal use only.

If your account uses two-factor login, `login` raises an auth error saying no token was returned. Two-factor support is not implemented.

## Tools

| Tool | What it does |
|---|---|
| `search_products` | Search the catalogue. Returns id, name, price in cents, unit size and unit price. |
| `get_cart` | Show cart items and the total in cents. |
| `add_to_cart` | Add a product by id (count 1 to 50). |
| `remove_from_cart` | Remove a quantity of a product. |
| `clear_cart` | Empty the cart. |
| `get_delivery_slots` | List delivery slots for the current cart. |
| `get_order_history` | List completed deliveries, to learn what you usually buy. |

There is no checkout tool on purpose. The server fills the cart and you confirm the order in the Picnic app.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.14 is pinned in `.python-version` and uv fetches it automatically.

Credentials come from environment variables and are never written to disk or logged:

| Variable | Meaning |
|---|---|
| `PICNIC_USER` | Your Picnic account email |
| `PICNIC_PASSWORD` | Your Picnic password |
| `PICNIC_COUNTRY` | `nl` (default), `de`, `be` or `fr` |

Register the server with Claude Code (adjust the path and country):

```bash
claude mcp add picnic --env PICNIC_COUNTRY=de --env PICNIC_USER=you@example.com --env PICNIC_PASSWORD=yourpassword -- uv run --directory C:\Users\josch\Projects\picnic-mcp picnic-mcp
```

Passing the password on the command line stores it in Claude's MCP config. If you prefer, set the variables in your shell profile or a secrets manager and leave out the `--env` flags for them.

## Development

```bash
uv run python -m unittest discover -s tests
```

Layout:

- `src/picnic_mcp/client.py`: HTTP client (`urllib`), login, search, cart, deliveries, plus helpers that shrink responses for the model.
- `src/picnic_mcp/server.py`: JSON-RPC 2.0 over stdio, tool registry and argument validation.
- `tests/`: offline tests with a fake network layer and a fake client.

## Ideas for later

- Two-factor login.
- Nutrition lookup via Open Food Facts for healthy meal planning.
- A `plan_week` MCP prompt that turns dietary goals and past orders into a cart.
