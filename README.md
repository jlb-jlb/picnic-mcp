![Picnic MCP: search groceries, manage recipes, plan healthy meals](assets/banner.png)

# picnic-mcp

An MCP server and Claude plugin for [Picnic](https://picnic.app), the grocery delivery app. Claude can search the catalogue, pick the best product for each ingredient (cheapest, organic, best value), fill your cart, read Picnic recipes with their nutrition, and save your own recipes in the app. You confirm and pay for the order in the Picnic app, so Claude never checks out for you.

It uses only the Python standard library. There is nothing to install from PyPI and no third-party code to trust. The Picnic client, the MCP protocol layer, the browser login and the credential storage are all written from scratch in this repository.

## Read this first

This project is unofficial and not affiliated with Picnic. Picnic has no public API. The endpoints were worked out by reading the public source of the maintained `python-picnic-api2` project as a reference (nothing is imported from it) and by testing against a real account. Picnic can change or block this API at any time, and using it may go against their terms of service. Use it for your own account and for personal use.

## Install

You need [uv](https://docs.astral.sh/uv/). The first start downloads Python 3.14 (pinned in `.python-version`) and builds the project, which can take a little while. After that it starts in a second.

### As a Claude plugin

```
/plugin marketplace add jlb-jlb/picnic-mcp
/plugin install picnic-mcp@picnic-mcp-marketplace
```

Claude Code asks for your Picnic country (`nl`, `de`, `be` or `fr`) when the plugin is enabled. The plugin also adds a skill that tells Claude the order in which to use the tools.

### As a plain MCP server

```bash
git clone https://github.com/jlb-jlb/picnic-mcp
claude mcp add picnic --env PICNIC_COUNTRY=de -- uv run --locked --directory ./picnic-mcp picnic-mcp
```

Use the absolute path to the clone if your client starts the server from another folder.

## Sign in

Ask Claude to log in to Picnic. It calls the `start_login` tool, which opens a sign-in page in your browser. You enter your e-mail and password, choose SMS or e-mail for the one-time code, and enter the code. Picnic requires this two-factor step on accounts that have it enabled.

The page runs on your own computer and closes when you are done. It sends your details only to Picnic. Claude never sees your password or the code. Without Claude, the same page opens with `picnic-mcp login --web`, and a plain terminal prompt is available with `picnic-mcp login`.

## Where your credentials go

| What | Where | Notes |
|---|---|---|
| Password and one-time code | From your browser to the local page, on to Picnic | Kept in memory for the duration of the login call only. Never written to disk, never logged, never shown to Claude. |
| Session token | The operating system's credential store | Windows Credential Manager, macOS Keychain, or the Linux desktop keyring through `secret-tool`. There is no file fallback. |

Without a credential store the login fails with a message that says what to install. On Linux that means `secret-tool` from libsecret (`libsecret-tools` on Debian and Ubuntu) and a running keyring such as GNOME Keyring. Headless servers and bare WSL usually have none.

The sign-in page is a short-lived local web page with these safeguards:

- It listens on `127.0.0.1` only, on a random port.
- The address contains a one-time random secret. Any other address returns 404.
- It refuses requests with another host name (DNS rebinding) and form posts from other sites.
- It sets no cookies and runs no scripts, and a strict content security policy forbids everything else.
- The request size is capped, failed attempts are limited, and the page expires after five minutes.
- It logs nothing.

## Data and network

The server talks to one place: the Picnic API at `storefront-prod.<country>.picnicinternational.com`. It sends no telemetry and stores no conversation content. Your cart, recipes and order history live with Picnic. The only thing stored on your computer is the session token in the credential store. Log out with `picnic-mcp logout`, which deletes the stored token for your country.

## Tools

| Tool | What it does |
|---|---|
| `start_login` | Opens the local sign-in page described above. |
| `search_products` | Search the catalogue. Returns id, name, price in cents, unit size and unit price. |
| `find_ingredients` | Picks the best product per ingredient by a preference (cheapest for the amount, best value per kg, organic, conventional, smallest or largest pack) and returns the packs to buy, total cost and leftover. Does not touch the cart. |
| `get_cart` | Show cart items and the total in cents. |
| `add_to_cart` | Add a product by id (count 1 to 50). |
| `remove_from_cart` | Remove a quantity of a product. |
| `clear_cart` | Empty the cart. |
| `search_recipes` | Search Picnic's recipes and your own. Returns id, name, duration and labels such as Vegan. |
| `get_recipe` | Show a recipe: preparation steps, ingredients with the product each maps to, amounts, and nutrition per portion. |
| `add_recipe_to_cart` | Add a recipe's ingredients to the cart like the app's add-all button, with portions and optional ingredients to include or skip. |
| `remove_recipe_from_cart` | Take a recipe's ingredients out of the cart again. |
| `create_recipe` | Save your own recipe in the Picnic app ("Eigenes Rezept") from a name, portions and ingredient product ids. Does not touch the cart. |
| `delete_recipe` | Delete a recipe you created with `create_recipe`. |
| `get_delivery_slots` | List bookable delivery windows for the current cart, soonest first. Does not book one. |
| `get_order_history` | List recent completed deliveries with date, delivery window and total paid. It does not include the products. |

Every tool declares a title and the read-only, destructive, idempotent and open-world hints, so clients can ask before anything that removes data.

There is no checkout tool on purpose. The server fills the cart and you confirm the order in the Picnic app.

## Example prompts

- "Log in to Picnic, then find the cheapest products for 400 g spaghetti, 600 g carrots and 3 eggs, and show me the total before adding anything."
- "Search Picnic for a vegan lentil recipe under 30 minutes, show me the nutrition per portion, and add it for two people."
- "I want organic vegetables and the cheapest pasta. Build a cart for these five ingredients."
- "Search Picnic for a healthy dinner that takes less than 30 minutes and add it to the cart for two."

## Good to know

- Picnic sells whole packs. A recipe you create holds products and pack counts, not "3 carrots", and the app has no place for preparation steps or an at-home section in your own recipes. Keep those in your notes.
- Organic is recognised from the product name (Bio, Bioland, Demeter, Naturland), because the search results carry no organic flag.
- `find_ingredients` marks a result as loose when no product name contains every word of the query.
- Nutrition values come from Picnic's own recipes and are estimates.

## Troubleshooting

| Problem | What to do |
|---|---|
| "no stored session" | Ask Claude to log in, or run `picnic-mcp login --web`. |
| Login fails right after the code | Check that the country setting matches your account. Codes expire quickly. |
| "no desktop keyring found" (Linux) | Install `libsecret-tools` and a keyring such as `gnome-keyring`, and run inside a desktop session. |
| HTTP 403 on every call | The session token expired. Log in again. |
| First start is slow | uv is downloading Python and building the project. Later starts are fast. |
| Umlauts look broken | Update to the current version, which reads and writes UTF-8 on Windows. |

## Development

```bash
uv run python -m unittest discover -s tests
```

The tests run offline against a fake network layer. Continuous integration runs them on Windows, macOS and Linux and also does a real round trip against each system's credential store.

Layout:

- `src/picnic_mcp/client.py`: HTTP client (`urllib`), login, search, cart, recipes, deliveries, plus helpers that shrink responses for the model.
- `src/picnic_mcp/finder.py`: the ingredient finder (pack parsing, organic detection, ranking).
- `src/picnic_mcp/auth.py`, `weblogin.py`, `secretstore.py`: login, the browser sign-in page and the credential store backends.
- `src/picnic_mcp/server.py`: JSON-RPC 2.0 over stdio, tool registry, annotations and argument validation.
- `.claude-plugin/` and `skills/`: the plugin manifest, the marketplace file and the shopping skill.
- `tests/`: offline tests.

## Ideas for later

- Nutrition lookup via Open Food Facts for healthy meal planning.
- A `plan_week` MCP prompt that turns dietary goals and preferences into a cart.

## License

MIT, see [LICENSE](LICENSE).
