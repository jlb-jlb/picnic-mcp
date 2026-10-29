import hashlib
import io
import json
import unittest
import urllib.error

from picnic_mcp.client import (
    PicnicAuthError,
    PicnicClient,
    PicnicError,
    flatten_search,
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

    def test_search_term_is_url_encoded(self):
        op = FakeOpener(FakeResponse([]))
        PicnicClient(opener=op).search("hähnchen & reis")
        self.assertIn("search_term=h%C3%A4hnchen+%26+reis", op.requests[0].full_url)

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

    def test_country_selects_host_and_rejects_unknown(self):
        op = FakeOpener(FakeResponse({}))
        PicnicClient(country="DE", opener=op).get_cart()
        self.assertIn("storefront-prod.de.", op.requests[0].full_url)
        with self.assertRaises(ValueError):
            PicnicClient(country="xx")


class HelperTests(unittest.TestCase):
    def test_flatten_search_finds_nested_articles_and_respects_limit(self):
        payload = [
            {
                "type": "CATEGORY",
                "items": [
                    {"type": "SINGLE_ARTICLE", "id": "1", "name": "Milk", "display_price": 99,
                     "unit_quantity": "1 l", "unit_quantity_sub": "0.99/l"},
                    {"type": "SINGLE_ARTICLE", "id": "2", "name": "Oats", "price": 150},
                    {"type": "SINGLE_ARTICLE", "id": "3", "name": "Rice", "price": 200},
                ],
            }
        ]
        out = flatten_search(payload, limit=2)
        self.assertEqual([a["id"] for a in out], ["1", "2"])
        self.assertEqual(out[0]["price_cents"], 99)
        self.assertEqual(out[1]["price_cents"], 150)

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
