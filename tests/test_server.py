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

    def test_unknown_tool_is_protocol_error(self):
        self.assertEqual(call("checkout", {})["error"]["code"], -32602)


class AuthTests(unittest.TestCase):
    def test_missing_credentials_reported_as_tool_error(self):
        with mock.patch.object(server, "_client", None), mock.patch.dict("os.environ", {}, clear=True):
            r = call("get_cart", {})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("PICNIC_USER", r["result"]["content"][0]["text"])

    def test_auth_failure_is_labelled(self):
        with mock.patch.object(server, "get_client", side_effect=PicnicAuthError("bad")):
            r = call("get_cart", {})
        self.assertTrue(r["result"]["content"][0]["text"].startswith("authentication error"))


if __name__ == "__main__":
    unittest.main()
