"""Ingredient finder: rank search results for one ingredient by a stated preference.

Picnic's search returns many loosely related products with a price and a size string such as
"500g", "6 x 750ml", "ca. 120g" or "1 Bund". This module turns that into comparable numbers
(price per kg, per litre or per piece), recognises organic products from their names, works out
how many packs an amount needs, and picks the best option for a preference. It does no I/O, so
it is tested offline.
"""

import math
import re

PREFERENCES = ("cheapest", "best_value", "organic", "conventional", "smallest_pack", "largest_pack")

# Picnic's catalogue carries no organic flag in the search tiles, only the product name does.
_ORGANIC = re.compile(r"\b(bio|bioland|demeter|naturland)\b", re.IGNORECASE)
_NUMBER = r"(\d+(?:[.,]\d+)?)"
_MULTI = re.compile(rf"(\d+)\s*x\s*{_NUMBER}\s*(kg|gr|g|ml|l)\b", re.IGNORECASE)
_SINGLE = re.compile(rf"{_NUMBER}\s*(kg|gr|g|ml|l)\b", re.IGNORECASE)
_PIECES = re.compile(rf"{_NUMBER}\s*(st[uü]ck|st\.?|bund|beutel|portionen?|er pack)", re.IGNORECASE)
_PACK_OF = re.compile(r"(\d+)er pack", re.IGNORECASE)
# To the base unit: grams, millilitres or pieces.
_FACTOR = {"kg": ("g", 1000), "g": ("g", 1), "gr": ("g", 1), "l": ("ml", 1000), "ml": ("ml", 1)}
_AMOUNT = re.compile(rf"^\s*{_NUMBER}\s*([a-zA-Zäöü.]*)\s*$")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def is_organic(name) -> bool:
    return bool(name) and bool(_ORGANIC.search(name))


def parse_pack(unit_quantity):
    """Read a size string into (amount, base_unit, approximate), or None when it has no usable size.

    base_unit is 'g', 'ml' or 'piece'. '6 x 750ml' gives 4500 ml.
    """
    if not isinstance(unit_quantity, str):
        return None
    text = unit_quantity.strip()
    approximate = bool(re.match(r"(ca\.|mind\.)", text, re.IGNORECASE))
    multi = _MULTI.search(text)
    if multi:
        unit, factor = _FACTOR[multi.group(3).lower()]
        return int(multi.group(1)) * _num(multi.group(2)) * factor, unit, approximate
    single = _SINGLE.search(text)
    if single:
        unit, factor = _FACTOR[single.group(2).lower()]
        return _num(single.group(1)) * factor, unit, approximate
    pack_of = _PACK_OF.search(text)
    if pack_of:
        return float(pack_of.group(1)), "piece", approximate
    pieces = _PIECES.search(text)
    if pieces:
        return _num(pieces.group(1)), "piece", approximate
    return None


def parse_amount(value):
    """Read a needed amount such as '300 g', '0.5 l', '1,5 kg' or 3 (pieces) into (amount, base_unit)."""
    if isinstance(value, bool):
        raise ValueError("amount must be a number or a text like '300 g'")
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError("amount must be positive")
        return float(value), "piece"
    if not isinstance(value, str):
        raise ValueError("amount must be a number or a text like '300 g'")
    match = _AMOUNT.match(value)
    if not match:
        raise ValueError(f"cannot read amount {value!r}, use a form like '300 g', '0.5 l' or '3'")
    number, unit = _num(match.group(1)), match.group(2).lower().rstrip(".")
    if number <= 0:
        raise ValueError("amount must be positive")
    if unit in _FACTOR:
        base, factor = _FACTOR[unit]
        return number * factor, base
    if unit in ("", "st", "stück", "stueck", "stk", "piece", "pieces"):
        return number, "piece"
    raise ValueError(f"unknown unit {unit!r}, use g, kg, ml, l or a plain count")


def _terms(text: str):
    return [t for t in re.split(r"\s+", (text or "").casefold()) if t]


def _label(cents, base):
    per = {"g": "kg", "ml": "l", "piece": "piece"}[base]
    return f"{cents / 100:.2f} EUR/{per}"


def describe(product, need=None):
    """Add comparable numbers to a flattened search result.

    need is (amount, base_unit) or None. With a matching unit it also gives the packs to buy, the
    total cost and the leftover.
    """
    out = {
        "id": product["id"],
        "name": product.get("name"),
        "pack_size": product.get("unit_quantity"),
        "price_cents": product.get("price_cents"),
        "organic": is_organic(product.get("name")),
    }
    price = product.get("price_cents")
    pack = parse_pack(product.get("unit_quantity"))
    if pack and isinstance(price, int) and price >= 0:
        amount, base, approximate = pack
        out["pack_amount"] = amount
        out["pack_unit"] = base
        scale = 1000 if base in ("g", "ml") else 1
        out["base_price_cents"] = round(price / amount * scale)
        out["base_price"] = _label(out["base_price_cents"], base)
        if approximate:
            out["approximate_size"] = True
        if need and need[1] == base:
            packs = max(1, math.ceil(need[0] / amount))
            out["packs"] = packs
            out["total_cents"] = packs * price
            out["leftover"] = round(packs * amount - need[0], 2)
    return out


def _sort_key(preference, has_need):
    """Sort key for the options that survived the filters. Lower is better."""
    price = lambda o: o["price_cents"] if isinstance(o.get("price_cents"), int) else 10**9  # noqa: E731
    base = lambda o: o.get("base_price_cents", 10**9)  # noqa: E731
    size = lambda o: o.get("pack_amount", 0)  # noqa: E731
    if preference == "best_value":
        return lambda o: (base(o), price(o))
    if preference == "smallest_pack":
        return lambda o: (size(o) or 10**9, price(o))
    if preference == "largest_pack":
        return lambda o: (-size(o), price(o))
    if has_need:
        # Cheapest way to cover the amount, then the least waste.
        return lambda o: (o.get("total_cents", 10**9), o.get("leftover", 10**9), base(o))
    return lambda o: (price(o), base(o))


# Working numbers used for ranking that the model does not need to see.
_INTERNAL = ("pack_amount", "pack_unit", "base_price_cents")


def _public(option):
    return {k: v for k, v in option.items() if k not in _INTERNAL}


def find(products, query, preference="cheapest", amount=None, exclude=(), limit=3):
    """Pick the best product for one ingredient.

    products is the flattened search result for the query. A product is relevant when every word of
    the query appears in its name. When none is, the search order is kept and the result says so,
    since Picnic's search is fuzzy.
    """
    if preference not in PREFERENCES:
        raise ValueError(f"preference must be one of {', '.join(PREFERENCES)}")
    need = parse_amount(amount) if amount is not None else None
    words, banned = _terms(query), [t.casefold() for t in exclude if isinstance(t, str) and t.strip()]
    relevant = [
        p for p in products
        if all(w in (p.get("name") or "").casefold() for w in words)
        and not any(b in (p.get("name") or "").casefold() for b in banned)
    ]
    match = "exact"
    if not relevant:
        match = "loose"
        relevant = [p for p in products if not any(b in (p.get("name") or "").casefold() for b in banned)]
    options = [describe(p, need) for p in relevant]
    notes = []
    if match == "loose" and options:
        notes.append("no product name contains all words of the query, these are Picnic's closest hits")
    if preference == "organic":
        organic = [o for o in options if o["organic"]]
        if organic:
            options = organic
        else:
            notes.append("no organic product found, showing conventional ones")
    elif preference == "conventional":
        options = [o for o in options if not o["organic"]] or options
    if need:
        comparable = [o for o in options if "total_cents" in o]
        if comparable:
            options = comparable
        elif options:
            notes.append(f"pack sizes are not given in {need[1]}, so the amount could not be turned into packs")
    ranked = sorted(options, key=_sort_key(preference, bool(need) and any("total_cents" in o for o in options)))
    ranked = [_public(o) for o in ranked]
    return {
        "query": query,
        "preference": preference,
        "amount": amount,
        "match": match if options else "none",
        "best": ranked[0] if ranked else None,
        "alternatives": ranked[1 : 1 + limit],
        "notes": notes,
    }
