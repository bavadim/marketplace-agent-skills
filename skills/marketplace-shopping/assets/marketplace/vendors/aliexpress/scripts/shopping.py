#!/usr/bin/env python3
"""Draft offers, bind live SKUs, compare delivery/seller/price and compile cart plans.

Agent supplies queries, equivalence judgments and evidence; arithmetic is deterministic.
"""
import argparse
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from statistics import median
import sys

import bom
from ali_cart import CART_URL, save_json

DEFAULTS = {"min_seller_rating_5": "4.5", "min_seller_reviews": 0,
            "min_independent_sellers": 1, "target_independent_sellers": 3,
            "high_price_factor": "3", "low_price_factor": "0.25",
            "fast_delivery_premium": "0.20", "max_observation_age_hours": 12}


def timestamp(value):
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("Observation timestamp requires timezone")
    return t


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def amount(value):
    return format(value.quantize(Decimal("0.01")), "f")


def checked_delivery(value, destination, sku_id, quantity, currency, today):
    if value.get("destination_key") != destination or value.get("sku_id") != sku_id:
        raise ValueError("delivery_destination_or_sku_mismatch")
    if value.get("quantity") != quantity or type(value.get("quantity")) is not int:
        raise ValueError("delivery_quote_quantity_mismatch")
    earliest, latest = dt.date.fromisoformat(value["earliest"]), dt.date.fromisoformat(value["latest"])
    if not today <= earliest <= latest:
        raise ValueError("invalid_delivery_window")
    if value["currency"] != currency:
        raise ValueError("shipping_currency_mismatch")
    if not isinstance(value.get("method"), str) or not value["method"].strip() or (value.get('is_default') is not None and type(value['is_default']) is not bool):
        raise ValueError("delivery_method_required")
    bom.money(value["cost"])
    return value


def policy(request):
    supplied = request.get("policy", {})
    if set(supplied) - set(DEFAULTS):
        raise ValueError("Unknown policy field")
    p = {**DEFAULTS, **supplied}
    for field in ("min_seller_rating_5", "high_price_factor", "low_price_factor", "fast_delivery_premium"):
        v = Decimal(str(p[field]))
        if not v.is_finite() or v < 0:
            raise ValueError("Invalid policy number")
    if not 0 <= Decimal(p["min_seller_rating_5"]) <= 5:
        raise ValueError("Seller rating threshold must be on 0..5 scale")
    if Decimal(p["high_price_factor"]) <= 1 or not 0 < Decimal(p["low_price_factor"]) < 1:
        raise ValueError("Invalid price outlier factors")
    for field in ("min_independent_sellers", "target_independent_sellers", "max_observation_age_hours"):
        bom.positive_int(p[field], field)
    if type(p["min_seller_reviews"]) is not int or p["min_seller_reviews"] < 0:
        raise ValueError("Invalid seller review threshold")
    return p


def draft(shortlist, row_id, ids):
    rows = [r for r in shortlist.get("rows", []) if r["row_id"] == row_id]
    if len(rows) != 1:
        raise ValueError("Row not found in batch-search result")
    found = {p["product_id"]: p for p in rows[0]["candidates"]}
    result = []
    for pid in dict.fromkeys(ids):
        card = copy.deepcopy(found[pid])
        card.update(status="needs_review", facts={})
        result.append({"offer_id": row_id + "-" + pid, "row_id": row_id, "product": card})
    return result


def bind(offers, offer_id, catalog, sku_id, new_offer_id=None):
    offers = copy.deepcopy(offers)
    matches = [o for o in offers if o["offer_id"] == offer_id]
    if len(matches) != 1:
        raise ValueError("Offer must be unique")
    if new_offer_id is not None:
        if not new_offer_id.strip() or any(o["offer_id"] == new_offer_id for o in offers):
            raise ValueError("New offer ID must be nonempty and unique")
        cloned = copy.deepcopy(matches[0])
        cloned["offer_id"] = new_offer_id
        offers.append(cloned)
        matches = [cloned]
    card = matches[0]["product"]
    if card["product_id"] != catalog["product_id"] or catalog.get("status") != "ok":
        raise ValueError("SKU catalogue product mismatch")
    skus = [s for s in catalog["skus"] if s["sku_id"] == sku_id]
    if len(skus) != 1:
        raise ValueError("SKU must exist and be unique")
    sku = skus[0]
    options = {p["name"]: p["value"] for p in sku["options"]}
    if len(options) != len(sku["options"]) or (not options and len(catalog["skus"]) != 1):
        raise ValueError("Ambiguous variant options")
    source = catalog["source"]
    old_id = card.get("facts", {}).get("sku_id", {}).get("value")
    old_options = card.get("facts", {}).get("variant", {}).get("value", {}).get("options")
    old_source = card.get("facts", {}).get("source_id", {}).get("value")
    if old_id != sku_id or old_options != options or old_source != catalog["source_id"]:
        # Specs, contents, shipping and judgments are tied to the previous variant.
        card["facts"] = {}
        card["evidence"] = [e for e in card.get("evidence", [])
                            if e.get("method") in ("platform_search", "structured_data")]
        card["unresolved"] = ["technical specifications for selected SKU", "package contents and pack size",
                              "delivery for selected SKU", "seller rating", "technical suitability"]
    evidence_id = "api-sku-" + sku_id
    ev = {"id": evidence_id, "method": "platform_api", **source,
          "text": json.dumps({"product_id": card["product_id"], "sku": sku}, ensure_ascii=False)}
    card["evidence"] = [e for e in card.get("evidence", []) if e["id"] != evidence_id] + [ev]
    values = {"sku_id": sku_id, "source_id": catalog["source_id"],
              "variant": {"options": options, **({"no_options": True} if not options else {})},
              "price_per_pack": sku["price"]["value"], "currency": sku["price"]["currency"]}
    for name, value in values.items():
        card.setdefault("facts", {})[name] = {"value": value, "verified": True, "evidence_ids": [evidence_id],
                                              "observed_at": catalog["observed_at"]}
        bom.fact(card, name)
    # Binding IDs and labels does not establish technical suitability, pack size or shipping.
    card.update(status="needs_review", sku_observed_at=catalog["observed_at"],
                selected_url=bom.variant_url(card["product_id"], sku_id))
    return offers


def enrich(offers, offer_id, packet):
    """Attach an agent's actually reviewed DOM/CV observations without hand-writing fact wrappers."""
    result = copy.deepcopy(offers)
    matches = [o for o in result if o["offer_id"] == offer_id]
    if len(matches) != 1:
        raise ValueError("Offer must be unique")
    card = matches[0]["product"]
    if packet["sku_id"] != bom.fact(card, "sku_id"):
        raise ValueError("Observation packet belongs to another SKU")
    captured = timestamp(packet["observed_at"]).isoformat()
    for f in card["facts"].values():
        f.setdefault("observed_at", card["observed_at"])
    for obs in packet["observations"]:
        obs_captured=timestamp(obs.get('observed_at',captured)).isoformat()
        if obs.get("reviewed") is not True or obs.get("method") not in ("dom", "vision", "platform_api") or not obs.get("text"):
            raise ValueError("Actually reviewed DOM/vision/API evidence is required")
        if obs["method"] == "vision" and (not Path(obs.get("path", "")).is_absolute() or not obs.get("bbox")):
            raise ValueError("Vision evidence needs absolute tile path and bbox")
        evidence_id = obs.get("id") or "review-" + digest(obs)[:16]
        ev = {k: v for k, v in obs.items() if k not in ("facts", "reviewed")}
        ev.update(id=evidence_id, observed_at=obs_captured)
        card["evidence"] = [e for e in card["evidence"] if e["id"] != evidence_id] + [ev]
        for name, value in obs["facts"].items():
            if name in ("sku_id", "source_id") and value != bom.fact(card, name):
                raise ValueError("Reviewed evidence cannot silently rebind SKU identity")
            card["facts"][name] = {"value": value, "verified": True, "evidence_ids": [evidence_id], "observed_at": obs_captured}
            bom.fact(card, name)
    card["unresolved"] = packet["unresolved"]
    card["status"] = "verified" if packet.get("review_complete") is True and not card["unresolved"] else "needs_review"
    card["observed_at"] = min(timestamp(f["observed_at"]) for f in card["facts"].values()).isoformat()
    return result


def collect(product, row_id, sku_id, quote=None, seller=None, shipping_index=0):
    """Bind machine facts once; agent supplies only the technical review afterward."""
    card=copy.deepcopy(product)
    if card.get('detail_status') not in ('ok','partial'): raise ValueError('Read product details first')
    card.update(facts={},status='needs_review')
    offer_id=row_id+'-'+card['product_id']+'-'+sku_id
    offers=bind([{'offer_id':offer_id,'row_id':row_id,'product':card}],offer_id,card['catalog'],sku_id)
    observations=[]
    seller=seller or product.get('seller')
    if seller:
        if seller.get('status')!='ok': raise ValueError('Seller response is not successful')
        expected=product.get('listing',{}).get('store',{}).get('url')
        if expected and seller['seller']['url']!=expected: raise ValueError('Seller does not match search listing')
        observations.append({'id':'api-seller',**seller['source'],'method':'platform_api','reviewed':True,'observed_at':seller['observed_at'],
                             'response_sha256':seller['source'].get('response_sha256') or seller['source'].get('sha256'),
                             'text':json.dumps(seller['seller'],ensure_ascii=False),'facts':{'seller':seller['seller']}})
    if quote:
        if quote.get('status')!='ok' or quote.get('sku_id')!=sku_id or quote.get('product_id')!=card['product_id']:
            raise ValueError('Delivery quote does not match selected product/SKU')
        if bom.money(quote['price']['value'])!=bom.money(bom.fact(offers[0]['product'],'price_per_pack')) or quote['price']['currency']!=bom.fact(offers[0]['product'],'currency'):
            raise ValueError('Price changed; refresh product before collecting facts')
        if type(shipping_index) is not int or not 0 <= shipping_index < len(quote['options']):
            raise ValueError('Shipping index is outside the observed options')
        chosen=quote['options'][shipping_index]
        if chosen.get('sku_id')!=sku_id or chosen.get('quantity')!=quote['quantity']: raise ValueError('Malformed quote identity')
        observations.append({'id':'api-delivery','method':'platform_api','reviewed':True,**quote['source'],
                             'text':json.dumps(chosen,ensure_ascii=False),'facts':{'delivery':chosen}})
    result=enrich(offers,offer_id,{'sku_id':sku_id,'observed_at':now_iso(),'review_complete':False,
        'unresolved':['technical suitability','package contents and pack size']+([] if seller else ['seller'])+([] if quote else ['delivery']),
        'observations':observations})
    return result


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def assess(request, row, offer, p, current):
    card = offer["product"]
    result = {"offer_id": offer["offer_id"], "row_id": row["id"], "product_id": card.get("product_id"),
              "status": "needs_review", "blockers": [], "warnings": []}
    try:
        one = bom.plan({"items": [row], "currency": request.get("currency", "RUB")},
                       [{"row_id": row["id"], "product": card}])
        if one["blockers"]:
            raise ValueError(one["blockers"][0]["reason"])
        observed = timestamp(card["observed_at"])
        age = (current - observed).total_seconds() / 3600
        if not 0 <= age <= p["max_observation_age_hours"]:
            raise ValueError("stale_or_future_observation")
        if bom.fact(card, "matches_request") is not True:
            raise ValueError("technical_equivalence_not_confirmed")
        contents = bom.fact(card, "package_contents")
        if not isinstance(contents, list) or not contents or any(not isinstance(x, str) or not x.strip() for x in contents):
            raise ValueError("package_contents_required")
        sku_id, source_id = bom.fact(card, "sku_id"), bom.fact(card, "source_id")
        if not isinstance(sku_id, str) or not sku_id.isdigit() or type(source_id) is not int or source_id < 0:
            raise ValueError("invalid_sku_identity")
        line = one["lines"][0]
        shipping = checked_delivery(bom.fact(card, "delivery"), request["destination_key"], sku_id,
                                    line["purchase_quantity"], request.get("currency", "RUB"), current.date())
        earliest = dt.date.fromisoformat(shipping["earliest"])
        latest = dt.date.fromisoformat(shipping["latest"])
        if not current.date() <= earliest <= latest:
            raise ValueError("invalid_delivery_window")
        if shipping["currency"] != request.get("currency", "RUB"):
            raise ValueError("shipping_currency_mismatch")
        fee = bom.money(shipping["cost"])
        seller = bom.fact(card, "seller")
        if seller.get("rating_kind") != "seller" or not seller.get("id"):
            raise ValueError("seller_rating_required_not_product_rating")
        rating, scale = Decimal(str(seller["rating"])), Decimal(str(seller["scale"]))
        if not rating.is_finite() or scale not in (Decimal(5), Decimal(100)) or not 0 <= rating <= scale:
            raise ValueError("invalid_seller_rating")
        rating5 = rating * 5 / scale
        reviews = seller["review_count"]
        if reviews is not None and (type(reviews) is not int or reviews < 0):
            raise ValueError("seller_rating_sample_unknown")
        unit_price = bom.money(line["price_per_pack"]) / line["pack_size"]
        if unit_price <= 0:
            raise ValueError("zero_price_requires_review")
        total = bom.money(line["subtotal"]) + fee
        result.update(sku_id=sku_id, source_id=source_id, packs=line["purchase_quantity"],
                      pack_size=line["pack_size"], unit_price=str(unit_price),
                      goods_total=line["subtotal"], shipping_cost=amount(fee), delivered_total=amount(total),
                      delivery=shipping, seller=seller, seller_rating_5=str(rating5), title=card.get("title"),
                      variant=bom.fact(card, "variant"), package_contents=contents,
                      url=bom.variant_url(card["product_id"], sku_id))
        if rating5 < Decimal(p["min_seller_rating_5"]):
            result["blockers"].append("seller_rating_below_threshold")
        if (reviews or 0) < p["min_seller_reviews"]:
            result["blockers"].append("seller_history_too_small")
        if reviews is None or reviews < 20:
            result["warnings"].append("seller_rating_has_unknown_or_small_sample")
        deadline = row.get("deliver_by") or request.get("deliver_by")
        if deadline and latest > dt.date.fromisoformat(deadline):
            result["blockers"].append("delivery_after_deadline")
        if row.get("max_total") is not None and total > bom.money(row["max_total"]):
            result["blockers"].append("row_budget_exceeded")
        result["status"] = "rejected" if result["blockers"] else "comparable"
    except (KeyError, ValueError, TypeError, ArithmeticError) as error:
        result["blockers"].append(str(error) if isinstance(error, ValueError) else "missing_or_invalid_offer_data")
    return result


def compare(request, offers, current=None):
    current = current or dt.datetime.now(dt.timezone.utc)
    p = policy(request)
    if not isinstance(request.get("destination_key"), str) or not request["destination_key"].strip():
        raise ValueError("A real destination_key is required; do not infer it from IP")
    rows = request["items"]
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Nonempty request with unique row IDs required")
    if len({o["offer_id"] for o in offers}) != len(offers) or any(o["row_id"] not in {r["id"] for r in rows} for o in offers):
        raise ValueError("Duplicate offer or unknown row")
    output = []
    for row in rows:
        entries = [assess(request, row, o, p, current) for o in offers if o["row_id"] == row["id"]]
        market = {}
        # One quote per independent seller prevents many duplicate listings swamping the median.
        for e in entries:
            if e["status"] == "comparable":
                sid = e["seller"]["id"]
                market[sid] = min(market.get(sid, Decimal("Infinity")), Decimal(e["unit_price"]))
        anchor = median(market.values()) if market else None
        for e in entries:
            if e["status"] != "comparable":
                continue
            if len(market) < p["min_independent_sellers"]:
                e["blockers"].append("insufficient_independent_price_comparisons")
            if len(market) < p['target_independent_sellers']:
                e['warnings'].append('limited_independent_price_comparisons')
            if len(market) >= 2:
                ratio = Decimal(e["unit_price"]) / anchor
                e.update(price_ratio_to_median=str(ratio), market_unit_median=str(anchor))
                if ratio >= Decimal(p["high_price_factor"]):
                    e["blockers"].append("overpriced_outlier")
                elif ratio <= Decimal(p["low_price_factor"]):
                    e["blockers"].append("suspicious_low_price_check_configuration")
            e["status"] = "needs_review" if e["blockers"] else "eligible"
        # A split market may be mostly placeholder listings: gather more evidence, don't force a pick.
        if any("suspicious_low_price_check_configuration" in e["blockers"] for e in entries):
            for e in entries:
                if e["status"] == "eligible":
                    e["status"] = "needs_review"
                    e["blockers"].append("split_price_market_requires_more_comparables")
        eligible = [e for e in entries if e["status"] == "eligible"]
        recommended = None
        if eligible:
            cheapest = min(Decimal(e["delivered_total"]) for e in eligible)
            band = cheapest * (1 + Decimal(p["fast_delivery_premium"]))
            eligible.sort(key=lambda e: (Decimal(e["delivered_total"]) > band,
                e["delivery"]["latest"] if Decimal(e["delivered_total"]) <= band else "9999",
                Decimal(e["delivered_total"]), -Decimal(e["seller_rating_5"]), -(e["seller"]["review_count"] or 0)))
            for rank, e in enumerate(eligible, 1):
                e.update(rank=rank, within_fast_delivery_price_band=Decimal(e["delivered_total"]) <= band)
            recommended = eligible[0]["offer_id"]
        output.append({"row_id": row["id"], "recommended_offer_id": recommended,
                       "independent_sellers": len(market), "offers": entries})
    return {"schema_version": 1, "kind": "comparison", "observed_at": current.isoformat(), "policy": p,
            "status": "ready_for_selection" if all(r["recommended_offer_id"] for r in output) else "needs_review",
            "rows": output, "cart_url": CART_URL}


def compile_plan(request, offers, selections, current=None):
    review = compare(request, offers, current)
    lookup = {o["offer_id"]: o for o in offers}
    selection = {s["row_id"]: s for s in selections}
    if len(selection) != len(selections) or set(selection) != {r["id"] for r in request["items"]}:
        raise ValueError("Select exactly one offer for every requested row")
    chosen, assessments, blockers = [], {}, []
    for row in review["rows"]:
        pick = selection[row["row_id"]]
        if not isinstance(pick.get("reason"), str) or not pick["reason"].strip():
            raise ValueError("Agent must record a brief reason for the selection")
        entry = next((e for e in row["offers"] if e["offer_id"] == pick["offer_id"]), None)
        if entry is None or entry["status"] != "eligible":
            blockers.append({"row_id": row["row_id"], "reason": "selected_offer_not_eligible"})
            continue
        chosen.append({"row_id": row["row_id"], "product": lookup[pick["offer_id"]]["product"]})
        assessments[row["row_id"]] = entry
    base = bom.plan(request, chosen)
    blockers.extend(base["blockers"])
    cards = {s["row_id"]: s["product"] for s in chosen}
    lines = []
    seen_identities = set()
    for line in base["lines"]:
        rows = line["row_ids"]
        card, detail = cards[rows[0]], assessments[rows[0]]
        identity = (line["product_id"], detail["source_id"], detail["sku_id"])
        if identity in seen_identities:
            blockers.append({"reason": "one_live_sku_has_conflicting_variant_labels"})
        seen_identities.add(identity)
        if any((assessments[r]["source_id"], assessments[r]["sku_id"], assessments[r]["pack_size"],
                assessments[r]["delivery"]) != (detail["source_id"], detail["sku_id"], detail["pack_size"], detail["delivery"]) for r in rows):
            blockers.append({"reason": "conflicting_quotes_for_same_sku", "row_ids": rows})
        shipping = detail["delivery"]
        if shipping["quantity"] != line["purchase_quantity"] or "cart_delivery" in card["facts"]:
            try:
                quote = bom.fact(card, "cart_delivery")
                if type(quote.get("quantity")) is not int or quote["quantity"] < line["purchase_quantity"]:
                    raise ValueError("Delivery quote does not cover required packs")
                shipping = checked_delivery(quote, request["destination_key"], detail["sku_id"],
                    quote["quantity"], request.get("currency", "RUB"), timestamp(review["observed_at"]).date())
                if shipping["latest"] > detail["delivery"]["latest"] or shipping["method"] != detail["delivery"]["method"]:
                    raise ValueError("Delivery terms changed; reassess offers")
            except (KeyError, ValueError, TypeError):
                blockers.append({"reason": "refresh_delivery_quote_for_aggregated_quantity", "row_ids": rows})
        lines.append({**line, "sku_id": detail["sku_id"], "source_id": detail["source_id"],
                      "url": detail["url"],
                      "title": detail["title"], "delivery": shipping, "seller": detail["seller"],
                      "package_contents": detail["package_contents"], "max_unit_price": line["price_per_pack"],
                      "offer_ids": [selection[r]["offer_id"] for r in rows],
                      "selection_reasons": [selection[r]["reason"] for r in rows]})
    total = sum((bom.money(l["subtotal"]) + bom.money(l["delivery"]["cost"]) for l in lines), Decimal(0))
    if request.get("total_budget") is not None and total > bom.money(request["total_budget"]):
        blockers.append({"reason": "total_budget_exceeded"})
    return {"schema_version": 1, "kind": "shopping_plan", "status": "blocked" if blockers else "ready_for_cart",
            "created_at": review["observed_at"], "policy": review["policy"], "destination_key": request["destination_key"],
            "expires_at": min((timestamp(c["observed_at"]) + dt.timedelta(hours=review["policy"]["max_observation_age_hours"])
                               for c in cards.values()), default=timestamp(review["observed_at"])).isoformat(),
            "currency": request.get("currency", "RUB"), "lines": lines, "blockers": blockers,
            "quoted_total": amount(total), "total_budget": request.get("total_budget"), "cart_url": CART_URL,
            "checkout_authorized": False, "inputs_sha256": digest([request, offers, selections])}


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("draft"); d.add_argument("shortlist", type=Path); d.add_argument("--row-id", required=True); d.add_argument("--ids", nargs="+", required=True)
    h = sub.add_parser('collect'); h.add_argument('product',type=Path); h.add_argument('--row-id',required=True); h.add_argument('--sku-id',required=True)
    h.add_argument('--quote',type=Path); h.add_argument('--seller',type=Path); h.add_argument('--shipping-index',type=int,default=0)
    b = sub.add_parser("bind"); b.add_argument("offers", type=Path); b.add_argument("catalog", type=Path); b.add_argument("--offer-id", required=True); b.add_argument("--sku-id", required=True)
    b.add_argument("--new-offer-id", help="Clone this offer to compare another SKU from the same listing")
    e = sub.add_parser("enrich"); e.add_argument("offers", type=Path); e.add_argument("packet", type=Path); e.add_argument("--offer-id", required=True)
    m = sub.add_parser("merge"); m.add_argument("files", type=Path, nargs="+")
    c = sub.add_parser("compare"); c.add_argument("request", type=Path); c.add_argument("offers", type=Path)
    g = sub.add_parser("plan"); g.add_argument("request", type=Path); g.add_argument("offers", type=Path); g.add_argument("selections", type=Path)
    for parser in (d, h, b, e, m, c, g): parser.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    read = lambda path: json.loads(path.read_text())
    try:
        if a.command == "draft": result = draft(read(a.shortlist), a.row_id, a.ids)
        elif a.command=='collect': result=collect(read(a.product),a.row_id,a.sku_id,read(a.quote) if a.quote else None,read(a.seller) if a.seller else None,a.shipping_index)
        elif a.command == "bind": result = bind(read(a.offers), a.offer_id, read(a.catalog), a.sku_id, a.new_offer_id)
        elif a.command == "enrich": result = enrich(read(a.offers), a.offer_id, read(a.packet))
        elif a.command == "merge":
            result = [offer for path in a.files for offer in read(path)]
            if len({o["offer_id"] for o in result}) != len(result):
                raise ValueError("Duplicate offer ID across input files")
        elif a.command == "compare": result = compare(read(a.request), read(a.offers))
        else: result = compile_plan(read(a.request), read(a.offers), read(a.selections))
    except (ValueError, KeyError, TypeError, OSError) as error:
        p.error(str(error))
    save_json(a.output, result)
    print(json.dumps({"output": str(a.output.resolve()), "status": result.get("status") if isinstance(result, dict) else "draft"}))
    return 2 if isinstance(result, dict) and result.get("status") in ("blocked", "needs_review") else 0


if __name__ == "__main__": sys.exit(main())
