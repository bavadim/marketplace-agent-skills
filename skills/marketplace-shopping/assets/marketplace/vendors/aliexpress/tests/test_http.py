#!/usr/bin/env python3
"""Offline parser, transport and scope tests; synthetic data, no user session."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ali_http as ali

URL = "https://aliexpress.ru/item/123456789.html"
IMAGE = "https://ae-pic-a1.aliexpress-media.com/kf/example.jpg"


def fixture():
    return {"data": {"productsFeed": {"products": [], "productsV2": [{"snippetContainer": {
        "itemData": {"pdpInfo": {"url": URL + "?sku_id=123&tracking=PRIVATE",
            "preloadedData": {"title": "Test 0603 resistor", "rating": "4.9", "salesCount": "20 купили",
                "imageUrl": "https://malformed-image.jpg", "price": {"value": 123.50, "currency": "RUB", "displayPrice": "123,50 ₽"},
                "store": {"name": "Example", "url": "https://aliexpress.ru/store/123?tracking=PRIVATE"}}},
            "properties": {"id": "123456789", "preselectSkuId": "123", "isFavorite": True},
            "trackingInfo": {"visitor": "PRIVATE"}},
        "presentations": [{"gallery": {"images": [IMAGE, IMAGE, "https://evil.test/track", "https://malformed-image.jpg"]}}]
    }}]}, "pagination": {"totalPages": 3, "currentPage": 1}, "errors": []}}


class HTTPTests(unittest.TestCase):
    def test_store_rating_is_distinct_from_product_rating_and_private_context(self):
        props = {"shopUrl": "https://aliexpress.ru/store/123", "name": "Store",
                 "buyer": {"name": "PRIVATE"}, "sellerStats": {
                     "sellerRating": 96.7, "productsRating": 4.9, "reviewsCount": 800,
                     "averageShippingTimeDays": 2.5, "unknown": "PRIVATE"}}
        state = {"widgets": [{"widgetId": "bx/SnowShopContext/1.21.0", "props": props}],
                 "account": "PRIVATE"}
        body = ('<script id="__AER_DATA__">' + json.dumps(state) + '</script>').encode()
        result = ali.parse_store(body, "123")
        self.assertEqual(result["seller"]["rating"], 96.7)
        self.assertEqual(result["seller"]["scale"], 100)
        self.assertIsNone(result["seller"]["review_count"])
        self.assertNotIn("delivery", result)
        self.assertNotIn("PRIVATE", json.dumps(result))
        with self.assertRaises(ali.ReadFailure):
            ali.parse_store(body, "999")

    def parse(self, payload):
        return ali.parse_search(payload, "test", "https://aliexpress.ru/wholesale?SearchText=test", ali.now())

    def test_projection_omits_tracking_and_keeps_facts_unverified(self):
        result = self.parse(fixture())
        card = result["products"][0]
        self.assertEqual(result["count"], 1)
        self.assertEqual(card["listing"]["price"]["value"], "123.5")
        self.assertEqual(card["listing"]["preselected_sku_id"], "123")
        self.assertEqual(card["facts"], {})
        self.assertEqual(card["status"], "needs_review")
        self.assertFalse(card["listing"]["price"]["selected_variant_verified"])
        self.assertEqual([i["url"] for i in card["images"]], [IMAGE])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("isFavorite", json.dumps(result))

    def test_feed_change_is_not_empty_success(self):
        for payload in ({"error": "failed"}, {"data": {"productsFeed": {"products": []}}}):
            with self.assertRaises(ali.ReadFailure):
                self.parse(payload)

    def test_batch_shortlist_preserves_images_for_selected_product(self):
        card = self.parse(fixture())["products"][0]
        other = {"product_id": "222", "images": []}
        batch = {"rows": [{"candidates": [other, card]}, {"candidates": [card]}]}
        with tempfile.TemporaryDirectory() as tmp, patch("ali_http.Client.fetch", side_effect=ali.ReadFailure("transport_error", "offline")):
            with ali.Client(use_saved_session=False) as client:
                result = ali.download_images(client, batch, card["product_id"], 1, Path(tmp) / "images.json")
                detail = ali.product(client, URL, batch)
        self.assertEqual(result["product_id"], card["product_id"])
        self.assertEqual(result["images"][0]["url"], IMAGE)
        self.assertEqual(detail["title"], card["title"])
        self.assertEqual(detail["images"], card["images"])
        self.assertEqual(detail["detail_status"], "transport_error")

    def test_download_preserves_option_image_sku_attribution(self):
        card={"product_id":"123456789","images":[{"url":IMAGE,"role":"sku_option","sku_ids":["321"]}]}
        with tempfile.TemporaryDirectory() as tmp, patch("vision.prepare",return_value={"tiles":[]}), \
                patch("ali_http.Client.fetch",return_value=(b"synthetic image",{})):
            with ali.Client(use_saved_session=False) as client:
                result=ali.download_images(client,card,"123456789",1,Path(tmp)/"images.json")
        self.assertEqual(result["images"][0]["sku_ids"],["321"])
        self.assertEqual(result["images"][0]["role"],"sku_option")

    def test_malformed_entry_marks_partial(self):
        data = fixture()
        data["data"]["productsFeed"]["productsV2"].append({"otherWidget": {}})
        result = self.parse(data)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["skipped_entries"], 1)

    def test_empty_with_errors_is_failure_not_no_products(self):
        data = fixture()
        data["data"]["productsFeed"]["productsV2"] = []
        with self.assertRaises(ali.ReadFailure):
            self.parse(data)
        data["data"]["pagination"]["totalPages"] = 0
        self.assertEqual(self.parse(data)["status"], "empty")
        data["data"]["errors"] = [{"error": "timeout", "level": 2}]
        with self.assertRaises(ali.ReadFailure):
            self.parse(data)

    def test_challenge_does_not_match_ordinary_module_reference(self):
        self.assertTrue(ali.challenge('<script>window._config_ = {"action":"captcha"};</script>'))
        self.assertFalse(ali.challenge('<script src="/assets/captcha.js"></script>'))

    def test_only_approved_image_domains(self):
        self.assertTrue(ali.image_allowed(IMAGE))
        for url in ("file:///etc/passwd", "https://localhost/x", "https://aliexpress-media.com.evil.test/x",
                    "https://u:p@ae-pic-a1.aliexpress-media.com/x", "https://ae-pic-a1.aliexpress-media.com:444/x",
                    "https://malformed-image.jpg", None):
            self.assertFalse(ali.image_allowed(url))

    def fake_run(self, responses, records):
        def run(args, **kwargs):
            records.append((args, kwargs))
            status, headers, body, exitcode = responses.pop(0)
            Path(args[args.index("--dump-header") + 1]).write_text(f"HTTP/2 {status}\r\n" + headers + "\r\n")
            Path(args[args.index("--output") + 1]).write_bytes(body)
            return subprocess.CompletedProcess(args, exitcode, f"{status}\t0.1\t2\t192.0.2.1\t0\t0", "PRIVATE")
        return run

    def test_http_200_captcha_is_blocked_and_no_retry(self):
        records = []
        body = b'<script>window._config_={"action":"captcha","url":"SECRET"}</script>'
        with ali.LegacyHTTPClient(use_saved_session=False) as client, patch("ali_http.subprocess.run", self.fake_run([(200, "", body, 0)], records)):
            card = ali.product(client, URL, self.parse(fixture()))
        self.assertEqual(card["detail_status"], "requires_browser_verification")
        self.assertEqual(card["title"], "Test 0603 resistor")
        self.assertEqual(len(records), 1)
        self.assertNotIn("SECRET", json.dumps(card))

    def test_image_request_never_contains_cookie_flags(self):
        records = []
        with ali.LegacyHTTPClient(use_saved_session=False) as client, patch("ali_http.subprocess.run", self.fake_run([(200, "", b"image", 0)], records)):
            client.fetch(IMAGE, asset=True)
        args, kwargs = records[0]
        self.assertNotIn("--cookie", args)
        self.assertNotIn("--cookie-jar", args)
        self.assertEqual(args[args.index("--proxy") + 1], "")

    def test_redirect_outside_scope_not_followed(self):
        records = []
        with ali.LegacyHTTPClient(use_saved_session=False) as client, patch("ali_http.subprocess.run", self.fake_run([(302, "Location: https://evil.test/?PRIVATE\r\n", b"", 0)], records)):
            with self.assertRaises(ali.ReadFailure):
                client.fetch(URL)
        self.assertEqual(len(records), 1)

    def test_bodyless_post_stays_post_and_redirect_is_not_replayed(self):
        records = []
        with ali.LegacyHTTPClient(use_saved_session=False) as client, patch("ali_http.subprocess.run", self.fake_run([(302, "Location: https://aliexpress.ru/cart\r\n", b"", 0)], records)):
            with self.assertRaises(ali.ReadFailure):
                client.fetch(URL, method="POST")
        self.assertEqual(len(records), 1)
        args = records[0][0]
        self.assertEqual(args[args.index("--request") + 1], "POST")
        self.assertNotIn("--data-binary", args)

    def test_timeout_partial_200_not_accepted(self):
        records = []
        with ali.LegacyHTTPClient(use_saved_session=False) as client, patch("ali_http.subprocess.run", self.fake_run([(200, "", b"partial", 28)], records)):
            with self.assertRaises(ali.ReadFailure) as error:
                client.fetch(URL)
        self.assertEqual(error.exception.status, "transport_error")

    def test_static_product_description_does_not_verify_variant(self):
        raw = ('<script type="application/ld+json">' + json.dumps({"@type": "Product", "name": "Example",
               "description": "10k ±1%", "image": IMAGE}) + '</script>').encode()
        records = []
        with ali.LegacyHTTPClient(use_saved_session=False) as client, patch("ali_http.subprocess.run", self.fake_run([(200, "", raw, 0)], records)):
            card = ali.product(client, URL)
        self.assertEqual(card["detail_status"], "structured_data_only")
        self.assertEqual(card["structured_listing"]["description"], "10k ±1%")
        self.assertEqual(card["facts"], {})
        self.assertEqual(card["status"], "needs_review")

    def test_anonymous_export_is_not_login(self):
        self.assertFalse(ali.login_state({"widgets": [{"props": {"loginStatus": "ANONYMOUS", "isLoggedIn": False}}]}))
        self.assertIsNone(ali.login_state({}))

    def test_current_auth_widget_status_wins_over_legacy_boolean(self):
        self.assertTrue(ali.login_state({"loginStatusV2": "LOGGED_IN", "isLoggedIn": False}))
        self.assertFalse(ali.login_state({"loginStatusV2": "ANONYMOUS", "isLoggedIn": True}))
        self.assertIsNone(ali.login_state({"loginStatusV2": "AUTO_LOGIN", "isLoggedIn": True}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
