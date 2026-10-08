#!/usr/bin/env python3
"""List actual SKU combinations and filter exact option labels; never infer variants from a title."""
import argparse
import json
import os
from pathlib import Path

import bom
from ali_cart import save_json
from ali_http import image_allowed


def matrix(catalog, options=None, quantity=1):
    if catalog.get('kind') == 'product' and isinstance(catalog.get('catalog'), dict):
        catalog = catalog['catalog']
    if catalog.get("status") != "ok" or catalog.get("kind") != "skus":
        raise ValueError("A successfully read SKU catalogue is required; a search card is not a catalogue")
    bom.positive_int(quantity, "quantity")
    options = options or {}
    if not isinstance(options, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           for k, v in options.items()):
        raise ValueError("--options must be a JSON object of exact observed option labels")
    rows, dimensions, ids, images = [], {}, set(), {}
    for sku in catalog["skus"]:
        sid = sku["sku_id"]
        url = bom.variant_url(catalog["product_id"], sid)
        if sid in ids:
            raise ValueError("Duplicate SKU in catalogue")
        ids.add(sid)
        values = {p["name"]: p["value"] for p in sku["options"]}
        if len(values) != len(sku["options"]) or any(not k or not v for k, v in values.items()):
            raise ValueError("Ambiguous SKU option names")
        if not values and len(catalog["skus"]) != 1:
            raise ValueError("Missing option labels in multi-SKU catalogue")
        for k, v in values.items():
            if v not in dimensions.setdefault(k, []):
                dimensions[k].append(v)
        stock = sku["available_quantity"]
        if type(stock) is not int or stock < 0:
            raise ValueError("Invalid stock quantity")
        price = sku["price"]
        bom.money(price["value"])
        if not price.get("currency"):
            raise ValueError("Missing currency")
        matches = all(values.get(k) == v for k, v in options.items())
        option_images = list(dict.fromkeys(p["image_url"] for p in sku["options"] if image_allowed(p.get("image_url"))))
        if matches:
            for url_image in option_images:
                entry = images.setdefault(url_image, {"url":url_image, "role":"sku_option", "sku_ids":[], "vision_status":"pending"})
                entry["sku_ids"].append(sid)
        rows.append({"sku_id": sid, "options": values, "price": price,
                     "available_quantity": stock, "in_stock_for_quantity": stock >= quantity,
                     "matches_options": matches, "url": url,
                     "technical_suitability_verified": False, "option_images":option_images})
    if set(options) - set(dimensions):
        raise ValueError("Unknown option dimension; read the unfiltered matrix first")
    matches = [r["sku_id"] for r in rows if r["matches_options"] and r["in_stock_for_quantity"]]
    return {"schema_version": 1, "kind": "variant_matrix", "product_id": catalog["product_id"],
            "observed_at": catalog["observed_at"], "source": catalog["source"],
            "status": "no_match" if not matches else "one_match" if options and len(matches) == 1 else "needs_choice",
            "dimensions": dimensions, "filters": options, "quantity": quantity,
            "variants": rows, "matching_sku_ids": matches, "selected_sku_id": None,
            "images":list(images.values()),
            "note": "Matching labels is not technical approval. Agent must select, bind and review the exact SKU."}


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("catalog", type=Path)
    p.add_argument("--options", default="{}", help='Exact labels, e.g. {"Power":"320W","Color":"3500K"}; use actual observed names')
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    try:
        result = matrix(json.loads(a.catalog.read_text()), json.loads(a.options), a.quantity)
    except (ValueError, KeyError, TypeError, OSError) as e:
        p.error(str(e))
    save_json(a.output, result)
    print(json.dumps({"status": result["status"], "variants": len(result["variants"]),
                      "matches": len(result["matching_sku_ids"]), "output": str(a.output.resolve())}))
    return 2 if result["status"] == "no_match" else 0


if __name__ == "__main__":
    raise SystemExit(main())
