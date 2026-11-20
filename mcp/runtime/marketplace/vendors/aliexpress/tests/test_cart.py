#!/usr/bin/env python3
"""Cart behavior tests using isolated files and synthetic server responses."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ali_cart as cart
from ali_http import ReadFailure


def raw_item(item_id="row1", product_id="10001", sku_id="20001", quantity=2, selected=True):
    return {"itemId": item_id, "productId": product_id, "skuId": sku_id, "productSourceId": 0,
            "quantity": {"currentCount": quantity, "maxCount": 20}, "checked": selected,
            "available": True, "title": "Example resistor", "subtitle": "10 kΩ, 100 pcs",
            "price": {"value": "41", "formattedPrice": "41 ₽", "currency": "RUB"},
            "trackingInfo": {"secret": "PRIVATE"}}


def snapshot(items=None):
    return {"authenticated": True, "complete": True, "items": copy.deepcopy(items or []), "status": "ok"}


class FakeAPI:
    def __init__(self, items=None, mode="ok"):
        self.snapshot = snapshot(items)
        self.mode = mode
        self.sent = []
        self.selection_sent = []
        self.reads = 0
        self.stock = 20
        self.sku_present = True

    def read(self):
        self.reads += 1
        if self.mode == "concurrent_before" and self.reads == 2:
            self.snapshot["items"][0]["quantity"] += 1
        if self.mode == "verification_unavailable" and self.sent:
            raise ReadFailure("transport_error", "timeout")
        return copy.deepcopy(self.snapshot)

    def skus(self, product_id, source_id):
        count = sum(x["quantity"] for x in self.snapshot["items"] if x["product_id"] == product_id and x["sku_id"] == "20001")
        return {"product_id": product_id, "source_id": 0, "title": "Example", "skus": [
            {"sku_id": "20001", "quantity_in_cart": count, "available_quantity": self.stock,
             "sku_attr": "1:2#10k", "options": [{"name": "Resistance", "value": "10k"}],
             "price": {"value": "41", "currency": "RUB"}}] if self.sku_present else []}

    def mutate(self, endpoint, payload):
        self.sent.append((endpoint, copy.deepcopy(payload)))
        if self.mode == "timeout_before_apply":
            raise ReadFailure("transport_error", "timeout")
        if self.mode == "captcha":
            raise ReadFailure("requires_browser_verification", "captcha", 200)
        rows = self.snapshot["items"]
        if endpoint == cart.ADD:
            if self.mode.startswith('deselect'):
                for row in rows: row['selected']=False
            rows.append(cart.normalize_item(raw_item("new-row", payload["productId"], payload["skuId"], payload["quantity"])))
        elif endpoint == cart.UPDATE:
            for x in rows:
                if x["product_id"] == payload["productId"] and x["sku_id"] == payload["skuId"]:
                    x["quantity"] = payload["quantity"]
        elif endpoint == cart.DELETE:
            rows[:] = [x for x in rows if not (x["product_id"] == payload["productId"] and x["sku_id"] == payload["skuId"])]
        else:
            raise AssertionError("Unexpected endpoint")
        if self.mode == "other_row_changed":
            rows[-1]["selected"] = not rows[-1]["selected"]
        if self.mode == "timeout_after_apply":
            raise ReadFailure("transport_error", "timeout")
        return {"id": "new-row"}

    def select(self, ids):
        self.selection_sent.append(ids)
        if self.mode=='deselect_restore_timeout_before': raise ReadFailure('transport_error','timeout')
        for row in self.snapshot['items']: row['selected']=row['item_id'] in ids
        if self.mode=='deselect_restore_timeout_after': raise ReadFailure('transport_error','timeout')
        return {}


class CartBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def execute(self, api, intent, **kwargs):
        return cart.execute(api, intent, self.root, "test-op", "session-digest", **kwargs)

    def add_intent(self, quantity=2):
        return {"operation": "add", "product_id": "10001", "sku_id": "20001", "source_id": None, "quantity": quantity}

    def test_create_adds_exact_sku_and_quantity_once(self):
        api = FakeAPI()
        result = self.execute(api, self.add_intent())
        self.assertEqual(result["status"], "verified")
        self.assertEqual(api.sent, [(cart.ADD, {"productId": "10001", "productSourceId": 0,
                                            "quantity": 2, "skuId": "20001", "skuAttr": "1:2#10k"})])
        record = cart.read_json(self.root / "test-op.json")
        self.assertEqual(record["status"], "verified")
        self.assertEqual((self.root / "test-op.json").stat().st_mode & 0o777, 0o600)

    def existing_other(self):
        return [cart.normalize_item(raw_item('old','10002','20002',1,True)),
                cart.normalize_item(raw_item('unselected','10003','20003',1,False))]

    def test_add_preserves_old_selection_with_journaled_repair(self):
        api=FakeAPI(self.existing_other(),mode='deselect')
        select=api.select
        def checked(ids):
            self.assertEqual(cart.read_json(self.root/'test-op.json')['selection_restore']['status'],'attempting')
            return select(ids)
        api.select=checked
        result=self.execute(api,self.add_intent())
        self.assertEqual(result['status'],'verified')
        self.assertEqual(api.selection_sent,[['old','new-row']])
        self.assertEqual(len(api.sent),1)
        self.assertFalse(result['cart']['items'][1]['selected'])

    def test_repair_timeout_reconciles_without_second_add_or_select(self):
        for mode in ['deselect_restore_timeout_before','deselect_restore_timeout_after']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                api=FakeAPI(self.existing_other(),mode=mode)
                def execute():return cart.execute(api,self.add_intent(),Path(tmp),'op','session')
                result=execute(); again=execute()
                self.assertEqual(len(api.sent),1);self.assertEqual(len(api.selection_sent),1)
                self.assertEqual(result['status'],'uncertain' if mode.endswith('before') else 'verified')
                self.assertEqual(again['status'],'uncertain' if mode.endswith('before') else 'already_completed')

    def test_unfinished_add_can_repair_selection_on_resume(self):
        api=FakeAPI(self.existing_other(),mode='deselect')
        with patch('ali_cart.restore_add_selection',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.execute(api,self.add_intent())
        result=self.execute(api,self.add_intent())
        self.assertEqual(result['status'],'verified');self.assertTrue(result['reconciled'])
        self.assertEqual(len(api.sent),1);self.assertEqual(len(api.selection_sent),1)

    def test_concurrent_change_during_selection_repair_is_not_overwritten(self):
        api=FakeAPI(self.existing_other(),mode='deselect'); read=api.read
        def changing():
            if api.reads==3:api.snapshot['items'][0]['quantity']+=1
            return read()
        api.read=changing
        result=self.execute(api,self.add_intent())
        self.assertEqual(result['status'],'uncertain');self.assertEqual(api.selection_sent,[])

    def test_price_and_option_limits_stop_add_before_mutation(self):
        for extra in [{'max_unit_price':'40','currency':'RUB'}, {'expected_options':{'Resistance':'20k'}}]:
            api=FakeAPI()
            with self.assertRaises(ReadFailure):self.execute(api,{**self.add_intent(),**extra})
            self.assertEqual(api.sent,[])

    def test_attempt_is_durable_before_mutation(self):
        api = FakeAPI()
        send = api.mutate
        def inspect(endpoint, payload):
            record = cart.read_json(self.root / "test-op.json")
            self.assertEqual(record["status"], "attempting")
            self.assertEqual(record["before"]["items"], [])
            return send(endpoint, payload)
        api.mutate = inspect
        self.execute(api, self.add_intent())

    def test_update_sets_absolute_quantity_and_preserves_other_rows(self):
        items = [cart.normalize_item(raw_item()), cart.normalize_item(raw_item("row2", "10002", "20002", 5, False))]
        api = FakeAPI(items)
        result = self.execute(api, {"operation": "update", "item_id": "row1", "quantity": 7})
        self.assertEqual(result["status"], "verified")
        self.assertEqual([x["quantity"] for x in api.snapshot["items"]], [7, 5])
        self.assertEqual(api.sent[0][0], cart.UPDATE)

    def test_delete_only_requested_row(self):
        items = [cart.normalize_item(raw_item()), cart.normalize_item(raw_item("row2", "10002", "20002", 5, False))]
        api = FakeAPI(items)
        result = self.execute(api, {"operation": "delete", "item_id": "row1"})
        self.assertEqual(result["status"], "verified")
        self.assertEqual([x["item_id"] for x in api.snapshot["items"]], ["row2"])
        self.assertNotIn("quantity", api.sent[0][1])

    def test_absent_delete_is_noop(self):
        api = FakeAPI()
        self.assertEqual(self.execute(api, {"operation": "delete", "item_id": "row1"})["status"], "noop")
        self.assertEqual(api.sent, [])

    def test_existing_sku_not_incremented_on_repeat(self):
        api = FakeAPI([cart.normalize_item(raw_item())])
        self.assertEqual(self.execute(api, self.add_intent())["status"], "noop")
        self.assertEqual(api.sent, [])

    def test_existing_sku_different_quantity_requires_update(self):
        api = FakeAPI([cart.normalize_item(raw_item(quantity=4))])
        with self.assertRaises(ReadFailure) as error:
            self.execute(api, self.add_intent())
        self.assertEqual(error.exception.status, "already_exists")
        self.assertEqual(api.sent, [])

    def test_unknown_or_out_of_stock_sku_never_mutated(self):
        for no_sku in (True, False):
            api = FakeAPI()
            api.sku_present = not no_sku
            api.stock = 1
            with self.assertRaises(ReadFailure):
                self.execute(api, self.add_intent())
            self.assertEqual(api.sent, [])

    def test_ambiguous_sku_blocks_delete(self):
        api = FakeAPI([cart.normalize_item(raw_item()), cart.normalize_item(raw_item("row2"))])
        with self.assertRaises(ReadFailure) as error:
            self.execute(api, {"operation": "delete", "item_id": "row1"})
        self.assertEqual(error.exception.status, "ambiguous_sku")
        self.assertEqual(api.sent, [])

    def test_dry_run_prepares_without_writing_journal_or_cart(self):
        api = FakeAPI()
        result = self.execute(api, self.add_intent(), dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["plan"]["payload"]["skuAttr"], "1:2#10k")
        self.assertFalse((self.root / "test-op.json").exists())
        self.assertEqual(api.sent, [])

    def test_timeout_after_apply_reconciles_without_resending(self):
        api = FakeAPI(mode="timeout_after_apply")
        result = self.execute(api, self.add_intent())
        self.assertEqual(result["status"], "verified")
        again = self.execute(api, self.add_intent())
        self.assertEqual(again["status"], "already_completed")
        self.assertEqual(len(api.sent), 1)

    def test_timeout_before_apply_never_retried_automatically(self):
        api = FakeAPI(mode="timeout_before_apply")
        for _ in range(2):
            result = self.execute(api, self.add_intent())
            self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(api.sent), 1)

    def test_late_success_reconciles_previous_uncertain_attempt(self):
        api = FakeAPI(mode="verification_unavailable")
        self.assertEqual(self.execute(api, self.add_intent())["status"], "uncertain")
        api.mode = "ok"
        result = self.execute(api, self.add_intent())
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["reconciled"])
        self.assertEqual(len(api.sent), 1)

    def test_captcha_stops_without_read_retry(self):
        api = FakeAPI(mode="captcha")
        result = self.execute(api, self.add_intent())
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(api.reads, 2)
        self.assertEqual(len(api.sent), 1)

    def test_concurrent_browser_change_prevents_mutation(self):
        api = FakeAPI([cart.normalize_item(raw_item())], mode="concurrent_before")
        with self.assertRaises(ReadFailure) as error:
            self.execute(api, {"operation": "update", "item_id": "row1", "quantity": 5})
        self.assertEqual(error.exception.status, "cart_changed_during_read")
        self.assertEqual(api.sent, [])

    def test_unrelated_selection_change_not_reported_success(self):
        items = [cart.normalize_item(raw_item()), cart.normalize_item(raw_item("row2", "10002", "20002", 5, False))]
        api = FakeAPI(items, mode="other_row_changed")
        result = self.execute(api, {"operation": "update", "item_id": "row1", "quantity": 4})
        self.assertEqual(result["status"], "uncertain")

    def test_operation_id_cannot_be_reused_for_new_request(self):
        api = FakeAPI()
        self.execute(api, self.add_intent())
        with self.assertRaises(ReadFailure) as error:
            self.execute(api, self.add_intent(3))
        self.assertEqual(error.exception.status, "operation_conflict")
        self.assertEqual(len(api.sent), 1)


def raw_cart(items, page=1, pages=1, count=None):
    return {"items": items, "itemsSummary": {"page": {"currentPage": page, "totalPages": pages},
        "selectedItemsIds": ["row1"], "selectAll": False, "sortType": 2},
        "summary": {"totalItemsCount": len(items) if count is None else count,
                    "totalPrice": {"value": "82", "formattedPrice": "82 ₽", "currency": "RUB"}}}


class FakeTransport:
    def __init__(self, replies, logged_in=True):
        self.replies = list(replies)
        self.logged_in = logged_in
        self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == cart.CART_URL:
            state = {"widgets": [{"props": {"isLoggedIn": self.logged_in, "loginStatus": "SIGNED_IN" if self.logged_in else "ANONYMOUS"}}]}
            return ('<script id="__AER_DATA__">' + json.dumps(state) + '</script>').encode(), {}
        return json.dumps(self.replies.pop(0)).encode(), {}


class CartContractTests(unittest.TestCase):
    def test_anonymous_cart_is_not_reported_as_empty_account_cart(self):
        client = FakeTransport([], logged_in=False)
        with self.assertRaises(ReadFailure) as error:
            cart.CartAPI(client).read()
        self.assertEqual(error.exception.status, "authentication_required")
        self.assertEqual(len(client.calls), 1)

    def test_reads_all_pages_and_preserves_selection(self):
        client = FakeTransport([{"data": raw_cart([raw_item()], 1, 2, 2)},
                                {"data": raw_cart([raw_item("row2", "10002", "20002")], 2, 2, 2)}])
        result = cart.CartAPI(client).read()
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["complete"])
        self.assertEqual(client.calls[2][1]["payload"], {"selection": ["row1"], "selectAll": False, "currentPage": 2, "sortType": 2})
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_later_page_can_omit_summary_as_frontend_retains_first(self):
        later = raw_cart([raw_item("row2", "10002", "20002")], 2, 2, 2)
        del later["summary"]
        client = FakeTransport([{"data": raw_cart([raw_item()], 1, 2, 2)}, {"data": later}])
        result = cart.CartAPI(client).read()
        self.assertTrue(result["complete"])
        self.assertEqual(result["count"], 2)

    def test_count_mismatch_is_incomplete_not_success(self):
        client = FakeTransport([{"data": raw_cart([raw_item()], count=3)}])
        with self.assertRaises(ReadFailure) as error:
            cart.CartAPI(client).read()
        self.assertEqual(error.exception.status, "incomplete_cart")

    def test_api_error_with_http_200_is_not_success(self):
        client = FakeTransport([{"error": {"code": "SESSION_EXPIRED", "message": "PRIVATE"}}])
        with self.assertRaises(ReadFailure) as error:
            cart.CartAPI(client).read()
        self.assertEqual(error.exception.status, "api_error")
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_live_sku_request_has_observed_fields_and_no_guessed_attr(self):
        client = FakeTransport([{"id": "10001", "sourceId": 0, "name": "Example", "skuInfo": [
            {"skuId": "20001", "skuAttr": "1:2#10k", "availQuantity": 10, "countInCart": 0,
             "propertyList": [{"name": "Resistance", "propertyValueName": "10k"}],
             "activityAmount": {"value": "41", "currency": "RUB", "formatted": "41 ₽"}}]}])
        result = cart.CartAPI(client).skus("10001")
        self.assertEqual(result["skus"][0]["options"], [{"name": "Resistance", "value": "10k"}])
        self.assertEqual(client.calls[0][1]["method"], "POST")
        self.assertIsNone(client.calls[0][1]["payload"])
        self.assertEqual(client.calls[0][0], cart.ORIGIN + cart.SKU + "?productId=10001")

    def test_guest_sku_cli_does_not_require_account_cart_or_session(self):
        image = "https://ae-pic-a1.aliexpress-media.com/kf/example.jpg"
        transport = FakeTransport([{"id": "10001", "sourceId": 0, "name": "Example", "skuInfo": [],
                                    "gallery": [image, image, "https://evil.test/track", None]}], logged_in=False)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with patch("sys.argv", ["ali_cart.py", "skus", "--product-id", "10001", "--output", str(output)]), \
                    patch.object(cart, "Client") as client, patch("builtins.print"):
                client.return_value.__enter__.return_value = transport
                cart.main()
            result = json.loads(output.read_text())
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["account_cart_verified"])
        self.assertEqual([i["url"] for i in result["images"]], [image])
        self.assertEqual(len(transport.calls), 1)
        self.assertNotEqual(transport.calls[0][0], cart.CART_URL)



if __name__ == "__main__":
    unittest.main(verbosity=2)
