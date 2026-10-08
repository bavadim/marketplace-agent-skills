#!/usr/bin/env python3
"""Run agent-authored shopping queries, merge duplicates, cache public search JSON."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

from ali_http import Client, ReadFailure, now, search
from ali_cart import save_json


def run(client, request, output_dir, max_requests=30):
    cache = Path(output_dir) / "search-cache"
    result = {"schema_version": 1, "status": "ok", "observed_at": now(), "rows": [], "requests_sent": 0}
    seen_rows = set()
    for row in request["items"]:
        if row["id"] in seen_rows:
            raise ValueError("Duplicate row ID")
        seen_rows.add(row["id"])
        queries = row.get("queries") or [row.get("query")]
        if not 1 <= len(queries) <= 5 or any(not isinstance(q, str) or not q.strip() for q in queries):
            raise ValueError("Each row needs 1..5 agent-authored queries")
        record = {"row_id": row["id"], "queries": [], "candidates": []}
        products = {}
        result["rows"].append(record)
        for query in dict.fromkeys(queries):
            key = hashlib.sha256(json.dumps([query, request.get("destination_key"), request.get("currency", "RUB")], ensure_ascii=False).encode()).hexdigest()
            path = cache / (key + ".json")
            data = None
            if path.exists():
                candidate = json.loads(path.read_text())
                age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(candidate["observed_at"])
                if dt.timedelta() <= age <= dt.timedelta(minutes=30) and candidate.get("status") in ("ok", "empty"):
                    data = candidate
            if data is None:
                if result["requests_sent"] >= max_requests:
                    result.update(status="partial", reason="max_requests")
                    return result
                try:
                    result["requests_sent"] += 1
                    data = search(client, query, 1)
                except ReadFailure as error:
                    result.update(status="partial", reason=error.status)
                    record["queries"].append({"query": query, "status": error.status})
                    return result  # No retry loop after access errors.
                save_json(path, data)
            record["queries"].append({"query": query, "status": data["status"], "count": data["count"]})
            if data["status"] not in ("ok", "empty"):
                result["status"] = "partial"
            for product in data["products"]:
                products.setdefault(product["product_id"], product)
            record["candidates"] = list(products.values())
    return result


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("request", type=Path)
    p.add_argument("--session")
    p.add_argument("--max-requests", type=int, default=30)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.max_requests <= 200:
        p.error("max-requests must be 1..200")
    with Client(a.session) as client:
        result = run(client, json.loads(a.request.read_text()), a.output.parent, a.max_requests)
    save_json(a.output, result)
    print(json.dumps({"status": result["status"], "rows": len(result["rows"]), "output": str(a.output.resolve())}))
    return 0 if result["status"] == "ok" else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
