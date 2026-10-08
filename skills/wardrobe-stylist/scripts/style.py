#!/usr/bin/env python3
"""Provider-neutral styling case API. No model calls or cart mutations."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import subprocess
import sys

SKILL = Path(__file__).resolve().parent.parent
METHODS = ["prepare", "offer.import", "fit.check", "outfits.check", "marketplace.call", "merchant.fetch", "merchant.inspect",
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


def marketplace_call(params):
    request = params["request"]
    if request.get("method") not in ("search", "product", "quote", "images", "review", "compare", "capabilities"):
        raise ValueError("This styling adapter only allows marketplace read/review operations")
    candidates = []
    if params.get("skill_path"):
        candidates.append(Path(params["skill_path"]))
    candidates.extend([SKILL.parent / "marketplace-shopping",
                       Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills" / "marketplace-shopping"])
    script = next((p / "scripts/shop.py" for p in candidates if (p / "scripts/shop.py").is_file()), None)
    if not script:
        return {"status": "skill_unavailable", "skill": "marketplace-shopping", "fallback": "web_search"}
    command = ([sys.executable, str(script), "capabilities"] if request.get("method") == "capabilities"
               else [sys.executable, str(script), "call", "--request", "-"])
    result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=params.get("timeout_seconds", 300))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ValueError(f"Marketplace returned no JSON; process exit {result.returncode}") from e


def dispatch(method, params):
    if method == "prepare": return prepare(params)
    if method == "offer.import":
        from offers import import_offer
        return import_offer(params)
    if method == "fit.check": return fit_check(params)
    if method == "outfits.check": return outfits_check(params)
    if method == "marketplace.call": return marketplace_call(params)
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


if __name__ == "__main__":
    if os.environ.get("_SKILL_PYTHON_LAUNCH_PID") != str(os.getpid()):
        shim = Path(__file__).with_name("python.sh")
        os.execv("/bin/bash", ["bash", str(shim), sys.executable, *sys.argv])
    main()
