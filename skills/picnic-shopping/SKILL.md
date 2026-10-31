---
name: picnic-shopping
description: Use when the user wants to shop at Picnic, turn a recipe or meal plan into a cart, compare products (cheapest, organic, best value), or save their own recipe in the Picnic app. Explains the order of the Picnic tools and the limits that apply.
---

# Shopping at Picnic

This skill describes how to use the Picnic tools in this plugin. Everything here is visible to the user and nothing in it changes what the tools do.

## Signing in

* If a tool reports that no session is stored, call `start_login`. It opens a sign-in page on the user's own computer, where they enter e-mail, password and the code Picnic sends.
* Never ask the user for their password or code in the chat, and never try to handle either yourself.

## From a recipe to a cart

1. Get the ingredients. For a Picnic recipe, call `search_recipes` and then `get_recipe`. The result lists each ingredient with a `kind`: `needed`, `probably_at_home`, `pantry` or `alternative`.
2. If the user wants a choice of products (cheapest, organic, best value), call `find_ingredients` with one item per ingredient, an amount where known, and the user's preference. It ranks products and gives the packs to buy and the total cost. It does not touch the cart.
3. Show the picks and the total, and let the user change them. Brand and pack size are the user's decision.
4. Only then add to the cart with `add_to_cart` (packs as the count), or use `add_recipe_to_cart` to add a whole Picnic recipe as the app's "add all" does.
5. Show the cart with `get_cart`.

## Limits to remember

* There is no checkout tool. The user confirms and pays in the Picnic app.
* Picnic sells whole packs. A recipe saved with `create_recipe` holds products and pack counts, not "3 carrots", and has no preparation steps or at-home section. Keep steps in the user's own notes.
* Organic is recognised from the product name (Bio, Bioland, Demeter, Naturland), so a product without that word counts as conventional.
* `find_ingredients` marks a result `loose` when no product name contains every query word. Say so instead of presenting it as an exact match.
* `clear_cart`, `remove_from_cart`, `remove_recipe_from_cart` and `delete_recipe` remove things. Ask before using them unless the user clearly asked.

## Healthy meal planning

`get_recipe` returns energy, fat, carbohydrates, sugar, protein, salt and fibre per portion for Picnic recipes. Use those numbers when comparing meals, name the source, and treat them as estimates. Do not give medical advice. `get_order_history` shows what the user usually buys and can guide suggestions.
