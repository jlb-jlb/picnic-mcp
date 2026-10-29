import io
import json
import unittest
from unittest import mock

from picnic_mcp import server
from picnic_mcp.client import PicnicAuthError


class FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, term):
        self.calls.append(("search", term))
        return {
            "body": {
                "child": {
                    "children": [
                        {
                            "type": "SELLING_UNIT_TILE",
                            "sellingUnit": {"id": "s1", "name": "Oats", "display_price": 120},
                            "onPress": {"payload": '{"sole_article_id":"1"}'},
                        }
                    ]
                }
            }
        }

    def add_product(self, pid, count):
        self.calls.append(("add", pid, count))
        return {"total_price": 240, "items": []}

    def get_cart(self):
        return {"total_price": 0, "items": []}

    def create_recipe(self, name, portions, ingredients):
        self.calls.append(("create_recipe", name, portions, ingredients))
        return {"sellingGroupId": "5cd1376dad2843a998f6e114b078b1a9"}

    def delete_recipe(self, recipe_id):
        self.calls.append(("delete_recipe", recipe_id))
        return {}


def rpc(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        msg["id"] = msg_id
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def call(name, args):
    return rpc("tools/call", {"name": name, "arguments": args})


class ProtocolTests(unittest.TestCase):
    def test_initialize_echoes_version_and_declares_tools(self):
        r = rpc("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_notification_gets_no_response(self):
        self.assertIsNone(rpc("notifications/initialized", msg_id=None))

    def test_unknown_method_is_error_and_ping_works(self):
        self.assertEqual(rpc("nope")["error"]["code"], -32601)
        self.assertEqual(rpc("ping")["result"], {})

    def test_tools_list_has_schemas_and_no_checkout(self):
        tools = rpc("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("search_products", names)
        self.assertFalse({n for n in names if "checkout" in n or "order" in n and n != "get_order_history"})
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_invalid_request_and_parse_error(self):
        self.assertEqual(server.handle([])["error"]["code"], -32600)
        out = io.StringIO()
        server.serve(io.StringIO("not json\n"), out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32700)

    def test_serve_round_trip_over_streams(self):
        out = io.StringIO()
        line = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n\n"
        server.serve(io.StringIO(line), out)
        self.assertEqual(json.loads(out.getvalue())["id"], 7)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClient()
        patcher = mock.patch.object(server, "get_client", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def text(self, response):
        return json.loads(response["result"]["content"][0]["text"])

    def test_search_returns_flattened_articles(self):
        r = call("search_products", {"query": " oats "})
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(self.text(r)[0]["name"], "Oats")
        self.assertEqual(self.text(r)[0]["id"], "1")
        self.assertEqual(self.fake.calls[0], ("search", "oats"))

    def test_add_to_cart_passes_validated_arguments(self):
        r = call("add_to_cart", {"product_id": "1", "count": 2})
        self.assertEqual(self.text(r)["total_price_cents"], 240)
        self.assertEqual(self.fake.calls[0], ("add", "1", 2))

    def test_invalid_arguments_are_tool_errors_not_calls(self):
        for name, args in [
            ("add_to_cart", {"product_id": "1", "count": 0}),
            ("add_to_cart", {"product_id": "1", "count": 51}),
            ("add_to_cart", {"product_id": "1", "count": True}),
            ("add_to_cart", {"product_id": ""}),
            ("search_products", {"query": "   "}),
            ("search_products", {"query": "x", "limit": 99}),
        ]:
            r = call(name, args)
            self.assertTrue(r["result"]["isError"], (name, args))
        self.assertEqual(self.fake.calls, [])

    def test_create_recipe_merges_duplicates_and_returns_the_id(self):
        r = call("create_recipe", {"name": " Haferbrei ", "portions": 3, "ingredients": [
            {"product_id": "s1", "quantity": 1}, {"product_id": "s2"}, {"product_id": "s1", "quantity": 2}]})
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(self.fake.calls[0], ("create_recipe", "Haferbrei", 3, {"s1": 3, "s2": 1}))
        out = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(out["recipe_id"], "5cd1376dad2843a998f6e114b078b1a9")
        self.assertEqual(out["ingredient_count"], 2)

    def test_create_recipe_validates_input_before_calling_picnic(self):
        good = [{"product_id": "s1"}]
        for args in [
            {"name": "", "ingredients": good},
            {"name": "x" * 81, "ingredients": good},
            {"name": "x", "portions": 0, "ingredients": good},
            {"name": "x", "portions": True, "ingredients": good},
            {"name": "x", "ingredients": []},
            {"name": "x", "ingredients": "s1"},
            {"name": "x", "ingredients": ["s1"]},
            {"name": "x", "ingredients": [{"product_id": ""}]},
            {"name": "x", "ingredients": [{"product_id": "s1", "quantity": 0}]},
            {"name": "x", "ingredients": [{"product_id": "s1"}] * 41},
        ]:
            self.assertTrue(call("create_recipe", args)["result"]["isError"], args)
        self.assertEqual(self.fake.calls, [])

    def test_delete_recipe_accepts_only_a_hex_id(self):
        rid = "5cd1376dad2843a998f6e114b078b1a9"
        self.assertFalse(call("delete_recipe", {"recipe_id": rid})["result"]["isError"])
        self.assertEqual(self.fake.calls, [("delete_recipe", rid)])
        for bad in ["", "../cart", "s1021989", 5]:
            self.assertTrue(call("delete_recipe", {"recipe_id": bad})["result"]["isError"], bad)
        self.assertEqual(len(self.fake.calls), 1)

    def test_unknown_tool_is_protocol_error(self):
        self.assertEqual(call("checkout", {})["error"]["code"], -32602)


class AuthTests(unittest.TestCase):
    def test_missing_session_points_to_the_login_command(self):
        env = {"PICNIC_TOKEN_FILE": "does-not-exist.json"}
        with mock.patch.object(server, "_client", None), mock.patch.dict("os.environ", env, clear=True):
            r = call("get_cart", {})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("picnic-mcp login", r["result"]["content"][0]["text"])

    def test_stored_token_creates_the_client(self):
        with mock.patch.object(server, "_client", None), \
                mock.patch.object(server, "load_token", return_value="saved"):
            client = server.get_client()
        self.assertEqual(client.token, "saved")

    def test_auth_failure_is_labelled(self):
        with mock.patch.object(server, "get_client", side_effect=PicnicAuthError("bad")):
            r = call("get_cart", {})
        self.assertTrue(r["result"]["content"][0]["text"].startswith("authentication error"))


if __name__ == "__main__":
    unittest.main()
