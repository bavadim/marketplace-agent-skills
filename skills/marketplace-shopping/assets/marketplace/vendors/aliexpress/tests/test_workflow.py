#!/usr/bin/env python3
"""Offline behavioral tests; all product data is synthetic, no external mutations."""
import copy
import tempfile
import unittest
from pathlib import Path
import bom
import vision


def card():
    values = {"variant": {"options": {"Resistance": "10k", "Pack": "100pcs"}},
              "pack_size": 100, "unit": "pcs", "price_per_pack": "125.05",
              "currency": "RUB", "resistance_ohm": 10000}
    return {"product_id": "1000000000000", "url": "https://aliexpress.ru/item/1000000000000.html",
            "observed_at": "2026-10-06T20:00:00+00:00", "status": "verified", "unresolved": [],
            "facts": {k: {"value": v, "verified": True, "evidence_ids": ["d1"]} for k, v in values.items()},
            "evidence": [{"id": "d1", "method": "dom", "text": "Synthetic fixture, not a live product"}]}


def row(id="R1", quantity=120):
    return {"id": id, "quantity": quantity, "unit": "pcs", "required_facts": {"resistance_ohm": 10000}}


def cart(items):
    return {"complete": True, "items": [{"sku_key": key, "quantity": qty,
            "verified": True, "evidence": "synthetic fixture"} for key, qty in items]}


class Planning(unittest.TestCase):
    def test_rounds_up_after_aggregating_duplicate_skus(self):
        result = bom.plan({"items": [row(quantity=60), row("R2", 60)]},
                          [{"row_id": id, "product": card()} for id in ("R1", "R2")])
        self.assertEqual(result["status"], "ready_for_cart_review")
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["purchase_quantity"], 2)
        self.assertEqual(result["lines"][0]["surplus_units"], 80)
        self.assertEqual(result["goods_subtotal"], "250.10")
        self.assertIsNone(result["order_total"])

    def test_mismatch_and_missing_facts_block(self):
        for value in (1000, "10000", None):
            c = card(); c["facts"]["resistance_ohm"]["value"] = value
            result = bom.plan({"items": [row()]}, [{"row_id": "R1", "product": c}])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["lines"], [])

    def test_unverified_ocr_and_missing_evidence_block(self):
        for mutation in (lambda c: c["facts"]["pack_size"].update(verified=False),
                         lambda c: c["evidence"][0].update(method="ocr"),
                         lambda c: c.update(evidence=[]),
                         lambda c: c.update(unresolved=["two incompatible pack sizes"])):
            c = card(); mutation(c)
            self.assertEqual(bom.plan({"items": [row()]}, [{"row_id": "R1", "product": c}])["status"], "blocked")

    def test_unknown_and_duplicate_rows_rejected(self):
        with self.assertRaises(ValueError): bom.plan({"items": [row(), row()]}, [])
        with self.assertRaises(ValueError): bom.plan({"items": [row()]}, [{"row_id": "unknown", "product": card()}])
        self.assertEqual(bom.plan({"items": [row()]}, [])["status"], "blocked")

    def test_prices_and_pack_counts_reject_nonfinite_or_boolean(self):
        for value in ("NaN", "Infinity", "-1", "1.001", 1.25):
            with self.assertRaises(ValueError): bom.money(value)
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError): bom.positive_int(value, "quantity")

    def test_variant_identity_and_no_options(self):
        a, b = card(), card()
        b["facts"]["variant"]["value"]["options"]["Pack"] = "50pcs"
        self.assertNotEqual(bom.sku_key(a), bom.sku_key(b))
        a["facts"]["variant"]["value"] = {"options": {}}
        with self.assertRaises(ValueError): bom.sku_key(a)
        a["facts"]["variant"]["value"]["no_options"] = True
        self.assertTrue(bom.sku_key(a))

    def test_budget_is_goods_only(self):
        result = bom.plan({"items": [row()], "goods_budget": "200.00"}, [{"row_id": "R1", "product": card()}])
        self.assertEqual(result["status"], "blocked")

    def test_normalize_does_not_invent_a_variant_price(self):
        result = bom.normalize({"url": card()["url"], "heading": ["Test"], "text": "от 2 ₽"})
        self.assertEqual(result["facts"], {})
        self.assertEqual(result["status"], "needs_review")
        with self.assertRaises(ValueError): bom.canonical_product("https://aliexpress.ru.evil.test/item/1.html")

    def test_cart_is_idempotent_and_checks_unrelated_items(self):
        plan = bom.plan({"items": [row()]}, [{"row_id": "R1", "product": card()}])
        key = plan["lines"][0]["sku_key"]
        before = cart([(key, 1), ("other", 3)])
        correct = bom.verify_cart(plan, before, cart([(key, 2), ("other", 3)]))
        self.assertEqual(correct["status"], "verified")
        self.assertEqual(correct["lines"][0]["add_quantity"], 1)
        self.assertEqual(bom.verify_cart(plan, before, cart([(key, 3), ("other", 3)]))["status"], "mismatch")
        self.assertEqual(bom.verify_cart(plan, before, cart([(key, 2)]))["unrelated_changed"], ["other"])
        after = cart([(key, 2), ("other", 3)])
        self.assertEqual(bom.verify_cart(plan, after, after)["lines"][0]["add_quantity"], 0)
        with self.assertRaises(ValueError): bom.verify_cart(plan, {"complete": False, "items": []}, after)

    def test_conflicting_sku_observations_block(self):
        a, b = card(), card(); b["facts"]["pack_size"]["value"] = 50
        result = bom.plan({"items": [row(), row("R2")]}, [{"row_id": "R1", "product": a}, {"row_id": "R2", "product": b}])
        self.assertEqual(result["status"], "blocked")


class Images(unittest.TestCase):
    def test_tiles_cover_full_image_without_resizing(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "description.png"
            Image.new("RGB", (800, 3100), "white").save(path)
            result = vision.prepare(path, Path(tmp) / "tiles", 1400, 160)
            self.assertEqual(len(result["tiles"]), 3)
            self.assertEqual(result["tiles"][0]["bbox"], [0, 0, 800, 1400])
            self.assertEqual(result["tiles"][-1]["bbox"][-1], 3100)
            for item in result["tiles"]:
                with Image.open(item["path"]) as tile:
                    self.assertEqual(tile.width, 800)
        with self.assertRaises(ValueError): vision.prepare("unused", "unused", 500, 500)


if __name__ == "__main__": unittest.main(verbosity=2)
