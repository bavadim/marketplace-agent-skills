#!/usr/bin/env python3
"""Normalize browser observations, validate evidence, plan BOM quantities, compare cart."""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def positive_int(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"{label}: positive integer required")
    return value


def canonical_product(url):
    u = urlsplit(url)
    match = re.fullmatch(r"/item/(\d+)\.html", u.path)
    if u.scheme != "https" or u.hostname != "aliexpress.ru" or u.username or u.password or not match:
        raise ValueError("Expected https://aliexpress.ru/item/<digits>.html")
    return match[1], f"https://aliexpress.ru/item/{match[1]}.html"


def variant_url(product_id, sku_id):
    if not all(isinstance(v, str) and re.fullmatch(r"\d+", v) for v in (product_id, sku_id)):
        raise ValueError("Product and SKU IDs must be observed numeric strings")
    return f"https://aliexpress.ru/item/{product_id}.html?sku_id={sku_id}"


def normalize(observation):
    product_id, url = canonical_product(observation["url"])
    if observation.get("product_id") not in (None, product_id):
        raise ValueError("Product ID mismatch")
    heading = observation.get("heading", [])
    title = heading[0] if len(heading) == 1 else None
    return {"schema_version": 1, "product_id": product_id, "url": url,
            "observed_at": observation.get("captured_at"), "title": title,
            "status": "needs_review", "facts": {},
            "evidence": [{"id": "dom-1", "method": "dom", "url": url,
                          "text": observation.get("text", "")}],
            "images": observation.get("images", []),
            "unresolved": ["selected variant", "price per purchase unit", "units in one pack", "delivery", "image specifications"]}


def fact(card, key):
    f = card.get("facts", {}).get(key)
    if not isinstance(f, dict) or f.get("verified") is not True or f.get("value") is None:
        raise ValueError(f"unverified fact: {key}")
    refs = f.get("evidence_ids")
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"missing evidence: {key}")
    evidence = card.get("evidence", [])
    ids = [e.get("id") for e in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate evidence IDs")
    found = {e["id"]: e for e in evidence}
    for ref in refs:
        if ref not in found or found[ref].get("method") not in ("dom", "vision", "platform_api"):
            raise ValueError(f"missing or OCR-only evidence: {key}")
        e = found[ref]
        if e["method"] == "platform_api":
            u = urlsplit(e.get("url", ""))
            observed_paths={'/aer-api/v3/bx/pdp/webProductToCart','/aer-jsonapi/v1/bx/pdp/web/productData',
                            '/widget','/aer-api/v1/pdp/web/freight/calculate'}
            if (u.scheme != "https" or u.hostname != "aliexpress.ru" or u.username or u.password
                    or u.port not in (None, 443) or (u.path not in observed_paths and not re.fullmatch(r'/store/\d+',u.path))
                    or not re.fullmatch(r"[0-9a-f]{64}", e.get("response_sha256", ""))):
                raise ValueError(f"invalid platform API evidence: {key}")
        if not e.get("text") or (e["method"] == "vision" and (not e.get("path") or not e.get("bbox"))):
            raise ValueError(f"incomplete evidence: {key}")
    return f["value"]


def money(value):
    if not isinstance(value, str):
        raise ValueError("Money must be a decimal string, e.g. 125.90")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid price") from error
    if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2:
        raise ValueError("Invalid money amount")
    return result


def sku_key(card):
    product_id, _ = canonical_product(card["url"])
    if product_id != card["product_id"]:
        raise ValueError("Product ID mismatch")
    variant = fact(card, "variant")
    if not isinstance(variant, dict) or not isinstance(variant.get("options"), dict):
        raise ValueError("variant must contain options")
    options = variant["options"]
    if not options and variant.get("no_options") is not True:
        raise ValueError("Empty variant needs explicit no_options=true")
    if options and variant.get("no_options") is True:
        raise ValueError("Contradictory variant")
    if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip() for k, v in options.items()):
        raise ValueError("Variant options must be nonempty strings")
    encoded = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{product_id}:{hashlib.sha256(encoded.encode()).hexdigest()[:16]}"


def plan(bom, selections):
    rows = bom.get("items", [])
    if not rows:
        raise ValueError("BOM items required")
    row_ids = [r["id"] for r in rows]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("Duplicate BOM row ID")
    chosen = {}
    for entry in selections:
        if entry["row_id"] in chosen or entry["row_id"] not in row_ids:
            raise ValueError("Duplicate or unknown selection row ID")
        chosen[entry["row_id"]] = entry["product"]
    blockers, groups = [], {}
    for row in rows:
        required = positive_int(row["quantity"], "BOM quantity")
        if not isinstance(row.get("required_facts"), dict):
            raise ValueError("Every row needs required_facts (may be empty)")
        card = chosen.get(row["id"])
        if not card:
            blockers.append({"row_id": row["id"], "reason": "no_selection"})
            continue
        try:
            if card.get("status") != "verified":
                raise ValueError("card is not verified")
            if card.get("unresolved"):
                raise ValueError("card has unresolved observations")
            stamp = dt.datetime.fromisoformat(card["observed_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("observed_at requires timezone")
            for key, expected in row["required_facts"].items():
                actual = fact(card, key)
                # Explicit canonical values prevent dangerous fuzzy unit/variant matching.
                if type(actual) is not type(expected) or actual != expected:
                    raise ValueError(f"spec mismatch: {key}")
            key = sku_key(card)
            pack = positive_int(fact(card, "pack_size"), "pack_size")
            unit = fact(card, "unit")
            if unit != row.get("unit", "pcs"):
                raise ValueError("BOM / pack unit mismatch")
            price = money(fact(card, "price_per_pack"))
            currency = fact(card, "currency")
            if currency != bom.get("currency", "RUB"):
                raise ValueError("currency mismatch")
            if key in groups and (groups[key]["pack_size"] != pack or groups[key]["price_per_pack"] != str(price)):
                raise ValueError("conflicting observations for same SKU")
            group = groups.setdefault(key, {"sku_key": key, "product_id": card["product_id"],
                "url": canonical_product(card["url"])[1], "variant": fact(card, "variant"),
                "pack_size": pack, "price_per_pack": str(price), "currency": currency,
                "unit": unit, "required_units": 0, "row_ids": [], "observed_at": card["observed_at"]})
            group["required_units"] += required
            group["row_ids"].append(row["id"])
        except (KeyError, ValueError, TypeError) as error:
            blockers.append({"row_id": row["id"], "reason": str(error)})
    lines = list(groups.values())
    subtotal = Decimal(0)
    for line in lines:
        packs = (line["required_units"] + line["pack_size"] - 1) // line["pack_size"]
        line["purchase_quantity"] = packs
        line["supplied_units"] = packs * line["pack_size"]
        line["surplus_units"] = line["supplied_units"] - line["required_units"]
        line["subtotal"] = str((money(line["price_per_pack"]) * packs).quantize(Decimal("0.01")))
        subtotal += Decimal(line["subtotal"])
    limit = bom.get("goods_budget")
    if limit is not None and subtotal > money(limit):
        blockers.append({"reason": "goods_budget_exceeded"})
    return {"schema_version": 1, "status": "blocked" if blockers else "ready_for_cart_review",
            "currency": bom.get("currency", "RUB"), "lines": lines, "blockers": blockers,
            "goods_subtotal": str(subtotal.quantize(Decimal("0.01"))), "shipping_total": None,
            "order_total": None, "checkout_authorized": False,
            "note": "Recheck live SKU, quantity, price and delivery before cart mutation. Quantities are purchase units/packs. Subtotal excludes delivery and checkout discounts."}


def verify_cart(plan_data, baseline, current):
    """Snapshots contain only relevant visible cart rows; quantities are packs."""
    def index(rows):
        result = {}
        for row in rows:
            if row.get("verified") is not True or not row.get("evidence"):
                raise ValueError("Each cart row needs verified visible evidence")
            key = row["sku_key"]
            if key in result:
                raise ValueError("Duplicate cart SKU; disambiguate rows first")
            result[key] = positive_int(row["quantity"], "cart quantity")
        return result
    if plan_data.get("status") != "ready_for_cart_review":
        raise ValueError("Plan has blockers")
    if baseline.get("complete") is not True or current.get("complete") is not True:
        raise ValueError("Cart snapshots must be complete, including lazy-loaded rows")
    before, after = index(baseline["items"]), index(current["items"])
    deltas, expected = [], {}
    for line in plan_data["lines"]:
        key = line["sku_key"]
        # Existing identical items count toward the BOM: idempotent target quantity.
        wanted = max(before.get(key, 0), line["purchase_quantity"])
        expected[key] = wanted
        deltas.append({"sku_key": key, "baseline_quantity": before.get(key, 0),
                       "expected_quantity": wanted, "actual_quantity": after.get(key, 0),
                       "add_quantity": max(0, line["purchase_quantity"] - before.get(key, 0)),
                       "verified": after.get(key, 0) == wanted})
    unrelated = [key for key in set(before) | set(after)
                 if key not in expected and before.get(key, 0) != after.get(key, 0)]
    return {"status": "verified" if all(d["verified"] for d in deltas) and not unrelated else "mismatch",
            "lines": deltas, "unrelated_changed": unrelated}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    n = sub.add_parser("normalize"); n.add_argument("observation")
    b = sub.add_parser("plan"); b.add_argument("bom"); b.add_argument("selections")
    c = sub.add_parser("verify-cart"); c.add_argument("plan"); c.add_argument("baseline"); c.add_argument("current")
    for parser in (n, b, c):
        parser.add_argument("--output", required=True)
    a = p.parse_args()
    try:
        if a.command == "normalize": result = normalize(read(a.observation))
        elif a.command == "plan": result = plan(read(a.bom), read(a.selections))
        else: result = verify_cart(read(a.plan), read(a.baseline), read(a.current))
        Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(Path(a.output).resolve()), "status": result.get("status")}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as error:
        p.error(str(error))


if __name__ == "__main__":
    main()
