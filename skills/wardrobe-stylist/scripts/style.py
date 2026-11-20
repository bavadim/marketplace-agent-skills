#!/usr/bin/env python3
"""Provider-neutral styling case API. No model calls or cart mutations."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parent.parent
METHODS = ["prepare", "offer.import", "fit.check", "outfits.check", "merchant.fetch", "merchant.inspect",
           "media.download", "render.prepare", "render.attach", "report"]
LABELS = {
    "height": ("Рост", "Height"), "weight": ("Вес", "Weight"),
    "chest": ("Грудь", "Chest"), "waist": ("Талия на уровне пояса", "Waist at waistband"),
    "hips": ("Бёдра", "Hips"), "shoulders": ("Плечи", "Shoulders"),
    "neck": ("Шея", "Neck"), "inseam": ("Шаговый шов", "Inseam"),
    "sleeve_outer": ("Рукав от плечевого шва", "Shoulder-seam sleeve"),
    "sleeve_inner": ("Внутренний шов рукава", "Inner sleeve"),
    "top_length": ("Длина верха от плеча", "Top length from shoulder"),
    "foot_length": ("Длина стопы", "Foot length")}
MEASURE_HELP = {
    "height": ("Без обуви: от пола до макушки у стены.", "Barefoot at a wall: floor to crown."),
    "chest": ("Лента горизонтально вокруг самой широкой части груди; руки расслаблены.", "Level tape around fullest chest; arms relaxed."),
    "waist": ("Вокруг тела там, где будет пояс брюк; укажи уровень пояса.", "Around intended waistband; record its level."),
    "hips": ("Вокруг самой широкой части ягодиц, ноги вместе.", "Around fullest seat, feet together."),
    "shoulders": ("Поперёк спины между точками плечевых швов; укажи, тело это или одежда.", "Across back between shoulder seam points; specify body or garment."),
    "neck": ("Вокруг основания шеи без сдавливания.", "Around base of neck without compression."),
    "inseam": ("На подходящих брюках: от пересечения швов в паху до низа по внутреннему шву.", "On fitting trousers: crotch seam intersection to hem along inside seam."),
    "sleeve_outer": ("На подходящем верхе: от плечевого шва до края манжеты.", "On a fitting top: shoulder seam to cuff."),
    "top_length": ("На подходящем верхе: от верхней точки плеча у горловины до подола.", "On a fitting top: highest shoulder point beside neck to hem."),
    "foot_length": ("Стоя: пятка у стены, расстояние до самого длинного пальца; обе стопы.", "Standing heel at wall: longest toe distance; measure both feet.")}
REQUIRED = {
    "top": ["chest", "shoulders", "top_length"],
    "outerwear": ["chest", "shoulders", "sleeve_outer"],
    "shirt": ["chest", "shoulders", "sleeve_outer", "neck"],
    "bottom": ["waist", "hips", "inseam"], "skirt": ["waist", "hips"],
    "dress": ["chest", "waist", "hips"], "shoes": []}


def now():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def output_path(path):
    p = Path(path).expanduser().resolve()
    if p == SKILL or SKILL in p.parents:
        raise ValueError("Case output must be outside the installed skill")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save(path, data):
    p = output_path(path)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)


def case_value(params):
    case = params.get("case")
    return load(case) if isinstance(case, str) else (case or {})


def ru(case):
    return case.get("request", {}).get("language", "en").startswith("ru")


def number(value, name="value"):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        n = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be numeric") from e
    if not math.isfinite(n) or n <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return n


def prepare(params):
    case = case_value(params)
    request = case.get("request", {})
    profile = case.get("profile", {})
    measurements = profile.get("measurements", [])
    language = 0 if ru(case) else 1
    rows, index, warnings = [], {}, []
    for m in measurements:
        mid = m["id"]
        number(m.get("value"), mid)
        if m.get("basis") not in ("body", "garment"):
            raise ValueError(f"Measurement {mid} needs body/garment basis")
        if m.get("unit") not in ("cm", "kg") or (mid == "weight") != (m["unit"] == "kg"):
            raise ValueError(f"Unsupported unit for {mid}; use cm, weight in kg")
        if not m.get("definition") or not m.get("source"):
            raise ValueError(f"Measurement {mid} needs definition and source")
        key = (mid, m["basis"], m["definition"])
        if key in index:
            raise ValueError(f"Duplicate measurement {mid}; apply corrections before prepare")
        index[key] = m
        rows.append({**m, "label": LABELS.get(mid, (mid, mid))[language]})
        if m.get("approximate") or m.get("confirmed") is False:
            warnings.append({"code": "measurement_uncertain", "id": mid})
    categories = request.get("categories", [])
    needed = {"height"} if any(c != "shoes" for c in categories) else set()
    for category in categories:
        if category not in REQUIRED:
            warnings.append({"code": "custom_category", "category": category})
        needed.update(REQUIRED.get(category, []))
    if "shoes" in categories and request.get("shoe_policy") != "nominal":
        needed.add("foot_length")
    present = {r["id"] for r in rows}
    missing = [{"id": m, "label": LABELS.get(m, (m, m))[language],
                "how": MEASURE_HELP[m][language]} for m in sorted(needed - present)]
    photos = profile.get("photos", [])
    photo_roles = {p.get("role") for p in photos if p.get("path") and Path(p["path"]).is_file()}
    for p in photos:
        if not p.get("path") or not Path(p["path"]).is_file():
            warnings.append({"code": "photo_missing_file", "role": p.get("role")})
    photo_gaps = [r for r in ("face", "body") if r not in photo_roles]
    questions = []
    if request.get("budget", {}).get("amount") is not None and request["budget"].get("scope") not in ("per_outfit", "capsule"):
        questions.append({"field": "budget.scope", "reason": "Separate alternatives and a shared capsule have different totals"})
    if not request.get("destination"):
        questions.append({"field": "destination", "reason": "Needed for actual delivery quotes"})
    criteria, jobs = [], []
    for criterion in case.get("criteria", []):
        criteria.append(criterion)
        for query in criterion.get("queries", []):
            jobs.append({"category": criterion["category"], "request": {"method": "search", "params": {
                "query": query, "providers": request.get("providers", ["ozon", "aliexpress"]),
                "limit": criterion.get("limit", 12)}}})
    return {"measurements": rows, "missing_measurements": missing, "missing_photos": photo_gaps,
            "questions": questions, "warnings": warnings, "criteria": criteria, "search_jobs": jobs,
            "personalized_render_ready": not photo_gaps, "fit_guaranteed": False}


def dimension(d):
    n = number(d.get("value"), "dimension.value")
    if d.get("unit", "cm") != "cm":
        raise ValueError("Normalize dimension units to cm before fit.check")
    kind = d.get("kind", "length")
    if kind == "flat_width":
        return n * 2, "circumference"
    if kind not in ("length", "circumference"):
        raise ValueError("dimension.kind must be length/circumference/flat_width")
    return n, kind


def fit_check(params):
    results = []
    for c in params.get("comparisons", []):
        base = {"id": c.get("id"), "status": "unknown"}
        target, offer = c.get("target"), c.get("offer")
        if not target or not offer:
            results.append({**base, "reason": "missing_dimension"}); continue
        tv, tk = dimension(target)
        ov, ok = dimension(offer)
        if not target.get("definition") or target.get("definition") != offer.get("definition") or tk != ok:
            results.append({**base, "reason": "incomparable_endpoints_or_dimension"}); continue
        if not target.get("source") or not offer.get("source"):
            results.append({**base, "reason": "missing_evidence_source"}); continue
        if offer.get("evidence_scope") != "exact_variant" or offer.get("estimated"):
            results.append({**base, "reason": "generic_chart_or_estimate"}); continue
        tb, ob = target.get("basis"), offer.get("basis")
        if tb not in ("body", "garment") or ob not in ("body", "garment"):
            results.append({**base, "reason": "missing_measurement_basis"}); continue
        if tb != ob and c.get("ease_cm") is None:
            results.append({**base, "reason": "explicit_ease_required"}); continue
        delta = ov - tv
        desired = c.get("ease_cm", 0)
        if isinstance(desired, bool) or not isinstance(desired, (int, float)) or not math.isfinite(desired):
            raise ValueError("ease_cm must be finite")
        low = float(c.get("tolerance_minus_cm", 0))
        high = float(c.get("tolerance_plus_cm", 0))
        if low < 0 or high < 0 or not math.isfinite(low + high):
            raise ValueError("Tolerances must be finite and nonnegative")
        if desired - low <= delta <= desired + high:
            status = "within_requested_range"
        else:
            status = "mismatch"
        if target.get("approximate") or offer.get("approximate"):
            status = "provisional"  # Approximate facts cannot establish a definite mismatch.
        results.append({**base, "status": status, "target_cm": tv, "offer_cm": ov,
                        "delta_cm": round(delta, 3), "desired_delta_cm": desired})
    states = {r["status"] for r in results}
    overall = "mismatch" if "mismatch" in states else ("candidate" if results and states == {"within_requested_range"} else "provisional")
    return {"status": overall, "comparisons": results, "fit_guaranteed": False}


def money(value):
    if value is None:
        return None
    try:
        n = Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError("Invalid monetary amount") from e
    if not n.is_finite() or n < 0 or isinstance(value, bool):
        raise ValueError("Money must be finite and nonnegative")
    return n


def total(ids, items):
    amounts, unknown = {}, []
    for iid in ids:
        price = items[iid].get("price", {})
        amount = money(price.get("ordinary"))
        currency = price.get("currency")
        quantity = items[iid].get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError("Item quantity must be a positive integer")
        if amount is None or not currency:
            unknown.append(iid)
        else:
            amounts[currency] = amounts.get(currency, Decimal(0)) + amount * quantity
    return {"goods_by_currency": {k: float(v) for k, v in sorted(amounts.items())},
            "unknown_price_items": unknown, "single_currency_complete": len(amounts) == 1 and not unknown,
            "delivery_included": False}


def outfits_check(params):
    case = case_value(params)
    items, outfits = case.get("items", {}), case.get("outfits", [])
    errors, warnings, results, used, seen = [], [], [], set(), set()
    if len(outfits) != 3:
        errors.append({"code": "requires_three_outfits", "count": len(outfits)})
    concepts = []
    stamp = params.get("as_of") or now()
    for outfit in outfits:
        oid = outfit.get("id")
        if not oid or oid in seen:
            errors.append({"code": "duplicate_or_missing_outfit_id", "id": oid})
        seen.add(oid)
        concept = outfit.get("style_summary", "").strip()
        if not concept or concept in concepts:
            errors.append({"code": "distinct_style_description_required", "id": oid})
        concepts.append(concept)
        ids = outfit.get("items", [])
        if not ids or len(ids) != len(set(ids)):
            errors.append({"code": "empty_or_duplicate_items", "id": oid})
        valid_ids = [i for i in ids if i in items]
        missing_ids = set(ids) - set(items)
        if missing_ids:
            errors.append({"code": "unknown_items", "id": oid, "items": sorted(missing_ids)})
        categories = {items[i].get("category") for i in valid_ids}
        missing_categories = set(outfit.get("required_categories", case.get("request", {}).get("categories", []))) - categories
        if missing_categories:
            errors.append({"code": "missing_outfit_categories", "id": oid, "categories": sorted(missing_categories)})
        for iid in valid_ids:
            item = items[iid]
            if iid in used:
                continue
            used.add(iid)
            if not item.get("url", "").startswith(("https://", "http://")) or not item.get("variant"):
                errors.append({"code": "exact_variant_and_link_required", "item": iid})
            if item.get("variant_verified") is not True:
                errors.append({"code": "variant_unverified", "item": iid})
            stock = item.get("stock", {})
            if stock.get("available") is not True or stock.get("scope") != "online":
                errors.append({"code": "online_variant_stock_unverified", "item": iid})
            quantity = item.get("quantity", 1)
            if stock.get("quantity") is not None and stock["quantity"] < quantity:
                errors.append({"code": "insufficient_stock", "item": iid})
            if not item.get("evidence") or not item.get("observed_at"):
                errors.append({"code": "dated_evidence_required", "item": iid})
            else:
                try:
                    when = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
                    age = (datetime.fromisoformat(stamp.replace("Z", "+00:00")) - when).total_seconds() / 3600
                    if age > params.get("max_age_hours", 24) or age < -0.05:
                        warnings.append({"code": "refresh_offer", "item": iid, "age_hours": round(age, 2)})
                except (ValueError, TypeError):
                    errors.append({"code": "invalid_evidence_timestamp", "item": iid})
            fit = item.get("fit", {})
            if fit.get("status") == "mismatch":
                errors.append({"code": "known_fit_mismatch", "item": iid})
            elif fit.get("status") != "candidate" or fit.get("unresolved"):
                warnings.append({"code": "fit_provisional", "item": iid, "details": fit.get("unresolved", [])})
            delivery = item.get("delivery", {})
            requested = case.get("request", {}).get("destination")
            if delivery.get("verified") is not True or (requested and delivery.get("destination") != requested):
                warnings.append({"code": "delivery_unverified", "item": iid})
            if delivery.get("cost") is None:
                warnings.append({"code": "delivery_cost_unknown", "item": iid})
        computed = total(valid_ids, items)
        if computed["unknown_price_items"]:
            warnings.append({"code": "price_unknown", "id": oid, "details": computed["unknown_price_items"]})
        if len(computed["goods_by_currency"]) > 1:
            warnings.append({"code": "mixed_currencies", "id": oid})
        results.append({"id": oid, **computed})
    capsule = total(sorted(used), items)
    budget = case.get("request", {}).get("budget", {})
    budget_status = {"scope": budget.get("scope"), "status": "unknown"}
    if budget.get("amount") is not None and budget.get("currency") and budget.get("scope") in ("per_outfit", "capsule"):
        targets = results if budget["scope"] == "per_outfit" else [{"id": "capsule", **capsule}]
        limit = money(budget["amount"])
        assessments = []
        for t in targets:
            amounts = t["goods_by_currency"]
            value = amounts.get(budget["currency"])
            state = "unknown" if not t["single_currency_complete"] or value is None else ("over" if Decimal(str(value)) > limit else "within_goods_budget")
            assessments.append({"id": t["id"], "status": state, "delivery_excluded": True})
            if state == "over":
                errors.append({"code": "goods_over_budget", "id": t["id"]})
        budget_status = {"scope": budget["scope"], "assessments": assessments}
    else:
        warnings.append({"code": "budget_scope_or_amount_unknown"})
    return {"ready_for_shopping_renders": not errors, "errors": errors, "warnings": warnings,
            "outfits": results, "capsule": capsule, "budget": budget_status,
            "observed_at": stamp, "fit_guaranteed": False,
            "requires_visual_style_review": True}


def dispatch(method, params):
    if method == "prepare": return prepare(params)
    if method == "offer.import":
        from offers import import_offer
        return import_offer(params)
    if method == "fit.check": return fit_check(params)
    if method == "outfits.check": return outfits_check(params)
    if method in ("merchant.fetch", "merchant.inspect", "media.download"):
        from web import fetch, inspect_merchant, download
        return {"merchant.fetch": fetch, "merchant.inspect": inspect_merchant, "media.download": download}[method](params)
    if method in ("render.prepare", "render.attach", "report"):
        from media import render_prepare, render_attach, report
        return {"render.prepare": render_prepare, "render.attach": render_attach, "report": report}[method](params)
    raise ValueError(f"Unknown method: {method}")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    call = sub.add_parser("call")
    call.add_argument("--request", required=True)
    call.add_argument("--output")
    args = parser.parse_args()
    if args.command == "capabilities":
        print(json.dumps({"schema_version": 1, "methods": METHODS,
                          "image_generation": "agent invokes native tool; scripts do not call paid APIs"})); return
    method = None
    try:
        request = json.load(sys.stdin) if args.request == "-" else load(args.request)
        method = request["method"]
        data = dispatch(method, request.get("params", {}))
        result = {"schema_version": 1, "method": method, "ok": True, "observed_at": now(), "data": data}
    except Exception as e:
        result = {"schema_version": 1, "method": method, "ok": False, "error": {"type": type(e).__name__, "message": str(e)}}
    if args.output:
        save(args.output, result)
        print(json.dumps({"ok": result["ok"], "method": method, "output": str(Path(args.output).resolve())}, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


def _self_test():
    import unittest
    import tempfile
    import media
    import offers
    style = sys.modules[__name__]

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

    suite = unittest.TestSuite()
    for case in (Measurements, Fit, Outfits, Media, OfferImport):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    return 0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(_self_test())
    if os.environ.get("_SKILL_PYTHON_LAUNCH_PID") != str(os.getpid()):
        shim = Path(__file__).with_name("python.sh")
        os.execv("/bin/bash", ["bash", str(shim), sys.executable, *sys.argv])
    main()
