import hashlib
import io
import json
import unittest
import urllib.error

from picnic_mcp.client import (
    PicnicAuthError,
    PicnicClient,
    PicnicError,
    flatten_recipe_search,
    flatten_search,
    parse_recipe,
    summarize_cart,
)


class FakeResponse:
    def __init__(self, body, headers=None):
        self._body = json.dumps(body).encode()
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Records requests and returns queued responses or raises queued errors."""

    def __init__(self, *queued):
        self.queue = list(queued)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


class ClientTests(unittest.TestCase):
    def test_login_sends_md5_secret_and_stores_token(self):
        op = FakeOpener(FakeResponse({}, {"x-picnic-auth": "tok123"}))
        c = PicnicClient(opener=op)
        c.login("a@b.c", "pw")
        req = op.requests[0]
        self.assertEqual(req.full_url, "https://storefront-prod.nl.picnicinternational.com/api/15/user/login")
        body = json.loads(req.data)
        self.assertEqual(body["secret"], hashlib.md5(b"pw").hexdigest())
        self.assertEqual(body["key"], "a@b.c")
        self.assertEqual(body["client_id"], 30100)
        self.assertTrue(c.authenticated)

    def test_token_is_sent_on_later_requests(self):
        op = FakeOpener(FakeResponse({}, {"x-picnic-auth": "tok"}), FakeResponse({"items": []}))
        c = PicnicClient(opener=op)
        c.login("u", "p")
        c.get_cart()
        self.assertEqual(op.requests[1].get_header("X-picnic-auth"), "tok")

    def test_login_without_token_raises(self):
        c = PicnicClient(opener=FakeOpener(FakeResponse({})))
        with self.assertRaises(PicnicAuthError):
            c.login("u", "p")

    def test_auth_error_body_raises(self):
        err = {"error": {"code": "AUTH_INVALID_CRED", "message": "bad"}}
        c = PicnicClient(opener=FakeOpener(FakeResponse(err)))
        with self.assertRaises(PicnicAuthError):
            c.get_cart()

    def test_http_401_is_auth_error_and_500_is_generic(self):
        c = PicnicClient(opener=FakeOpener(http_error(401), http_error(500)))
        with self.assertRaises(PicnicAuthError):
            c.get_cart()
        with self.assertRaises(PicnicError) as ctx:
            c.get_cart()
        self.assertNotIsInstance(ctx.exception, PicnicAuthError)

    def test_http_errors_name_the_endpoint_and_show_safe_headers_only(self):
        err = urllib.error.HTTPError(
            "http://x", 403, "forbidden",
            {"Server": "CloudFront", "X-Cache": "Error from cloudfront", "x-picnic-auth": "secret-token",
             "Set-Cookie": "session=abc"},
            io.BytesIO(b""),
        )
        c = PicnicClient(opener=FakeOpener(err))
        with self.assertRaises(PicnicAuthError) as ctx:
            c.get_cart()
        msg = str(ctx.exception)
        self.assertIn("HTTP 403 on GET /cart", msg)
        self.assertIn("cloudfront", msg.lower())
        self.assertNotIn("secret-token", msg)
        self.assertNotIn("session=abc", msg)

    def test_search_term_is_url_encoded_on_the_page_endpoint(self):
        op = FakeOpener(FakeResponse([]))
        PicnicClient(opener=op).search("hähnchen & reis")
        url = op.requests[0].full_url
        self.assertIn("/api/15/pages/search-page-results?", url)
        self.assertIn("search_term=h%C3%A4hnchen+%26+reis", url)

    def test_app_headers_are_sent_and_device_id_is_random_per_client(self):
        op = FakeOpener(FakeResponse({}), FakeResponse({}))
        a, b = PicnicClient(opener=op), PicnicClient(opener=op)
        a.get_cart()
        b.get_cart()
        h1, h2 = op.requests[0], op.requests[1]
        self.assertEqual(h1.get_header("X-picnic-agent"), "30100;1.206.1-#15408")
        self.assertRegex(h1.get_header("X-picnic-did"), r"^[0-9A-F]{16}$")
        self.assertNotEqual(h1.get_header("X-picnic-did"), h2.get_header("X-picnic-did"))
        self.assertEqual(h1.get_header("User-agent"), "okhttp/4.9.0")
        self.assertEqual(h1.get_header("Accept"), "*/*")

    def test_deliveries_use_the_summary_endpoint(self):
        op = FakeOpener(FakeResponse([]))
        PicnicClient(opener=op).get_deliveries(["COMPLETED"])
        req = op.requests[0]
        self.assertTrue(req.full_url.endswith("/api/15/deliveries/summary"))
        self.assertEqual(json.loads(req.data), ["COMPLETED"])

    def test_cart_mutations_use_expected_paths_and_bodies(self):
        op = FakeOpener(FakeResponse({}), FakeResponse({}), FakeResponse({}))
        c = PicnicClient(opener=op)
        c.add_product("123", 2)
        c.remove_product("123")
        c.clear_cart()
        paths = [r.full_url.split("/api/15")[1] for r in op.requests]
        self.assertEqual(paths, ["/cart/add_product", "/cart/remove_product", "/cart/clear"])
        self.assertEqual(json.loads(op.requests[0].data), {"product_id": "123", "count": 2})
        self.assertEqual(json.loads(op.requests[1].data), {"product_id": "123", "count": 1})

    def test_create_recipe_posts_the_task_payload_the_app_sends(self):
        op = FakeOpener(FakeResponse({"sellingGroupId": "abc"}))
        out = PicnicClient(opener=op).create_recipe("Test", 2, {"s1": 1, "s2": 3})
        req = op.requests[0]
        self.assertTrue(req.full_url.endswith("/api/15/pages/task/create-user-defined-recipe"))
        payload = json.loads(req.data)["payload"]
        self.assertEqual(payload["name"], "Test")
        self.assertEqual(payload["portions"], 2)
        self.assertEqual(payload["selling_units"], ["s1", "s2"])
        self.assertEqual(payload["selling_unit_quantities_by_id"], {"s1": 1, "s2": 3})
        self.assertIsNone(payload["id"])
        self.assertEqual(out["sellingGroupId"], "abc")

    def test_delete_recipe_uses_the_sellable_task(self):
        op = FakeOpener(FakeResponse({}))
        PicnicClient(opener=op).delete_recipe("abc")
        req = op.requests[0]
        self.assertTrue(req.full_url.endswith("/api/15/pages/task/delete-user-defined-sellable"))
        self.assertEqual(json.loads(req.data), {"payload": {"sellable_id": "abc"}})

    def test_recipe_search_and_pages_use_the_expected_paths(self):
        op = FakeOpener(FakeResponse({}), FakeResponse({}), FakeResponse({}))
        c = PicnicClient(opener=op)
        c.search_recipes("pasta & co")
        c.get_recipe_page("abc123")
        c.get_recipe_title("abc123")
        urls = [r.full_url.split("/api/15")[1] for r in op.requests]
        self.assertEqual(urls[0], "/pages/search-page-results?is_recipe=true&search_term=pasta+%26+co")
        self.assertEqual(urls[1], "/pages/selling-group-details-page?selling_group_id=abc123")
        self.assertEqual(urls[2], "/pages/selling-group-title-section?selling_group_id=abc123")

    def test_add_and_remove_recipe_post_the_task_payloads(self):
        op = FakeOpener(FakeResponse({}), FakeResponse({}))
        c = PicnicClient(opener=op)
        c.add_recipe_to_cart("abc123", 3, ["i1", "i2"])
        c.remove_recipe_from_cart("abc123")
        self.assertTrue(op.requests[0].full_url.endswith("/pages/task/assign-selling-group-to-basket"))
        self.assertEqual(
            json.loads(op.requests[0].data),
            {"payload": {"day_offset": None, "portions": 3, "selected_components_ids": ["i1", "i2"],
                         "selling_group_id": "abc123"}},
        )
        self.assertTrue(op.requests[1].full_url.endswith("/pages/task/remove-selling-group-from-basket"))
        self.assertEqual(json.loads(op.requests[1].data), {"payload": {"selling_group_id": "abc123"}})

    def test_country_selects_host_and_rejects_unknown(self):
        op = FakeOpener(FakeResponse({}))
        PicnicClient(country="DE", opener=op).get_cart()
        self.assertIn("storefront-prod.de.", op.requests[0].full_url)
        with self.assertRaises(ValueError):
            PicnicClient(country="xx")


class HelperTests(unittest.TestCase):
    @staticmethod
    def tile(unit_id, name, price, article_id=None, **extra):
        node = {"type": "SELLING_UNIT_TILE", "sellingUnit": {"id": unit_id, "name": name, "display_price": price, **extra}}
        if article_id:
            node["onPress"] = {"payload": json.dumps({"sole_article_id": article_id})}
        return node

    def test_flatten_search_finds_tiles_anywhere_and_respects_limit(self):
        page = {
            "body": {
                "child": {
                    "children": [
                        {"children": [self.tile("s1", "Haferflocken", 99, "1001", unit_quantity="500 g",
                                                 unit_quantity_sub="1.98/kg")]},
                        {"deeper": [{"x": [self.tile("s2", "Haferdrink", 150, "1002")]}]},
                        self.tile("s3", "Hafermilch", 200, "1003"),
                    ]
                }
            }
        }
        out = flatten_search(page, limit=2)
        self.assertEqual([a["id"] for a in out], ["1001", "1002"])
        self.assertEqual(out[0]["selling_unit_id"], "s1")
        self.assertEqual(out[0]["price_cents"], 99)
        self.assertEqual(out[0]["unit_price"], "1.98/kg")
        self.assertEqual(len(flatten_search(page, limit=10)), 3)

    def test_flatten_search_reads_article_id_from_nested_key_or_embedded_string(self):
        nested = {"type": "SELLING_UNIT_TILE", "sellingUnit": {"id": "s1", "name": "A"},
                  "onPress": {"payload": {"sole_article_id": "111"}}}
        embedded = self.tile("s2", "B", 100, "222")
        self.assertEqual([a["id"] for a in flatten_search([nested, embedded])], ["111", "222"])

    def test_flatten_search_falls_back_to_selling_unit_id_and_dedupes(self):
        page = [self.tile("s9", "Milch", 89), self.tile("s9", "Milch", 89)]
        out = flatten_search(page)
        self.assertEqual([a["id"] for a in out], ["s9"])

    def test_flatten_search_ignores_unrelated_and_empty_payloads(self):
        self.assertEqual(flatten_search({"body": {"child": {"type": "TEXT"}}}), [])
        self.assertEqual(flatten_search([]), [])
        self.assertEqual(flatten_search({"error": {"code": "X"}}), [])

    @staticmethod
    def recipe_tile(n, group_id, *texts):
        children = [{"markdown": t} for t in texts]
        return {"id": f"core-wide-recipe-tile__{n}", "pml": {"component": {"children": children},
                                                          "onPress": {"target": f"app.picnic://store/page;id=selling-group-details-page,selling_group_id={group_id}"}}}

    def test_flatten_recipe_search_reads_name_duration_and_labels(self):
        page = {"body": {"children": [
            self.recipe_tile(1, "666222e556c0d0549db6a4a2", "#(#ffffff)Vegan#(#ffffff)", "Pasta Primavera",
                             "#(#333333)30 Minuten#(#333333)"),
            self.recipe_tile(2, "666222e556c0d0549db6a4a2", "Duplicate", "5 Minuten"),
            self.recipe_tile(3, "67cb0644dcfa7e4782444ac1", "Linsensuppe", "#(#333333)1 Stunde 15 Minuten#(#333333)"),
            {"id": "other-tile", "x": 1},
        ]}}
        out = flatten_recipe_search(page)
        self.assertEqual([r["id"] for r in out], ["666222e556c0d0549db6a4a2", "67cb0644dcfa7e4782444ac1"])
        self.assertEqual(out[0], {"id": "666222e556c0d0549db6a4a2", "name": "Pasta Primavera",
                                  "duration": "30 Minuten", "labels": ["Vegan"]})
        self.assertEqual(out[1]["duration"], "1 Stunde 15 Minuten")
        self.assertEqual(len(flatten_recipe_search(page, limit=1)), 1)
        self.assertEqual(flatten_recipe_search({"body": {}}), [])

    def test_parse_recipe_reads_ingredients_amounts_and_nutrition(self):
        context = json.dumps({"ingredients": [
            {"checked": True, "ingredient_id": "i-1", "quantity": 1, "selling_unit_id": "s1", "status": "ACTIVE", "swap_type": None},
            {"checked": False, "ingredient_id": "i-2", "quantity": 2, "selling_unit_id": "s2", "status": "ACTIVE", "swap_type": None},
            {"checked": True, "ingredient_id": "i-3", "quantity": 1, "selling_unit_id": "s3", "status": "UNAVAILABLE", "swap_type": None},
            {"checked": True, "ingredient_id": "i-1", "quantity": 1, "selling_unit_id": "s1", "status": "ACTIVE", "swap_type": None},
        ]})
        page = {
            "analytics": context,
            "tiles": [{"id": "core-wide-selling-unit-tile-i-1", "c": [{"markdown": "#(#333333)Mutti #(#333333)"}, {"markdown": "Passata "}]}],
            "amounts": [{"markdown": "**Tagliatelle** 225 g"}, {"markdown": "**Du benötigst:** Pfanne"},
                        {"markdown": "**Allergene**"}],
            "nutrition": [{"markdown": "**Nährwerte**"},
                          {"accessibilityLabel": "Tabelle mit 9 Zeilen und 2 Spalten, 1 Portion des Originalrezepts enthält ungefähr:"},
                          {"accessibilityLabel": "Brennwert, 3.407 kJ"}, {"accessibilityLabel": "Eiweiß, 39,1g"},
                          {"accessibilityLabel": "Hinweis, ohne Zahl"}],
        }
        own = parse_recipe(page, {"x": [{"markdown": "#(#295813)Eigenes Rezept#(#295813)"}, {"markdown": "Pasta"}]})
        self.assertEqual((own["name"], own["description"]), ("Pasta", None))
        picnic = parse_recipe(page, {"x": [{"markdown": "Kreative Salate"}, {"markdown": "Linsen-Salat"},
                                           {"markdown": "Frisch und gesund."}]})
        self.assertEqual((picnic["name"], picnic["description"]), ("Linsen-Salat", "Frisch und gesund."))
        recipe = own
        self.assertEqual([(i["ingredient_id"], i["selected"]) for i in recipe["ingredients"]],
                         [("i-1", True), ("i-2", False), ("i-3", False)])
        self.assertEqual(recipe["ingredients"][0]["product"], "Mutti Passata")
        self.assertEqual(recipe["ingredients"][1]["quantity"], 2)
        self.assertEqual(recipe["amounts"], [{"ingredient": "Tagliatelle", "amount": "225 g"}])
        self.assertEqual(recipe["nutrition_per_portion"], {"Brennwert": "3.407 kJ", "Eiweiß": "39,1g"})

    def test_parse_recipe_sorts_ingredients_into_the_apps_groups_and_reads_the_steps(self):
        rows = [
            {"checked": True, "ingredient_id": "i-1", "quantity": 1, "selling_unit_id": "s1", "status": "ACTIVE", "swap_type": None},
            {"checked": False, "ingredient_id": "i-2", "quantity": 1, "selling_unit_id": "s2", "status": "ACTIVE", "swap_type": None},
            {"checked": False, "ingredient_id": "i-3", "quantity": 1, "selling_unit_id": "s3", "status": "ACTIVE", "swap_type": None},
            {"checked": False, "ingredient_id": "i-4", "quantity": 1, "selling_unit_id": "s4", "status": "ACTIVE", "swap_type": None},
        ]

        def group(name, tile):
            return {"id": f"sellable-components-{name}-list", "tiles": [
                {"id": f"core-wide-selling-unit-tile-{tile}", "c": [{"markdown": tile.upper()}]}]}

        page = {
            "analytics": json.dumps({"ingredients": rows}),
            "groups": [group("CORE", "i-1"), group("CORE_STOCKABLE", "i-2"),
                       group("VARIATION", "i-3"), group("CUPBOARD", "i-4")],
            "own": {"markdown": "**Eigene Zutaten:** Rapsöl, Salz"},
            "steps": {"id": "instructions-block", "c": [
                {"markdown": "So wird's gemacht"}, {"markdown": "2 Portionen"},
                {"markdown": "Schritt 1"}, {"markdown": "**300 g Nudeln** kochen."},
                {"markdown": "Schritt 2"}, {"markdown": "Servieren."}]},
        }
        recipe = parse_recipe(page)
        self.assertEqual([(i["ingredient_id"], i["kind"]) for i in recipe["ingredients"]],
                         [("i-1", "needed"), ("i-2", "probably_at_home"), ("i-3", "alternative"), ("i-4", "pantry")])
        self.assertEqual(recipe["instructions"], ["300 g Nudeln kochen.", "Servieren."])
        self.assertEqual(recipe["own_ingredients"], "Rapsöl, Salz")

    def test_parse_recipe_tolerates_an_empty_page(self):
        recipe = parse_recipe({})
        self.assertEqual((recipe["name"], recipe["ingredients"], recipe["nutrition_per_portion"]), (None, [], {}))
        self.assertEqual((recipe["instructions"], recipe["own_ingredients"]), ([], None))

    def test_summarize_cart_extracts_quantity_and_total(self):
        cart = {
            "total_price": 450,
            "items": [
                {"items": [{"id": "9", "name": "Eggs", "display_price": 250,
                            "decorators": [{"type": "QUANTITY", "quantity": 2}]}]}
            ],
        }
        s = summarize_cart(cart)
        self.assertEqual(s["total_price_cents"], 450)
        self.assertEqual(s["items"][0]["count"], 2)

    def test_summarize_cart_handles_non_dict(self):
        self.assertEqual(summarize_cart([]), {"items": [], "total_price_cents": None})


if __name__ == "__main__":
    unittest.main()


class NoRedirectTest(unittest.TestCase):
    """A redirect must never carry the session token to another host."""

    def test_a_redirect_is_an_error_and_is_not_followed(self):
        import http.server
        import threading
        import urllib.request

        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("x-picnic-auth"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        target = http.server.HTTPServer(("127.0.0.1", 0), Target)

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/x")
                self.end_headers()

            def log_message(self, *args):
                pass

        redirector = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        for srv in (target, redirector):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        client = PicnicClient(token="secret-token")
        client._base = f"http://127.0.0.1:{redirector.server_address[1]}"
        with self.assertRaises(PicnicError) as ctx:
            client._get("/anything")
        self.assertIn("302", str(ctx.exception))
        self.assertEqual(seen, [])


class DeliverySummaryTest(unittest.TestCase):
    def test_slots_keep_only_available_ones_soonest_first_and_respect_the_limit(self):
        from picnic_mcp.client import summarize_slots

        payload = {
            "delivery_slots": [
                {"slot_id": "c", "window_start": "2026-11-03T10:00", "window_end": "2026-11-03T11:00", "cut_off_time": "x", "is_available": True},
                {"slot_id": "a", "window_start": "2026-11-01T10:00", "window_end": "2026-11-01T11:00", "cut_off_time": "y", "is_available": True},
                {"slot_id": "full", "window_start": "2026-11-01T08:00", "window_end": "z", "cut_off_time": "y", "is_available": False},
                {"slot_id": "b", "window_start": "2026-11-02T10:00", "window_end": "2026-11-02T11:00", "cut_off_time": "y", "is_available": True},
            ],
            "selected_slot": {"slot_id": "b", "state": "IMPLICIT"},
        }
        out = summarize_slots(payload, limit=2)
        self.assertEqual([s["slot_id"] for s in out], ["a", "b"])
        self.assertTrue(out[1]["selected"])
        self.assertEqual(summarize_slots({}), [])
        self.assertEqual(summarize_slots([]), [])

    def test_deliveries_are_newest_first_with_summed_totals_and_no_internal_ids(self):
        from picnic_mcp.client import summarize_deliveries

        payload = [
            {"delivery_id": "old", "creation_time": "2026-01-01T10:00", "status": "COMPLETED", "delivery_time": {"start": "s1", "end": "e1"}, "orders": [{"total_price": 1000}], "slot": {"hub_id": "X"}},
            {"delivery_id": "new", "creation_time": "2026-05-01T10:00", "status": "COMPLETED", "eta2": {"start": "s2", "end": "e2"}, "orders": [{"total_price": 700}, {"total_price": 86}, {"total_price": None}]},
        ]
        out = summarize_deliveries(payload, limit=5)
        self.assertEqual([d["delivery_id"] for d in out], ["new", "old"])
        self.assertEqual(out[0]["total_cents"], 786)
        self.assertEqual(out[0]["delivered_from"], "s2")
        self.assertNotIn("slot", out[1])
        self.assertEqual(summarize_deliveries({"error": 1}), [])
