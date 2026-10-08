import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import style
import media
import offers


def sample_case():
    items = {}
    stamp = datetime.now(timezone.utc).isoformat()
    for iid, category, price in [("coat", "outerwear", 20000), ("jacket", "outerwear", 6350),
                                 ("blazer", "outerwear", 19990), ("wine", "top", 5990),
                                 ("navy", "top", 5790), ("shirt", "top", 3466),
                                 ("jeans", "bottom", 5791), ("boots", "shoes", 7169)]:
        items[iid] = {"name": iid, "category": category, "price": {"ordinary": price, "currency": "RUB"},
                      "url": f"https://shop.example/{iid}", "variant": "exact-size/color",
                      "variant_verified": True, "stock": {"available": True, "scope": "online", "quantity": 2},
                      "observed_at": stamp, "evidence": [{"path": "/example/quote.json"}],
                      "fit": {"status": "provisional", "note": "Try-on candidate", "unresolved": ["Shoulders missing"]},
                      "delivery": {"verified": False, "cost": None}}
    return {"request": {"language": "ru", "categories": ["outerwear", "top", "bottom", "shoes"],
                        "destination": "Test city", "shoe_policy": "nominal",
                        "budget": {"amount": 50000, "currency": "RUB", "scope": "per_outfit"}},
            "profile": {"measurements": [], "photos": []}, "items": items,
            "outfits": [{"id": "urban", "title": "Urban", "style_summary": "Coat with textured knit", "items": ["coat", "wine", "jeans", "boots"]},
                        {"id": "smart", "title": "Smart", "style_summary": "Blazer with stripe shirt", "items": ["blazer", "shirt", "jeans", "boots"]},
                        {"id": "casual", "title": "Casual", "style_summary": "Short jacket with navy knit", "items": ["jacket", "navy", "jeans", "boots"]}]}


class Measurements(unittest.TestCase):
    def test_confirmed_unusual_value_and_missing_help(self):
        case = sample_case()
        case["profile"]["measurements"] = [{"id": "top_length", "value": 60, "unit": "cm", "basis": "garment", "definition": "highest shoulder to hem", "source": "user", "confirmed": True}]
        result = style.prepare({"case": case})
        self.assertEqual(result["measurements"][0]["value"], 60)
        self.assertTrue(all(gap["how"] for gap in result["missing_measurements"]))
        self.assertNotIn("foot_length", {g["id"] for g in result["missing_measurements"]})
        self.assertEqual(result["missing_photos"], ["face", "body"])

    def test_missing_basis_is_not_guessed(self):
        case = sample_case()
        case["profile"]["measurements"] = [{"id": "chest", "value": 104, "unit": "cm", "definition": "chest", "source": "user"}]
        with self.assertRaises(ValueError):
            style.prepare({"case": case})

    def test_queries_are_separate_jobs(self):
        case = sample_case()
        case["criteria"] = [{"category": "top", "queries": ["navy knit", "wine knit"]}, {"category": "bottom", "queries": ["long jeans"]}]
        jobs = style.prepare({"case": case})["search_jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertEqual([j["category"] for j in jobs], ["top", "top", "bottom"])


class Fit(unittest.TestCase):
    def comparison(self):
        return {"id": "chest", "target": {"value": 104, "kind": "circumference", "basis": "body", "definition": "underarms", "source": "user"},
                "offer": {"value": 59, "kind": "flat_width", "basis": "garment", "definition": "underarms", "source": "model chart", "evidence_scope": "exact_variant"}, "ease_cm": 14}

    def test_explicit_flat_width_and_ease(self):
        result = style.fit_check({"comparisons": [self.comparison()]})
        self.assertEqual(result["comparisons"][0]["offer_cm"], 118)
        self.assertEqual(result["status"], "candidate")

    def test_body_garment_requires_ease_and_generic_chart_provisional(self):
        c = self.comparison(); c.pop("ease_cm")
        self.assertEqual(style.fit_check({"comparisons": [c]})["status"], "provisional")
        c = self.comparison(); c["offer"]["evidence_scope"] = "category_chart"
        self.assertEqual(style.fit_check({"comparisons": [c]})["status"], "provisional")

    def test_neck_and_shoulder_sleeves_not_comparable(self):
        c = self.comparison(); c["offer"]["definition"] = "neck centre to cuff"
        self.assertEqual(style.fit_check({"comparisons": [c]})["comparisons"][0]["status"], "unknown")

    def test_known_width_mismatch_not_erased(self):
        c = self.comparison(); c["offer"]["value"] = 50
        self.assertEqual(style.fit_check({"comparisons": [c]})["status"], "mismatch")


class Outfits(unittest.TestCase):
    def test_totals_shared_once_and_shipping_unknown(self):
        case = sample_case()
        result = style.outfits_check({"case": case})
        self.assertTrue(result["ready_for_shopping_renders"])
        self.assertEqual(result["outfits"][0]["goods_by_currency"], {"RUB": 38950})
        self.assertEqual(result["outfits"][2]["goods_by_currency"], {"RUB": 25100})
        self.assertEqual(result["capsule"]["goods_by_currency"], {"RUB": 74546})
        self.assertFalse(result["capsule"]["delivery_included"])
        self.assertIn("delivery_unverified", {w["code"] for w in result["warnings"]})

    def test_unavailable_and_physical_store_variants_block(self):
        case = sample_case(); case["items"]["coat"]["stock"]["available"] = False
        self.assertFalse(style.outfits_check({"case": case})["ready_for_shopping_renders"])
        case = sample_case(); case["items"]["coat"]["stock"]["scope"] = "local_store"
        self.assertFalse(style.outfits_check({"case": case})["ready_for_shopping_renders"])

    def test_conditional_price_not_total_and_size_price_changes(self):
        case = sample_case(); case["items"]["boots"]["price"]["conditional"] = [{"amount": 1}]
        first = style.outfits_check({"case": case})["outfits"][0]["goods_by_currency"]["RUB"]
        case["items"]["boots"]["price"]["ordinary"] = 11907
        second = style.outfits_check({"case": case})["outfits"][0]["goods_by_currency"]["RUB"]
        self.assertEqual(second - first, 4738)

    def test_currency_unknown_and_scope_preserved(self):
        case = sample_case(); case["items"]["boots"]["price"]["currency"] = "GBP"
        result = style.outfits_check({"case": case})
        self.assertFalse(result["outfits"][0]["single_currency_complete"])
        self.assertEqual(result["budget"]["assessments"][0]["status"], "unknown")
        case = sample_case(); case["request"]["budget"]["scope"] = "capsule"
        self.assertIn("goods_over_budget", {e["code"] for e in style.outfits_check({"case": case})["errors"]})

    def test_cart_cannot_be_called(self):
        with self.assertRaises(ValueError):
            style.marketplace_call({"request": {"method": "cart.apply", "params": {}}})


class Media(unittest.TestCase):
    def test_render_requests_identity_and_maximum_references(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "photo.png"; Image.new("RGB", (100, 200), "grey").save(photo)
            case = sample_case()
            case["profile"]["photos"] = [{"role": "face", "path": str(photo)}, {"role": "body", "path": str(photo)}]
            for item in case["items"].values():
                item["image_path"] = str(photo)
            result = media.render_prepare({"case": case, "outfit_id": "urban", "directory": str(root / "input"), "anchor": str(photo)})
            self.assertLessEqual(len(result["request"]["referenced_image_paths"]), 5)
            self.assertFalse(result["generator_called"])
            self.assertIn("Do not slim", result["request"]["prompt"])
            self.assertTrue(Path(result["reference_boards"][0]).is_file())

    def test_missing_photo_does_not_create_generic_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                media.render_prepare({"case": sample_case(), "outfit_id": "urban", "directory": tmp})

    def test_report_links_escaping_and_no_fake_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = sample_case(); case["items"]["coat"]["name"] = '<script>alert("x")</script>'
            result = media.report({"case": case, "directory": tmp})
            self.assertFalse(result["complete"])
            html = Path(result["gallery"]).read_text()
            self.assertIn("&lt;script&gt;", html)
            self.assertNotIn('<script>alert', html)
            self.assertIn("https://shop.example/coat", html)
            self.assertIn("38\u202f950 RUB", html)


class OfferImport(unittest.TestCase):
    def envelope(self, ref="ozon:1"):
        return {"ok": True, "method": "product", "observed_at": "2026-10-08T10:00:00+00:00",
                "data": {"product": {"provider": "ozon", "ref": ref, "title": "Exact shoes", "url": "https://shop.example/size45",
                                      "stage": "product", "variant_verified": True, "options": {"Size": ["45"]}, "available": True,
                                      "price": {"unit": "11907", "currency": "RUB", "conditional": {"unit": "7900", "condition": "bank"}},
                                      "delivery": {"verified": False, "cost": None}, "warnings": []}}}

    def test_import_preserves_exact_price_and_unknown_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "product.json"; path.write_text(json.dumps(self.envelope()))
            result = offers.import_offer({"input": str(path), "id": "boots", "category": "shoes"})
            self.assertEqual(result["item"]["price"]["ordinary"], 11907)
            self.assertIsNone(result["item"]["delivery"]["cost"])
            self.assertEqual(result["item"]["fit"]["status"], "provisional")

    def test_wrong_sku_quote_and_search_snippet_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "product.json"; q = Path(tmp) / "quote.json"
            p.write_text(json.dumps(self.envelope()))
            q.write_text(json.dumps(self.envelope("ozon:2")))
            with self.assertRaises(ValueError):
                offers.import_offer({"input": str(p), "quote": str(q), "id": "boots", "category": "shoes"})
            search = self.envelope(); search["method"] = "search"; p.write_text(json.dumps(search))
            with self.assertRaises(ValueError):
                offers.import_offer({"input": str(p), "id": "boots", "category": "shoes"})


if __name__ == "__main__":
    unittest.main()
