#!/usr/bin/env python3
"""Fill the cart from a reviewed shopping plan, preserve existing items, verify and link.

Uses the same transport and durable per-operation journal as ali_cart.py.
"""
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import ali_cart as cart
from session_store import binding, default_session
import bom
from ali_http import Client, ReadFailure, now
from shopping import checked_delivery, digest, timestamp


def validate_plan(plan):
    if plan.get("kind") != "shopping_plan" or plan.get("status") != "ready_for_cart" or plan.get("blockers"):
        cart.fail("plan_not_ready", "Compile an eligible shopping plan before cart mutation")
    current = dt.datetime.now(dt.timezone.utc)
    if not timestamp(plan["created_at"]) <= current <= timestamp(plan["expires_at"]):
        cart.fail("stale_plan", "Refresh expired offer observations and recompile")
    if not plan.get("lines"):
        cart.fail("invalid_plan", "Plan has no lines")
    keys = []
    for line in plan["lines"]:
        cart.identifier(line["product_id"], "product ID", True)
        cart.identifier(line["sku_id"], "SKU ID", True)
        cart.integer(line["source_id"], "source ID")
        bom.positive_int(line["purchase_quantity"], "purchase quantity")
        bom.positive_int(line["pack_size"], "pack size")
        bom.money(line["max_unit_price"])
        quote_quantity = bom.positive_int(line["delivery"]["quantity"], "delivery quote quantity")
        if quote_quantity < line["purchase_quantity"]:
            cart.fail("invalid_plan", "Delivery quote quantity is below planned purchase quantity")
        checked_delivery(line["delivery"], plan["destination_key"], line["sku_id"], quote_quantity, plan["currency"], current.date())
        keys.append(cart.sku_key(line))
    if len(set(keys)) != len(keys):
        cart.fail("invalid_plan", "Duplicate live SKU in compiled plan")


def find(snapshot, line):
    matches = [x for x in snapshot["items"] if cart.sku_key(x) == cart.sku_key(line)]
    if len(matches) > 1:
        cart.fail("ambiguous_sku", "Multiple cart rows match a planned SKU")
    return matches[0] if matches else None


def live_check(api, line, target):
    catalog = api.skus(line["product_id"], line["source_id"])
    matches = [s for s in catalog["skus"] if s["sku_id"] == line["sku_id"]]
    if len(matches) != 1:
        cart.fail("sku_changed", "Planned SKU is no longer uniquely available")
    sku = matches[0]
    options = {o["name"]: o["value"] for o in sku["options"]}
    if len(options) != len(sku["options"]) or options != line["variant"]["options"]:
        cart.fail("variant_changed", "Live SKU option labels differ from the reviewed variant")
    if sku["price"]["currency"] != line["currency"] or bom.money(sku["price"]["value"]) > bom.money(line["max_unit_price"]):
        cart.fail("price_changed", "Live price exceeds the reviewed price or currency changed")
    if sku["available_quantity"] < target:
        cart.fail("insufficient_stock", "Live SKU stock is insufficient for target cart quantity")
    return sku


def build_jobs(snapshot, plan, run_id):
    if snapshot.get("authenticated") is not True or snapshot.get("complete") is not True:
        cart.fail("incomplete_cart", "Need an authenticated complete cart baseline")
    jobs = []
    for line in plan["lines"]:
        old = find(snapshot, line)
        target = max(old["quantity"] if old else 0, line["purchase_quantity"])
        if line["delivery"]["quantity"] != target:
            cart.fail("delivery_quote_cart_quantity_mismatch", "Refresh cart_delivery for the actual target quantity including existing packs")
        if old:
            intent = {"operation": "update", "item_id": old["item_id"], "quantity": target}
        else:
            intent = {"operation": "add", "product_id": line["product_id"], "sku_id": line["sku_id"],
                      "source_id": line["source_id"], "quantity": target}
        jobs.append({"intent": intent, "target": target, "line": line,
                     "operation_id": run_id + "-" + digest(cart.sku_key(line))[:16]})
    return jobs


def final_check(baseline, after, jobs):
    issues, lines = [], []
    target_keys = {cart.sku_key(j["line"]) for j in jobs}
    before_other = {"items": [x for x in baseline["items"] if cart.sku_key(x) not in target_keys]}
    after_other = {"items": [x for x in after["items"] if cart.sku_key(x) not in target_keys]}
    if cart.state_rows(before_other) != cart.state_rows(after_other):
        issues.append("unrelated_cart_items_changed")
    for job in jobs:
        line, target = job["line"], job["target"]
        current = find(after, line)
        old = find(baseline, line)
        good = current is not None and current["quantity"] == target
        if old and current and old["selected"] != current["selected"]:
            good = False
        if current and current.get("available") is not True:
            good = False
        try:
            price_ok = bool(current and current["price"]["currency"] == line["currency"]
                and bom.money(current["price"]["value"]) <= bom.money(line["max_unit_price"]))
        except (KeyError, TypeError, ValueError):
            price_ok = False
        if not good or not price_ok:
            issues.append("sku_quantity_price_or_availability_mismatch:" + line["sku_id"])
        lines.append({"row_ids": line["row_ids"], "title": line["title"], "product_id": line["product_id"],
                      "url": bom.variant_url(line["product_id"], line["sku_id"]),
                      "sku_id": line["sku_id"], "variant": line["variant"], "package_contents": line["package_contents"],
                      "pack_size": line["pack_size"], "required_units": line["required_units"], "target_packs": target,
                      "actual_packs": current["quantity"] if current else 0,
                      "added_packs": target - (old["quantity"] if old else 0),
                      "price_per_pack": current.get("price") if current else None,
                      "delivery_quote": line["delivery"], "seller": line["seller"], "verified": good and price_ok})
    return {"status": "cart_verified" if not issues else "needs_review", "issues": issues, "lines": lines,
            "cart_url": cart.CART_URL, "checkout_performed": False,
            "delivery_is_quote": True,
            "shipping_selection_required": any(j['line']['delivery'].get('checkout_selected') is not True for j in jobs)}


def apply(api, plan, state_dir, run_id, session_hash, dry_run=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", run_id):
        cart.fail("invalid_run_id", "run-id must be 1..70 letters, digits, hyphen or underscore")
    validate_plan(plan)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_path = state_dir / ("batch-" + run_id + ".json")
    with (state_dir / ".shopping-plan.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            cart.fail("busy", "Another shopping plan is active")
        if run_path.exists():
            record = cart.read_json(run_path)
            if record["plan_hash"] != digest(plan) or record["session_hash"] != session_hash:
                cart.fail("run_conflict", "This run ID belongs to another plan or session export")
            baseline, jobs = record["baseline"], record["jobs"]
        else:
            baseline = api.read()
            jobs = build_jobs(baseline, plan, run_id)
            record = {"baseline": baseline, "jobs": jobs, "plan_hash": digest(plan), "session_hash": session_hash,
                      "next_index": 0, "expected": baseline, "status": "prepared", "results": []}
        # All proposed lines are checked before the first change; checked again per mutation.
        for job in jobs:
            live_check(api, job["line"], job["target"])
        if dry_run:
            return {"status": "dry_run", "run_id": run_id, "jobs": jobs, "cart_url": cart.CART_URL,
                    "baseline_fingerprint": cart.fingerprint(baseline), "mutation_sent": False}
        cart.save_json(run_path, record)
        for index in range(record["next_index"], len(jobs)):
            job = jobs[index]
            operation_path = state_dir / (job["operation_id"] + ".json")
            current = api.read()
            # A pending child journal must reconcile a possible successful request first.
            if not operation_path.exists() and cart.fingerprint(current) != cart.fingerprint(record["expected"]):
                return {"status": "needs_review", "reason": "cart_changed_between_operations", "run_id": run_id,
                        "cart_url": cart.CART_URL, "completed_lines": record["results"]}
            try:
                result = cart.execute(api, job["intent"], state_dir, job["operation_id"], session_hash,
                    preflight=lambda transport, snapshot: live_check(transport, job["line"], job["target"]))
            except ReadFailure as error:
                return {"status": "blocked", "reason": error.status, "run_id": run_id,
                        "completed_lines": record["results"], "cart_url": cart.CART_URL}
            if result["status"] not in ("verified", "noop", "already_completed"):
                return {"status": result["status"], "run_id": run_id, "operation": result,
                        "completed_lines": record["results"], "cart_url": cart.CART_URL}
            after = result["cart"]
            # Child confirmation does not excuse unrelated changes since the batch baseline.
            target_plan = {"target": job["line"], "quantity": job["target"]}
            if not cart.verify(record["expected"], after, target_plan):
                return {"status": "needs_review", "reason": "unexpected_batch_cart_change", "run_id": run_id,
                        "cart_url": cart.CART_URL, "completed_lines": record["results"]}
            record["results"].append({"operation_id": job["operation_id"], "status": result["status"], "row_ids": job["line"]["row_ids"]})
            record.update(next_index=index + 1, expected=after, status="in_progress")
            cart.save_json(run_path, record)
        final = api.read()
        result = final_check(baseline, final, jobs)
        result.update(run_id=run_id, observed_at=now(), quoted_total=plan["quoted_total"], currency=plan["currency"],
                      journal=str(run_path.resolve()), cart_fingerprint=cart.fingerprint(final))
        record.update(status=result["status"], result=result)
        cart.save_json(run_path, record)
        return result


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("plan", type=Path)
    p.add_argument("--session", type=Path, default=default_session())
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--state-dir", type=Path)
    p.add_argument("--run-id")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    run_id = a.run_id or str(uuid.uuid4())
    try:
        plan = cart.read_json(a.plan)
        with Client(a.session) as client:
            session_hash = binding(a.session)
            result = apply(cart.CartAPI(client), plan, a.state_dir or a.session.parent / "cart-state",
                           run_id, session_hash, a.dry_run)
    except ReadFailure as error:
        result = {"status": error.status, "reason": error.reason, "run_id": run_id, "cart_url": cart.CART_URL}
    except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
        result = {"status": "failed", "error_type": type(error).__name__, "run_id": run_id, "cart_url": cart.CART_URL}
    cart.save_json(a.output, result)
    print(json.dumps({"status": result["status"], "cart_url": cart.CART_URL, "output": str(a.output.resolve()), "run_id": run_id}))
    return 0 if result["status"] in ("cart_verified", "dry_run") else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
