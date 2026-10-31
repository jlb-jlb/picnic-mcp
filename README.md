# picnic-mcp

An MCP server that lets Claude search Picnic groceries and fill your cart. It uses only the Python standard library, so there is nothing to install from PyPI and no third-party code to trust. The Picnic API client and the MCP stdio protocol layer are both written from scratch in this repo.

## Status

Confirmed live against the German server: two-factor login, search, adding and removing a cart item, creating and deleting your own recipe, and searching a recipe, reading its nutrition and adding it to the cart and removing it again. Picnic has no public API, so the endpoints come from reading the public source of the maintained unofficial `python-picnic-api2` project as a reference (nothing is imported from it). Check its `src/python_picnic_api2` folder first when an endpoint stops working. Picnic can change or block this API at any time, and using it may violate their terms of service. Use it for personal use only.

Accounts with two-factor login (the German one is) get HTTP 403 `TWO_FACTOR_AUTHENTICATION_REQUIRED` on everything except login until a code is verified. Run `picnic-mcp login` once to do that.

## Tools

| Tool | What it does |
|---|---|
| `search_products` | Search the catalogue. Returns id, name, price in cents, unit size and unit price. |
| `find_ingredients` | Picks the best product per ingredient by a preference (cheapest for the amount, best value per kg, organic, conventional, smallest or largest pack) and returns the packs to buy, total cost and leftover |
| `get_cart` | Show cart items and the total in cents. |
| `add_to_cart` | Add a product by id (count 1 to 50). |
| `remove_from_cart` | Remove a quantity of a product. |
| `clear_cart` | Empty the cart. |
| `search_recipes` | Search Picnic's recipes and your own. Returns id, name, duration and labels such as Vegan. |
| `get_recipe` | Show a recipe: ingredients with the product each maps to, amounts, and nutrition per portion. |
| `add_recipe_to_cart` | Add a recipe's ingredients to the cart like the app's add-all button, with portions and optional ingredients to skip. |
| `remove_recipe_from_cart` | Take a recipe's ingredients out of the cart again. |
| `create_recipe` | Save your own recipe in the Picnic app ("Eigenes Rezept") from a name, portions and ingredient product ids. Does not touch the cart. |
| `delete_recipe` | Delete a recipe you created with `create_recipe`. |
| `get_delivery_slots` | List delivery slots for the current cart. |
| `get_order_history` | List completed deliveries, to learn what you usually buy. |

There is no checkout tool on purpose. The server fills the cart and you confirm the order in the Picnic app.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.14 is pinned in `.python-version` and uv fetches it automatically.

Log in once in a terminal. It asks for the password with hidden input, has Picnic send a code by SMS or e-mail, and stores the session token in the operating system's own credential store: Windows Credential Manager, macOS Keychain, or the Linux desktop keyring (needs `secret-tool` from libsecret and a running keyring). There is no file fallback, so on a system without a keyring the login fails with a message that says what to install. The server uses only that token and never sees the password. An older plain token file is moved into the store on first use and deleted. Log in again if the token stops working.

```bash
uv run --directory C:\Users\josch\Projects\picnic-mcp picnic-mcp login
```

| Variable | Meaning |
|---|---|
| `PICNIC_COUNTRY` | `nl` (default), `de`, `be` or `fr`. Must match for `login` and for the server. |
| `PICNIC_USER`, `PICNIC_PASSWORD` | Optional, only read by `login` to skip the prompts. |

Register the server with Claude Code (adjust the path and country):

```bash
claude mcp add picnic --env PICNIC_COUNTRY=de -- uv run --directory C:\Users\josch\Projects\picnic-mcp picnic-mcp
```

## Development

```bash
uv run python -m unittest discover -s tests
```

Layout:

- `src/picnic_mcp/client.py`: HTTP client (`urllib`), login, search, cart, deliveries, plus helpers that shrink responses for the model.
- `src/picnic_mcp/auth.py`: `picnic-mcp login` (two-factor flow) and the session token store.
- `src/picnic_mcp/server.py`: JSON-RPC 2.0 over stdio, tool registry and argument validation.
- `tests/`: offline tests with a fake network layer and a fake client.

## Ideas for later

- Nutrition lookup via Open Food Facts for healthy meal planning.
- A `plan_week` MCP prompt that turns dietary goals and past orders into a cart.
