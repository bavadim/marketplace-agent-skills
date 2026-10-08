#!/usr/bin/env python3
"""Offline end-to-end procurement behavior; all products, sellers and carts synthetic."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ali_cart as cart
from ali_http import ReadFailure
import batch_search
import bom
import cart_plan
import shopping
from test_cart import raw_item


def request():
    return {"currency": "RUB", "destination_key": "test-city", "items": [
        {"id": "R1", "query": "synthetic controller", "quantity": 2, "unit": "pcs",
         "required_facts": {"voltage_v": "24", "cable_included": True}}]}


def offer(index=1, cost="100.00", days=10, pack=1, row_id="R1"):
    current = dt.datetime.now(dt.timezone.utc)
    pid, sku = str(10000 + index), str(20000 + index)
    values = {"variant": {"options": {"Комплектация": "Контроллер с кабелем"}}, "pack_size": pack,
        "unit": "pcs", "price_per_pack": cost, "currency": "RUB", "voltage_v": "24", "cable_included": True,
        "matches_request": True, "sku_id": sku, "source_id": 0, "package_contents": ["Контроллер", "Кабель"],
        "seller": {"id": str(index), "name": "Synthetic seller", "rating_kind": "seller", "rating": "4.8", "scale": "5", "review_count": 120},
        "delivery": {"destination_key": "test-city", "sku_id": sku, "quantity": (2 + pack - 1) // pack,
                     "earliest": (current.date()+dt.timedelta(days=days-1)).isoformat(),
                     "latest": (current.date()+dt.timedelta(days=days)).isoformat(), "cost": "0.00", "currency": "RUB",
                     "method": "Synthetic standard delivery", "is_default": True}}
    card = {"product_id": pid, "url": f"https://aliexpress.ru/item/{pid}.html", "title": "Synthetic controller",
            "observed_at": current.isoformat(), "status": "verified", "unresolved": [],
            "facts": {k: {"value": v, "verified": True, "evidence_ids": ["fixture"]} for k, v in values.items()},
            "evidence": [{"id": "fixture", "method": "dom", "text": "Synthetic evidence for offline test, not a live page"}]}
    return {"offer_id": row_id + "-offer" + str(index), "row_id": row_id, "product": card}


def offers():
    return [offer(1, "100.00", 10), offer(2, "110.00", 2), offer(3, "105.00", 5), offer(4, "1000.00", 1)]


def choose(data=None):
    data = data or offers()
    return shopping.compile_plan(request(), data, [{"row_id": "R1", "offer_id": "R1-offer2", "reason": "Fastest within reasonable delivered price band"}])


class ShoppingTests(unittest.TestCase):
    def test_one_seller_warns_without_fabricated_median_and_strict_minimum_still_blocks(self):
        data=[offer()]
        result=shopping.compare(request(),data)['rows'][0]
        self.assertEqual(result['offers'][0]['status'],'eligible')
        self.assertIn('limited_independent_price_comparisons',result['offers'][0]['warnings'])
        strict=request();strict['policy']={'min_independent_sellers':3}
        self.assertIn('insufficient_independent_price_comparisons', shopping.compare(strict,data)['rows'][0]['offers'][0]['blockers'])

    def test_tenfold_placeholder_excluded_fast_reasonable_offer_recommended(self):
        result = shopping.compare(request(), offers())
        row = result["rows"][0]
        self.assertEqual(row["recommended_offer_id"], "R1-offer2")
        self.assertIn("overpriced_outlier", row["offers"][3]["blockers"])

    def test_fast_delivery_outside_premium_band_does_not_win(self):
        data = offers()
        data[1]["product"]["facts"]["price_per_pack"]["value"] = "200.00"
        self.assertEqual(shopping.compare(request(), data)["rows"][0]["recommended_offer_id"], "R1-offer3")

    def test_comparison_uses_pack_unit_price_and_shipping(self):
        data = [offer(1, "100.00", 2, pack=100), offer(2, "1.00", 3), offer(3, "1.05", 4)]
        result = shopping.compare(request(), data)["rows"][0]
        self.assertEqual(result["offers"][0]["unit_price"], "1.00")
        self.assertNotIn("overpriced_outlier", result["offers"][0]["blockers"])
        self.assertEqual(result["recommended_offer_id"], "R1-offer2")

    def test_multiple_listings_from_one_seller_are_not_independent(self):
        data = offers()[:3]
        for o in data: o["product"]["facts"]["seller"]["value"]["id"] = "same-shop"
        strict=request(); strict['policy']={'min_independent_sellers':3}
        result = shopping.compare(strict, data)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["rows"][0]["independent_sellers"], 1)

    def test_unknown_destination_and_product_rating_cannot_pass(self):
        r = request(); r.pop("destination_key")
        with self.assertRaises(ValueError): shopping.compare(r, offers())
        data = offers()
        data[1]["product"]["facts"]["seller"]["value"]["rating_kind"] = "product"
        result = shopping.compare(request(), data)["rows"][0]["offers"][1]
        self.assertIn("seller_rating_required_not_product_rating", result["blockers"])

    def test_wrong_voltage_configuration_and_unverified_ocr_rejected(self):
        for edit in (lambda c: c["facts"]["voltage_v"].update(value="12"),
                     lambda c: c["facts"]["cable_included"].update(value=False),
                     lambda c: c["evidence"][0].update(method="ocr")):
            data = offers(); edit(data[1]["product"])
            self.assertNotEqual(shopping.compare(request(), data)["rows"][0]["offers"][1]["status"], "eligible")

    def test_delivery_for_wrong_city_or_other_sku_rejected(self):
        for key,value in (("destination_key", "another-city"), ("sku_id", "wrong"), ("quantity", 20)):
            data = offers(); data[1]["product"]["facts"]["delivery"]["value"][key] = value
            self.assertNotEqual(shopping.compare(request(), data)["rows"][0]["offers"][1]["status"], "eligible")

    def test_deadline_and_budget_enforced(self):
        r = request(); r["deliver_by"] = dt.datetime.now(dt.timezone.utc).date().isoformat()
        self.assertEqual(shopping.compare(r, offers())["status"], "needs_review")
        r = request(); r["total_budget"] = "200.00"
        result = shopping.compile_plan(r, offers(), [{"row_id": "R1", "offer_id": "R1-offer2", "reason": "test"}])
        self.assertEqual(result["status"], "blocked")
        self.assertIn({"reason": "total_budget_exceeded"}, result["blockers"])

    def test_unknown_review_count_is_warning_not_invented_zero(self):
        data = offers(); data[1]["product"]["facts"]["seller"]["value"]["review_count"] = None
        item = shopping.compare(request(), data)["rows"][0]["offers"][1]
        self.assertEqual(item["status"], "eligible")
        self.assertTrue(item["warnings"])

    def test_stale_facts_do_not_get_new_lifetime_by_compiling(self):
        data = offers()
        old = dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=11)
        data[1]["product"]["observed_at"] = old.isoformat()
        result = choose(data)
        self.assertLess(shopping.timestamp(result["expires_at"]), dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2))
        data[1]["product"]["observed_at"] = (old-dt.timedelta(hours=3)).isoformat()
        self.assertEqual(choose(data)["status"], "blocked")

    def test_split_market_does_not_choose_placeholder_majority(self):
        result = shopping.compare(request(), [offer(1,"100.00"),offer(2,"1000.00"),offer(3,"1100.00")])
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["rows"][0]["recommended_offer_id"])

    def test_duplicate_bom_rows_aggregate_and_need_combined_shipping_quote(self):
        r = request(); r["items"][0]["quantity"] = 60
        r["items"].append({**r["items"][0], "id": "R2"})
        data = []
        for row_id in ("R1", "R2"):
            for index in (1,2,3):
                o = offer(index, "100.00", pack=100, row_id=row_id)
                data.append(o)
        selected = [{"row_id": row_id, "offer_id": row_id+"-offer1", "reason": "same component"} for row_id in ("R1","R2")]
        self.assertEqual(shopping.compile_plan(r,data,selected)["status"], "blocked")
        for o in data:
            f=copy.deepcopy(o["product"]["facts"]["delivery"]);f["value"]["quantity"]=2
            o["product"]["facts"]["cart_delivery"]=f
        plan = shopping.compile_plan(r,data,selected)
        self.assertEqual(plan["status"], "ready_for_cart")
        self.assertEqual(len(plan["lines"]), 1)
        self.assertEqual(plan["lines"][0]["purchase_quantity"], 2)

    def test_binding_live_sku_does_not_verify_technical_suitability(self):
        data = [offer()]
        catalog = {"status":"ok", "product_id":"10001", "source_id":0, "observed_at":dt.datetime.now(dt.timezone.utc).isoformat(),
            "source":{"url":"https://aliexpress.ru/aer-api/v3/bx/pdp/webProductToCart?productId=10001","response_sha256":"a"*64},
            "skus":[{"sku_id":"20001","sku_attr":"1:2", "options":[{"name":"Комплектация","value":"Контроллер с кабелем"}],
                     "price":{"value":"99.00","currency":"RUB"}}]}
        result = shopping.bind(data,"R1-offer1",catalog,"20001")[0]["product"]
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(bom.fact(result,"price_per_pack"), "99.00")
        catalog["source"]["response_sha256"]="bad"
        with self.assertRaises(ValueError):shopping.bind(data,"R1-offer1",catalog,"20001")

    def test_review_packet_generates_fact_wrappers_and_binds_to_sku(self):
        packet={"sku_id":"20001","observed_at":dt.datetime.now(dt.timezone.utc).isoformat(),
            "review_complete":True,"unresolved":[],"observations":[{"id":"spec","method":"dom","text":"24V, cable included",
                "reviewed":True,"facts":{"voltage_v":"24","cable_included":True}}]}
        updated=shopping.enrich([offer()],"R1-offer1",packet)[0]["product"]
        self.assertTrue(bom.fact(updated,"cable_included"))
        packet["sku_id"]="other"
        with self.assertRaises(ValueError):shopping.enrich([offer()],"R1-offer1",packet)

    def test_ocr_or_unreviewed_packet_never_verifies_a_fact(self):
        packet={"sku_id":"20001","observed_at":dt.datetime.now(dt.timezone.utc).isoformat(),
            "review_complete":True,"unresolved":[],"observations":[{"method":"ocr","text":"24V","reviewed":True,"facts":{"voltage_v":"24"}}]}
        with self.assertRaises(ValueError):shopping.enrich([offer()],"R1-offer1",packet)


class SimulatedCart:
    def __init__(self, plan, items=None):
        self.plan=plan
        self.items=copy.deepcopy(items or [])
        self.sent=[]
        self.raise_after_apply=False
        self.extra_price="0.00"

    def read(self):
        return {"authenticated":True,"complete":True,"status":"ok","items":copy.deepcopy(self.items)}

    def skus(self, product_id, source_id):
        from decimal import Decimal
        line=next(l for l in self.plan["lines"] if l["product_id"]==product_id)
        count=sum(x["quantity"] for x in self.items if cart.sku_key(x)==cart.sku_key(line))
        return {"product_id":product_id,"source_id":source_id,"title":line["title"],"skus":[{
            "sku_id":line["sku_id"],"quantity_in_cart":count,"available_quantity":100,"sku_attr":"synthetic",
            "options":[{"name":k,"value":v} for k,v in line["variant"]["options"].items()],
            "price":{"value":str(Decimal(line["max_unit_price"])+Decimal(self.extra_price)),"currency":line["currency"]}}]}

    def mutate(self, endpoint, payload):
        self.sent.append((endpoint,payload))
        line=next(l for l in self.plan["lines"] if l["product_id"]==payload["productId"])
        if endpoint==cart.ADD:
            raw=raw_item("added-"+payload["skuId"],payload["productId"],payload["skuId"],payload["quantity"])
            raw["price"]["value"]=line["max_unit_price"]
            self.items.append(cart.normalize_item(raw))
        elif endpoint==cart.UPDATE:
            for item in self.items:
                if cart.sku_key(item)==cart.sku_key(line):item["quantity"]=payload["quantity"]
        else:raise AssertionError("Batch must never remove existing items")
        if self.raise_after_apply:raise KeyboardInterrupt("simulated interruption after server applied request")
        return {"id":"synthetic"}


class CartPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.plan=choose();self.root=Path(self.tmp.name)

    def apply(self,api,**kwargs):
        return cart_plan.apply(api,self.plan,self.root,"shopping-test","session-digest",**kwargs)

    def test_complete_workflow_places_correct_variant_and_returns_cart_url(self):
        api=SimulatedCart(self.plan)
        result=self.apply(api)
        self.assertEqual(result["status"],"cart_verified")
        self.assertEqual(result["cart_url"],"https://aliexpress.ru/cart")
        self.assertEqual(result["lines"][0]["actual_packs"],2)
        self.assertEqual(api.sent[0][1]["skuId"],"20002")
        self.assertEqual(self.apply(api)["status"],"cart_verified")
        self.assertEqual(len(api.sent),1)

    def test_existing_quantity_counts_toward_goal_and_other_rows_untouched(self):
        raw=raw_item("existing","10002","20002",1,False);raw["price"]["value"]="110.00"
        unrelated=cart.normalize_item(raw_item("other","99999","99998",5))
        api=SimulatedCart(self.plan,[cart.normalize_item(raw),unrelated])
        result=self.apply(api)
        self.assertEqual(result["status"],"cart_verified")
        self.assertEqual(result["lines"][0]["added_packs"],1)
        self.assertEqual(api.items[-1],unrelated)
        self.assertFalse(api.items[0]["selected"])

    def test_excess_existing_quantity_not_reduced(self):
        raw=raw_item("existing","10002","20002",5);raw["price"]["value"]="110.00"
        self.plan["lines"][0]["delivery"]["quantity"]=5
        api=SimulatedCart(self.plan,[cart.normalize_item(raw)])
        result=self.apply(api)
        self.assertEqual(result["status"],"cart_verified")
        self.assertEqual(api.sent,[])
        self.assertEqual(result["lines"][0]["actual_packs"],5)

    def test_excess_existing_packs_require_shipping_quote_for_actual_cart(self):
        raw=raw_item("existing","10002","20002",5);raw["price"]["value"]="110.00"
        api=SimulatedCart(self.plan,[cart.normalize_item(raw)])
        with self.assertRaises(ReadFailure) as err:self.apply(api)
        self.assertEqual(err.exception.status,"delivery_quote_cart_quantity_mismatch")
        self.assertEqual(api.sent,[])

    def test_price_jump_blocks_but_quote_does_not_claim_shipping_selected(self):
        api=SimulatedCart(self.plan);api.extra_price="1.00"
        with self.assertRaises(ReadFailure) as err:self.apply(api)
        self.assertEqual(err.exception.status,"price_changed")
        self.assertEqual(api.sent,[])
        self.plan["lines"][0]["delivery"]["is_default"]=False
        api.extra_price='0.00'
        result=self.apply(api)
        self.assertEqual(result['status'],'cart_verified')
        self.assertTrue(result['shipping_selection_required'])
        self.assertFalse(result['checkout_performed'])

    def test_resume_after_interruption_does_not_duplicate_add(self):
        api=SimulatedCart(self.plan);api.raise_after_apply=True
        with self.assertRaises(KeyboardInterrupt):self.apply(api)
        api.raise_after_apply=False
        result=self.apply(api)
        self.assertEqual(result["status"],"cart_verified")
        self.assertEqual(len(api.sent),1)

    def test_dry_run_leaves_cart_unchanged(self):
        api=SimulatedCart(self.plan)
        self.assertEqual(self.apply(api,dry_run=True)["status"],"dry_run")
        self.assertEqual(api.sent,[])


class BatchSearchTests(unittest.TestCase):
    def test_reuses_query_cache_and_deduplicates_products(self):
        r=request();r["items"][0]["queries"]=["test one","test two"]
        response={"status":"ok","count":1,"observed_at":dt.datetime.now(dt.timezone.utc).isoformat(),
                  "products":[{"product_id":"123","title":"Synthetic"}]}
        with tempfile.TemporaryDirectory() as root,patch("batch_search.search",return_value=response) as call:
            first=batch_search.run(None,r,root)
            second=batch_search.run(None,r,root)
            self.assertEqual(call.call_count,2)
            self.assertEqual(len(first["rows"][0]["candidates"]),1)
            self.assertEqual(second["requests_sent"],0)


if __name__=="__main__":unittest.main(verbosity=2)
