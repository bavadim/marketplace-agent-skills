"""Map exact marketplace product/quote envelopes without upgrading unknown facts."""
from pathlib import Path
from style import load, money


def import_offer(params):
    source = Path(params["input"]).resolve()
    envelope = load(source)
    if envelope.get("ok") is not True or envelope.get("method") not in ("product", "quote", "review"):
        raise ValueError("Import a successful product/quote/review result, not a search snippet")
    product = envelope.get("data", {}).get("product")
    if not product or product.get("provider") not in ("ozon", "aliexpress"):
        raise ValueError("Expected an Ozon/AliExpress product envelope")
    sources = [source]
    if params.get("quote"):
        quote_source = Path(params["quote"]).resolve()
        quote_envelope = load(quote_source)
        quote = quote_envelope.get("data", {}).get("product")
        if quote_envelope.get("ok") is not True or not quote or quote.get("ref") != product.get("ref"):
            raise ValueError("Quote must describe the same exact variant ref")
        product = quote
        envelope = quote_envelope
        sources.append(quote_source)
    options = product.get("options", {})
    ambiguous = any(not isinstance(values, list) or len(values) != 1 for values in options.values())
    variant = "; ".join(f"{label}: {', '.join(str(v) for v in values)}" for label, values in options.items() if isinstance(values, list))
    price = product.get("price", {})
    ordinary = money(price.get("unit"))
    native_stock = product.get("stock")
    quantity = native_stock.get("quantity") if isinstance(native_stock, dict) else (native_stock if isinstance(native_stock, int) and not isinstance(native_stock, bool) else None)
    exact = product.get("variant_verified") is True and bool(variant) and not ambiguous
    item = {"name": product.get("title", ""), "category": params["category"], "ref": product["ref"],
            "url": product.get("url"), "variant": variant, "sku": product.get("variant_id"),
            "variant_verified": exact, "options": options,
            "price": {"ordinary": float(ordinary) if ordinary is not None else None,
                      "currency": price.get("currency"), "conditional": price.get("conditional"),
                      "reference": price.get("reference")},
            "stock": {"available": product.get("available"), "quantity": quantity,
                      "scope": "online" if exact and product.get("stage") != "search" else "unverified"},
            "delivery": product.get("delivery") or envelope.get("data", {}).get("delivery") or {},
            "seller": product.get("seller"), "product_rating": product.get("product_rating"),
            "observed_at": product.get("observed_at") or envelope.get("observed_at"),
            "evidence": [{"path": str(p), "claim": "Native exact-variant product/quote response"} for p in sources],
            "fit": params.get("fit", {"status": "provisional", "unresolved": ["Garment dimensions need comparison"]}),
            "image_candidates": product.get("images", []), "specifications": product.get("specifications", {}),
            "visual_notes": params.get("visual_notes", "")}
    if params.get("image_path"):
        item["image_path"] = str(Path(params["image_path"]).resolve())
    return {"item_id": params["id"], "item": item, "requires_fit_review": True,
            "warnings": product.get("warnings", []) + (["Selected options are incomplete/ambiguous"] if not exact else [])}
