#!/usr/bin/env python3
"""AliExpress cart CRUD, with live SKU checks and durable reconciliation.

Uses observed SnowCartContextWidget/0.51.0 and SnowCartSkuModal/0.13.1 contracts.
No checkout, bulk clear, guessed variants, automatic mutation retries or CAPTCHA solving.
"""
import argparse
import copy
from decimal import Decimal, InvalidOperation
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlencode

from ali_http import (Client, ORIGIN, PageData, ReadFailure, clean_text,
                      image_allowed, login_state, now, walk)
from session_store import binding, default_session

CART_URL = ORIGIN + "/cart"
GET = "/aer-jsonapi/v4/cart/hot/get"
PAGE = "/aer-jsonapi/v4/cart/hot/items/get"
ADD = "/aer-jsonapi/v2/cart/items/add"
UPDATE = "/aer-jsonapi/v2/cart/sku/update"
DELETE = "/aer-jsonapi/v2/cart/sku/remove"
SELECT = "/aer-jsonapi/v4/cart/hot/items/select"
SKU = "/aer-api/v3/bx/pdp/webProductToCart"


def fail(status, reason):
    raise ReadFailure(status, reason)


def integer(value, label, minimum=0):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)) or int(value) < minimum:
        fail("invalid_data", label + " must be an integer >= " + str(minimum))
    return int(value)


def identifier(value, label, numeric=False):
    pattern = r"\d{1,30}" if numeric else r"[A-Za-z0-9._:-]{1,128}"
    if value is None or isinstance(value, bool) or not re.fullmatch(pattern, str(value)):
        fail("invalid_data", "Invalid " + label)
    return str(value)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".cart-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def price(value):
    if not isinstance(value, dict):
        return None
    return {"value": str(value["value"]) if isinstance(value.get("value"), (str, int, float)) else None,
            "currency": clean_text(value.get("currency")),
            "display": clean_text(value.get("formattedPrice") or value.get("formatted"))}


def normalize_item(item):
    if not isinstance(item, dict) or not isinstance(item.get("quantity"), dict):
        fail("unsupported_response", "Cart item/quantity schema changed")
    quantity = item["quantity"]
    selected = item.get("checked")
    if not isinstance(selected, bool):
        fail("unsupported_response", "Cart item selection is unknown")
    store = item.get("store") or {}
    image = item.get("imageUrl")
    return {"item_id": identifier(item.get("itemId"), "itemId"),
            "product_id": identifier(item.get("productId"), "productId", True),
            "sku_id": identifier(item.get("skuId"), "skuId", True),
            "source_id": integer(item.get("productSourceId"), "productSourceId"),
            "quantity": integer(quantity.get("currentCount"), "currentCount", 1),
            "max_quantity": integer(quantity["maxCount"], "maxCount") if "maxCount" in quantity else None,
            "title": clean_text(item.get("title")), "variant_text": clean_text(item.get("subtitle")),
            "selected": selected, "available": item.get("available") if isinstance(item.get("available"), bool) else None,
            "price": price(item.get("price")), "store_name": clean_text(store.get("name")),
            "url": ORIGIN + "/item/" + str(item["productId"]) + ".html",
            "image_url": image if image_allowed(image) else None}


def state_rows(snapshot):
    return sorted([{k: x[k] for k in ("item_id", "product_id", "source_id", "sku_id", "quantity", "selected")}
                   for x in snapshot["items"]], key=lambda x: x["item_id"])


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(state_rows(snapshot), sort_keys=True).encode()).hexdigest()


def sku_key(item):
    return item["product_id"], item["source_id"], item["sku_id"]


class CartAPI:
    def __init__(self, client, max_pages=30):
        self.client, self.max_pages = client, max_pages

    def authenticate(self):
        raw, _ = self.client.fetch(CART_URL)
        state = login_state(PageData(raw.decode("utf-8", "replace")).aer)
        if hasattr(self.client, "auth_observed"):
            self.client.auth_observed(state)
        if state is not True:
            fail("authentication_required", "Cart page reports anonymous or unknown login; account cart was not read")

    def call(self, endpoint, payload=None, bare=False):
        raw, meta = self.client.fetch(ORIGIN + endpoint, payload=payload, referer=CART_URL, method="POST")
        self.last_source = {"url": ORIGIN + endpoint, "observed_at": now(),
                            "response_sha256": hashlib.sha256(raw).hexdigest()}
        try:
            parsed = json.loads(raw)
        except ValueError:
            fail("unsupported_response", "Cart endpoint returned non-JSON")
        if not isinstance(parsed, dict):
            fail("unsupported_response", "Cart endpoint returned unexpected JSON")
        if parsed.get("error"):
            error = parsed["error"]
            code = error.get("code") if isinstance(error, dict) else None
            safe = str(code) if code is not None and re.fullmatch(r"[\w:.-]{1,100}", str(code)) else "unclassified"
            fail("api_error", "Cart API error: " + safe)
        data = parsed if bare else parsed.get("data")
        if not isinstance(data, dict):
            fail("unsupported_response", "Cart endpoint missing data object")
        return data

    def read(self):
        self.authenticate()
        first = self.call(GET, {})
        summary, paging = self.page_state(first)
        total = integer(paging.get("totalPages"), "totalPages", 1)
        if integer(paging.get("currentPage"), "currentPage", 1) != 1:
            fail("incomplete_cart", "Initial cart response is not page 1")
        if total > self.max_pages:
            fail("incomplete_cart", "Cart exceeds --max-pages; no mutation attempted")
        expected_count = integer(summary.get("totalItemsCount"), "totalItemsCount")
        raw_items = list(first["items"])
        selected = first["itemsSummary"].get("selectedItemsIds")
        select_all = first["itemsSummary"].get("selectAll")
        sort = first["itemsSummary"].get("sortType")
        if not isinstance(selected, list) or not isinstance(select_all, bool) or not isinstance(sort, int):
            fail("unsupported_response", "Cart pagination selection/sort schema changed")
        self.sort_type = sort
        for page_number in range(2, total + 1):
            data = self.call(PAGE, {"selection": selected, "selectAll": select_all,
                                   "currentPage": page_number, "sortType": sort})
            page_summary, page = self.page_state(data, require_summary=False)
            if (page.get("currentPage") != page_number or page.get("totalPages") != total
                    or (page_summary is not None and page_summary.get("totalItemsCount") != expected_count)):
                fail("cart_changed_during_read", "Cart pagination/count changed during read")
            raw_items.extend(data["items"])
        items = [normalize_item(x) for x in raw_items]
        ids = [x["item_id"] for x in items]
        if len(ids) != len(set(ids)) or len(items) != expected_count:
            fail("incomplete_cart", "Duplicate rows or item count mismatch; no mutation attempted")
        result = {"schema_version": 1, "kind": "cart", "status": "ok", "observed_at": now(),
                  "authenticated": True, "complete": True, "scope": "hot_cart",
                  "pages": total, "count": len(items), "items": items,
                  "selected_total": price(summary.get("totalPrice")),
                  "shipping": price(summary.get("shippingPrice"))}
        result["fingerprint"] = fingerprint(result)
        return result

    @staticmethod
    def page_state(data, require_summary=True):
        if (not isinstance(data.get("items"), list)
                or (require_summary and not isinstance(data.get("summary"), dict))
                or (data.get("summary") is not None and not isinstance(data["summary"], dict))
                or not isinstance(data.get("itemsSummary"), dict)
                or not isinstance(data["itemsSummary"].get("page"), dict)):
            fail("unsupported_response", "Cart page schema changed")
        # The frontend retains the first-page summary when appending items/get.
        return data.get("summary"), data["itemsSummary"]["page"]

    def skus(self, product_id, source_id=None):
        product_id = identifier(product_id, "product ID", True)
        query = {"productId": product_id}
        if source_id is not None:
            query["sourceId"] = integer(source_id, "source ID")
        data = self.call(SKU + "?" + urlencode(query), bare=True)
        if str(data.get("id")) != product_id or not isinstance(data.get("skuInfo"), list):
            fail("unsupported_response", "Product-to-cart schema or product identity changed")
        source = integer(data.get("sourceId"), "product source ID")
        if source_id is not None and source != source_id:
            fail("identity_mismatch", "Product source ID differs from request")
        skus = []
        for row in data["skuInfo"]:
            attrs = row.get("skuAttr")
            if not isinstance(attrs, str):
                fail("unsupported_response", "Missing skuAttr")
            properties = row.get("propertyList")
            if not isinstance(properties, list):
                fail("unsupported_response", "Missing SKU propertyList")
            options = [{"name": clean_text(v.get("name")), "value": clean_text(v.get("propertyValueName")),
                        **({"image_url": v["image"]} if image_allowed(v.get("image")) else {})}
                       for v in properties if isinstance(v, dict)]
            if len(options) != len(properties) or any(not p["name"] or not p["value"] for p in options):
                fail("unsupported_response", "Unreadable SKU option")
            skus.append({"sku_id": identifier(row.get("skuId"), "SKU ID", True), "sku_attr": attrs,
                         "available_quantity": integer(row.get("availQuantity"), "availQuantity"),
                         "quantity_in_cart": integer(row.get("countInCart"), "countInCart"),
                         "options": options, "price": price(row.get("activityAmount"))})
        gallery = data.get("gallery")
        gallery = gallery if isinstance(gallery, list) else []
        return {"schema_version": 1, "kind": "skus", "status": "ok", "observed_at": now(),
                "product_id": product_id, "source_id": source, "title": clean_text(data.get("name")),
                "source": self.last_source, "skus": skus,
                "images": [{"url": url, "role": "product_gallery", "vision_status": "pending"}
                           for url in dict.fromkeys(u for u in gallery if image_allowed(u))]}

    def mutate(self, endpoint, payload):
        if endpoint not in (ADD, UPDATE, DELETE):
            fail("invalid_operation", "Endpoint is not a supported cart mutation")
        return self.call(endpoint, payload)

    def select(self, item_ids):
        return self.call(SELECT, {'selection':item_ids,'selectAll':False,'currentPage':1,'sortType':self.sort_type})


def check_constraints(intent, sku):
    if intent.get('expected_options') is not None:
        if {o['name']:o['value'] for o in sku.get('options',[])} != intent['expected_options']:
            fail('variant_changed','SKU labels differ from reviewed options')
    if intent.get('max_unit_price') is not None:
        try:
            cap=Decimal(str(intent['max_unit_price'])); amount=Decimal(str(sku['price']['value']))
            if not cap.is_finite() or not amount.is_finite() or cap<0 or amount<0: raise ValueError()
        except (ValueError,KeyError,TypeError,InvalidOperation):
            fail('invalid_data','Invalid price limit or current price')
        if amount>cap or sku['price']['currency']!=intent.get('currency','RUB'):
            fail('price_changed','Price exceeds the reviewed limit or currency differs')


def prepare(api, snapshot, intent):
    if snapshot.get("authenticated") is not True or snapshot.get("complete") is not True:
        fail("incomplete_cart", "Authenticated complete baseline required")
    op = intent["operation"]
    items = snapshot["items"]
    if op == "add":
        product = api.skus(intent["product_id"], intent.get("source_id"))
        choices = [s for s in product["skus"] if s["sku_id"] == intent["sku_id"]]
        if len(choices) != 1:
            fail("sku_not_found", "Requested SKU is not unique in live product options")
        sku = choices[0]
        check_constraints(intent,sku)
        target = {"product_id": product["product_id"], "source_id": product["source_id"], "sku_id": sku["sku_id"]}
        existing = [x for x in items if sku_key(x) == sku_key(target)]
        if len(existing) > 1:
            fail("ambiguous_sku", "Multiple cart rows have the same product/source/SKU")
        if sku["quantity_in_cart"] != sum(x["quantity"] for x in existing):
            fail("cart_changed_during_read", "SKU count disagrees with cart baseline")
        qty = integer(intent["quantity"], "quantity", 1)
        if existing:
            if existing[0]["quantity"] != qty:
                fail("already_exists", "SKU already exists with another quantity; use update with item_id")
            return {"noop": True, "reason": "already_present", "target": target, "quantity": qty}
        if qty > sku["available_quantity"]:
            fail("insufficient_stock", "Requested quantity exceeds live SKU availability")
        payload = {"productId": target["product_id"], "productSourceId": target["source_id"],
                   "quantity": qty, "skuId": target["sku_id"], "skuAttr": sku["sku_attr"]}
        return {"endpoint": ADD, "payload": payload, "target": target, "quantity": qty,
                "title": product["title"], "options": sku["options"], "price": sku["price"]}
    matches = [x for x in items if x["item_id"] == intent["item_id"]]
    if not matches:
        if op == "delete":
            return {"noop": True, "reason": "already_absent"}
        fail("item_not_found", "item_id is absent; read the cart again")
    item = matches[0]
    target = {k: item[k] for k in ("product_id", "source_id", "sku_id")}
    if sum(sku_key(x) == sku_key(target) for x in items) != 1:
        fail("ambiguous_sku", "SKU endpoint could affect more than one row")
    quantity = 0 if op == "delete" else integer(intent["quantity"], "quantity", 1)
    if op == "update":
        check_constraints(intent,item)
        if item["quantity"] == quantity:
            return {"noop": True, "reason": "quantity_unchanged", "target": target, "quantity": quantity}
        if item.get("max_quantity") is not None and quantity > item["max_quantity"]:
            fail("insufficient_stock", "Requested quantity exceeds cart maximum")
    payload = {"productId": item["product_id"], "productSourceId": item["source_id"], "skuId": item["sku_id"]}
    if op == "update":
        payload["quantity"] = quantity
    return {"endpoint": UPDATE if op == "update" else DELETE, "payload": payload,
            "target": target, "quantity": quantity, "item_id": item["item_id"],
            "title": item.get("title"), "variant_text": item.get("variant_text")}


def verify(before, after, plan):
    if after.get("authenticated") is not True or after.get("complete") is not True:
        return False
    target = sku_key(plan["target"])
    matches = [x for x in after["items"] if sku_key(x) == target]
    quantity = plan["quantity"]
    matched = not matches if quantity == 0 else len(matches) == 1 and matches[0]["quantity"] == quantity
    if quantity and matched and plan.get('max_unit_price') is not None:
        try: check_constraints({'max_unit_price':plan['max_unit_price'],'currency':plan.get('currency','RUB')},matches[0])
        except ReadFailure: return False
    old_other = {"items": [x for x in before["items"] if sku_key(x) != target]}
    new_other = {"items": [x for x in after["items"] if sku_key(x) != target]}
    # Keep target selection as well when changing an existing row's quantity.
    old_target = [x for x in before["items"] if sku_key(x) == target]
    selection_ok = not (quantity and old_target and matches) or old_target[0]["selected"] == matches[0]["selected"]
    return matched and selection_ok and state_rows(old_other) == state_rows(new_other)


def restore_add_selection(api, record, path, after):
    """Repair the observed add side effect, never unrelated quantity/row changes.

    A repair is a separate, journaled, at-most-once request. A resumed add may
    reconcile or perform its still-unattempted repair but never resend the add.
    """
    before,plan=record['before'],record['plan']
    if plan.get('endpoint')!=ADD or record.get('selection_restore') or verify(before,after,plan):
        return after
    old={x['item_id']:x for x in before['items']}
    # The observed server behavior deselects ALL existing rows and selects the new one.
    if not any(x['selected'] for x in old.values()): return after
    if any(x['selected'] for x in after['items'] if x['item_id'] in old): return after
    target=[x for x in after['items'] if sku_key(x)==sku_key(plan['target'])]
    if len(target)!=1 or target[0]['selected'] is not True: return after
    repaired=copy.deepcopy(after)
    for x in repaired['items']:
        if x['item_id'] in old: x['selected']=old[x['item_id']]['selected']
    if not verify(before,repaired,plan): return after
    current=api.read()
    if fingerprint(current)!=fingerprint(after): return current
    ids=[x['item_id'] for x in repaired['items'] if x['selected']]
    record['selection_restore']={'status':'attempting','selection':ids,'observed_before':after}
    save_json(path,record)
    try:
        api.select(ids)
    except (ReadFailure,OSError,ValueError,subprocess.SubprocessError) as e:
        record['selection_restore']['request_error']=getattr(e,'status','transport_error')
        save_json(path,record)
        if getattr(e,'status','') in ('requires_browser_verification','rate_limited') or getattr(e,'http_status',None)==403:
            raise
    final=api.read()
    record['selection_restore']['status']='verified' if verify(before,final,plan) else 'uncertain'
    save_json(path,record)
    return final


def execute(api, intent, state_dir, operation_id, session_hash, dry_run=False, preflight=None):
    operation_id = identifier(operation_id, "operation ID")
    # IDs are used as filenames: disallow traversal, including '..'.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation_id):
        fail("invalid_data", "Operation ID must use letters, digits, hyphen or underscore")
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("busy", "Another cart command holds this state directory lock")
        record_path = state_dir / (operation_id + ".json")
        before = api.read()
        if record_path.exists():
            record = read_json(record_path)
            if record["intent"] != intent or record["session_hash"] != session_hash:
                fail("operation_conflict", "Operation ID belongs to a different request/session export")
            plan = record["plan"]
            if record["status"] in ("verified", "noop"):
                return {"status": "already_completed", "operation_id": operation_id,
                        "journal": str(record_path.resolve()), "cart": before, "mutation_sent": False}
            # A previous attempt can still have succeeded after the response was lost.
            if not plan.get('noop'):
                before=restore_add_selection(api,record,record_path,before)
            if not plan.get("noop") and verify(record["before"], before, plan):
                record.update(status="verified", after=before, reconciled_at=now())
                save_json(record_path, record)
                return {"status": "verified", "operation_id": operation_id, "reconciled": True,
                        "journal": str(record_path.resolve()), "cart": before, "mutation_sent": False}
            return {"status": "uncertain", "operation_id": operation_id, "mutation_sent": False,
                    "reason": "Previous attempt is unresolved; do not resend automatically",
                    "journal": str(record_path.resolve()), "cart": before}
        plan = prepare(api, before, intent)
        if intent.get('max_unit_price') is not None:
            plan.update(max_unit_price=intent['max_unit_price'],currency=intent.get('currency','RUB'))
        if preflight:
            preflight(api, before)
        if dry_run:
            return {"status": "dry_run", "operation_id": operation_id, "plan": plan,
                    "baseline_fingerprint": fingerprint(before), "mutation_sent": False}
        if not plan.get("noop"):
            # SKU lookup and preflight may take time: detect browser edits before sending.
            current = api.read()
            if fingerprint(current) != fingerprint(before):
                fail("cart_changed_during_read", "Cart changed during preparation; no mutation sent")
        record = {"schema_version": 1, "operation_id": operation_id, "session_hash": session_hash,
                  "intent": intent, "plan": plan, "before": before, "created_at": now(),
                  "status": "noop" if plan.get("noop") else "attempting"}
        save_json(record_path, record)
        base = {"operation_id": operation_id, "journal": str(record_path.resolve()), "plan": plan}
        if plan.get("noop"):
            return {**base, "status": "noop", "cart": before, "mutation_sent": False}
        error = None
        try:
            api.mutate(plan["endpoint"], plan["payload"])
        except (ReadFailure, OSError, ValueError, subprocess.SubprocessError) as e:
            error = {"status": getattr(e, "status", "transport_error"), "error_type": type(e).__name__}
            record["request_error"] = error
            if getattr(e, "status", "") in ("requires_browser_verification", "rate_limited") or getattr(e, "http_status", None) == 403:
                record["status"] = "uncertain"
                save_json(record_path, record)
                return {**base, "status": "uncertain", "request_error": error, "mutation_sent": True}
        try:
            after = api.read()
            after = restore_add_selection(api,record,record_path,after)
            ok = verify(before, after, plan)
            record.update(after=after, status="verified" if ok else "uncertain")
        except (ReadFailure, OSError, ValueError, subprocess.SubprocessError) as e:
            after = None
            record.update(status="uncertain", verification_error={"status": getattr(e, "status", "read_error")})
        record["checked_at"] = now()
        save_json(record_path, record)
        return {**base, "status": record["status"], "mutation_sent": True, "cart": after,
                "request_error": error, "verification_error": record.get("verification_error")}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    parsers = {name: sub.add_parser(name) for name in ("list", "get", "skus", "add", "update", "delete")}
    for name, p in parsers.items():
        p.add_argument("--session", default=None if name == "skus" else default_session(), type=Path,
                       help="Managed AliExpress session (optional for public SKU catalogue)")
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--max-pages", type=int, default=30)
        if name in ("get", "update", "delete"):
            p.add_argument("--item-id", required=True)
        if name in ("add", "skus"):
            p.add_argument("--product-id", required=True)
            p.add_argument("--source-id", type=int)
        if name == "add":
            p.add_argument("--sku-id", required=True)
        if name in ("add", "update"):
            p.add_argument("--quantity", type=int, required=True, help="Absolute number of purchase units/packs")
            p.add_argument('--max-unit-price',help='Stop if the current unit price exceeds this amount')
            p.add_argument('--currency',default=None,help='Currency for max-unit-price; default RUB')
        if name=='add':
            p.add_argument('--expected-options',type=json.loads,help='Exact reviewed option labels as a JSON object')
        if name in ("add", "update", "delete"):
            p.add_argument("--operation-id", default=None, help="Reuse this ID to reconcile a previous attempt without resending")
            p.add_argument("--state-dir", type=Path, help="Default: cart-state next to the session export")
            p.add_argument("--dry-run", action="store_true", help="Read and prepare request, without mutation")
    a = parser.parse_args()
    operation_id = getattr(a, "operation_id", None) or str(uuid.uuid4())
    try:
        if not 1 <= a.max_pages <= 200:
            fail("invalid_data", "max-pages must be 1..200")
        if hasattr(a, "quantity"):
            integer(a.quantity, "quantity", 1)
        if hasattr(a, "product_id"):
            identifier(a.product_id, "product ID", True)
        if hasattr(a, "sku_id"):
            identifier(a.sku_id, "SKU ID", True)
        if hasattr(a, "item_id"):
            identifier(a.item_id, "item ID")
        if getattr(a, "source_id", None) is not None:
            integer(a.source_id, "source ID")
        with Client(a.session) as client:
            api = CartAPI(client, a.max_pages)
            if a.operation in ("list", "get"):
                result = api.read()
                if a.operation == "get":
                    found = [x for x in result["items"] if x["item_id"] == a.item_id]
                    result = {"status": "ok" if found else "not_found", "kind": "cart_item",
                              "item": found[0] if found else None, "cart_fingerprint": result["fingerprint"]}
            elif a.operation == "skus":
                result = api.skus(a.product_id, a.source_id)
                result["account_cart_verified"] = False  # Public catalogue access is not account login.
            else:
                intent = {"operation": a.operation}
                for key in ("item_id", "product_id", "source_id", "sku_id", "quantity"):
                    if hasattr(a, key):
                        intent[key] = getattr(a, key)
                # Preserve legacy intent shape so existing operation IDs still reconcile.
                for key in ('max_unit_price', 'expected_options'):
                    if getattr(a,key,None) is not None: intent[key] = getattr(a,key)
                if intent.get('max_unit_price') is not None:
                    intent['currency'] = a.currency or 'RUB'
                session_hash = binding(a.session)
                result = execute(api, intent, a.state_dir or a.session.parent / "cart-state", operation_id,
                                 session_hash, a.dry_run)
    except ReadFailure as e:
        result = {"status": e.status, "reason": e.reason, "http_status": e.http_status}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        result = {"status": "failed", "error_type": type(e).__name__}
    result["network"] = {"effective_group": grp.getgrgid(os.getegid()).gr_name, "effective_gid": os.getegid()}
    result["operation"] = a.operation
    if a.operation in ("add", "update", "delete"):
        result.setdefault("operation_id", operation_id)
    save_json(a.output, result)
    print(json.dumps({"status": result["status"], "output": str(a.output.resolve()),
                      "operation_id": result.get("operation_id"), "count": result.get("count")}, ensure_ascii=False))
    return 0 if result["status"] in ("ok", "noop", "verified", "already_completed", "dry_run") else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
