"""Unmodified reference boards, native render requests and shopping reports."""
from html import escape
from pathlib import Path
import shutil

from style import case_value, LABELS, now, outfits_check, output_path, prepare, ru, save


def versioned(path):
    path = output_path(path)
    if not path.exists():
        return path
    for n in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-v{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("Too many output versions")


def outfit_by_id(case, oid):
    outfit = next((o for o in case.get("outfits", []) if o["id"] == oid), None)
    if outfit is None:
        raise ValueError(f"Unknown outfit ID: {oid}")
    return outfit


def board(items, path):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    columns = min(3, len(items))
    width, height = 440, 620
    rows = (len(items) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * width, rows * height), "white")
    draw = ImageDraw.Draw(canvas)
    font_path = next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf") if Path(p).is_file()), None)
    font = ImageFont.truetype(font_path, 17) if font_path else ImageFont.load_default()
    for n, (iid, item) in enumerate(items):
        x, y = n % columns * width, n // columns * height
        with Image.open(item["image_path"]) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((width - 24, height - 100))
            canvas.paste(image, (x + (width - image.width) // 2, y + 8 + (height - 110 - image.height) // 2))
        text = f"{iid}: {item['name']}\n{item['variant']}"
        lines = []
        for line in text.splitlines():
            words, current = line.split(), ""
            for word in words:
                trial = f"{current} {word}".strip()
                if draw.textlength(trial, font=font) > width - 24 and current:
                    lines.append(current); current = word
                else:
                    current = trial
            lines.append(current)
        draw.multiline_text((x + 12, y + height - 88), "\n".join(lines[:4]), font=font, fill="#222222", spacing=2)
    canvas.save(path, quality=92)


def render_prepare(params):
    case = case_value(params)
    check = outfits_check({"case": case, "as_of": params.get("as_of") or now()})
    if check["errors"] and not params.get("allow_concept"):
        raise ValueError("Resolve outfits.check errors before shopping renders: " + ", ".join(sorted({e["code"] for e in check["errors"]})))
    outfit = outfit_by_id(case, params["outfit_id"])
    selected = [(iid, case["items"][iid]) for iid in outfit["items"]]
    if len(selected) > 12:
        raise ValueError("Use at most 12 visible garments per render or create a focused reference selection")
    photos = case.get("profile", {}).get("photos", [])
    identity = []
    for role in ("face", "body"):
        photo = next((p for p in photos if p.get("role") == role and Path(p.get("path", "")).is_file()), None)
        if not photo:
            raise ValueError(f"Missing {role} identity photo; continue shopping and request the photo")
        identity.append(str(Path(photo["path"]).resolve()))
    directory = output_path(Path(params["directory"]) / "placeholder").parent
    boards = []
    for n in range(0, len(selected), 6):
        path = versioned(directory / f"{outfit['id']}-board-{n // 6 + 1}.jpg")
        board(selected[n:n + 6], path)
        boards.append(str(path))
    refs = identity + boards
    roles = ["user face identity reference", "same user's full-body physique reference"]
    roles += ["labeled original merchant garment photos; product references only" for _ in boards]
    if params.get("anchor"):
        anchor = Path(params["anchor"]).resolve()
        if not anchor.is_file():
            raise ValueError("Render anchor file does not exist")
        refs.insert(0, str(anchor))
        roles.insert(0, "approved prior render: preserve the same identity, pose, camera, lighting and background; replace clothing only")
    if len(refs) > 5:
        raise ValueError("Native request exceeds five reference images")
    measurements = case.get("profile", {}).get("measurements", [])
    context = "; ".join(f"{m['id']}={m['value']} {m['unit']}" for m in measurements if m.get("basis") == "body" and m["id"] in ("height", "weight", "chest", "waist", "hips"))
    garments = "\n".join(f"- {iid}: {item['name']}; exact variant {item['variant']}. {item.get('visual_notes', '')}" for iid, item in selected)
    scene = case.get("render_scene", "A neutral softly lit studio, full-length frontal relaxed stance, head and both shoes visible with margin, natural photographic perspective, no text or collage")
    prompt = ("Use case: identity-preserve virtual clothing try-on.\n"
              "Input roles: " + "; ".join(f"image {i + 1}: {role}" for i, role in enumerate(roles)) + ".\n"
              "Preserve the same person from the identity photos: recognizable face, apparent age, hair, skin texture and natural body proportions. "
              "Do not slim, elongate, muscularize, beautify or replace them with a retailer model. Retailer faces and poses are irrelevant.\n"
              f"User-provided body context (not a measured fit simulation): {context or 'preserve reference proportions'}.\n"
              f"Outfit direction: {outfit['style_summary']}.\nExact garments:\n{garments}\n"
              "Match original garment colors, fabric texture, cut, buttons, pockets and known lengths. Do not extend short outerwear or add accessories not listed. "
              "Allow natural layering and fabric drape, without pretending unknown dimensions are verified.\n"
              f"Scene and framing: {scene}.\nThis is an approximate styling visualization, not evidence of fit.")
    request = {"prompt": prompt, "referenced_image_paths": refs, "transparent_background": False}
    request_path = versioned(directory / f"{outfit['id']}-request.json")
    save(request_path, request)
    return {"outfit_id": outfit["id"], "tool": "image_gen", "request": request, "request_path": str(request_path),
            "reference_boards": boards, "shopping_verified": not check["errors"],
            "inspect_inputs_before_generation": refs, "generator_called": False}


def render_attach(params):
    case_path = Path(params["case"]).resolve()
    case = case_value(params)
    outfit = outfit_by_id(case, params["outfit_id"])
    review = params.get("visual_review", {})
    if not all(review.get(k) is True for k in ("identity", "garments", "framing")):
        raise ValueError("Attach only after visual identity, garment and framing checks pass")
    source = Path(params["source_path"]).resolve()
    from PIL import Image
    with Image.open(source) as image:
        image.verify()
    request_path = Path(params["request_path"]).resolve()
    if not request_path.is_file():
        raise ValueError("Save the actual request before attaching the generated output")
    directory = Path(params.get("directory", case_path.parent / "renders"))
    destination = versioned(directory / f"{outfit['id']}{source.suffix or '.png'}")
    shutil.copy2(source, destination)
    outfit["render"] = {"path": str(destination), "request_path": str(request_path),
                        "mode": params.get("mode", "built-in image_gen"), "observed_at": now(),
                        "visual_review": review, "fit_guaranteed": False}
    save(case_path, case)
    return {"case_path": str(case_path), "render": outfit["render"]}


def md(value):
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def formatted_money(value, currency, russian):
    if value is None or not currency:
        return "неизвестна" if russian else "unknown"
    amount = f"{float(value):,.2f}".rstrip("0").rstrip(".")
    if russian:
        amount = amount.replace(",", "\u202f").replace(".", ",")
    return f"{amount} {currency}"


def totals_text(result, russian):
    parts = [formatted_money(v, k, russian) for k, v in result["goods_by_currency"].items()]
    if result["unknown_price_items"]:
        parts.append(("часть цен неизвестна" if russian else "some prices unknown"))
    return " + ".join(parts) or ("неизвестно" if russian else "unknown")


def delivery_text(item, case):
    russian = ru(case)
    delivery = item.get("delivery", {})
    destination = case.get("request", {}).get("destination")
    if not delivery.get("verified") or (destination and delivery.get("destination") != destination):
        return "Не подтверждена" if russian else "Unverified"
    window = " — ".join(str(delivery.get(k)) for k in ("earliest", "latest") if delivery.get(k))
    cost = formatted_money(delivery.get("cost"), delivery.get("currency"), russian)
    return f"{delivery.get('destination', '')}; {window or ('дата неизвестна' if russian else 'date unknown')}; {cost}"


def notice_text(notice, case):
    texts = {
        "requires_three_outfits": ("Нужно три образа", "Three outfits required"),
        "duplicate_or_missing_outfit_id": ("Неоднозначный идентификатор образа", "Ambiguous outfit ID"),
        "distinct_style_description_required": ("Нужно описать разные стили", "Distinct style descriptions needed"),
        "empty_or_duplicate_items": ("Проверь состав комплекта", "Check outfit contents"),
        "unknown_items": ("Нет данных о вещи", "Item details missing"),
        "missing_outfit_categories": ("Не хватает части комплекта", "Outfit category missing"),
        "exact_variant_and_link_required": ("Нужны вариант и ссылка", "Exact variant and link needed"),
        "variant_unverified": ("Выбранный вариант не проверен", "Selected variant unverified"),
        "online_variant_stock_unverified": ("Наличие для онлайн-заказа не подтверждено", "Online variant stock unverified"),
        "insufficient_stock": ("Недостаточно товара", "Insufficient stock"),
        "dated_evidence_required": ("Не хватает источника с датой проверки", "Dated evidence missing"),
        "invalid_evidence_timestamp": ("Дата проверки некорректна", "Invalid observation date"),
        "refresh_offer": ("Нужно обновить наличие и цену", "Refresh stock and price"),
        "known_fit_mismatch": ("Есть несовместимость по меркам", "Known dimension mismatch"),
        "fit_provisional": ("Посадку нужно проверить примеркой или мерками изделия", "Fit needs try-on or garment measurements"),
        "delivery_unverified": ("Доставка до выбранного города не подтверждена", "Destination delivery unverified"),
        "delivery_cost_unknown": ("Стоимость доставки неизвестна", "Shipping cost unknown"),
        "price_unknown": ("Часть цен неизвестна", "Some prices unknown"),
        "mixed_currencies": ("Цены в разных валютах нельзя сложить без пересчёта", "Mixed currencies cannot be summed without conversion"),
        "goods_over_budget": ("Стоимость вещей превышает бюджет", "Goods exceed budget"),
        "budget_scope_or_amount_unknown": ("Нужно уточнить бюджет и его область", "Budget amount or scope needs clarification")}
    label = texts.get(notice["code"], (notice["code"], notice["code"]))[0 if ru(case) else 1]
    iid = notice.get("item")
    subject = case.get("items", {}).get(iid, {}).get("name", iid or notice.get("id", ""))
    details = "; ".join(notice.get("details", []))
    return ": ".join(str(x) for x in (subject, label, details) if x)


def report(params):
    case = case_value(params)
    check = outfits_check({"case": case, "as_of": params.get("as_of") or now()})
    if check["errors"] and not params.get("allow_incomplete"):
        raise ValueError("Resolve outfit validation errors, or explicitly use allow_incomplete for a provisional report")
    prepared = prepare({"case": case})
    russian = ru(case)
    directory = output_path(Path(params["directory"]) / "placeholder").parent
    title = "Подбор гардероба" if russian else "Wardrobe selection"
    disclaimer = ("Приблизительная визуализация стиля; посадка не гарантирована. Цены обычные, доставка и подгонка не включены." if russian else
                  "Approximate styling visualization; fit is not guaranteed. Ordinary prices; shipping and alterations excluded.")
    budget = case.get("request", {}).get("budget", {})
    budget_scope = {"per_outfit": "на один образ" if russian else "per alternative", "capsule": "на все уникальные вещи" if russian else "for the shared capsule"}.get(budget.get("scope"), "область не уточнена" if russian else "scope unknown")
    budget_line = ("Бюджет: " if russian else "Budget: ") + formatted_money(budget.get("amount"), budget.get("currency"), russian) + "; " + budget_scope
    lines = [f"# {title}", "", disclaimer, "", budget_line, "", f"{'Проверено' if russian else 'Observed'}: {check['observed_at']}", ""]
    if check["errors"]:
        lines += ["**" + ("Предварительный подбор — есть неподтверждённые предложения." if russian else "Provisional selection — some offers are unverified.") + "**", ""]
    columns = ["Мерка", "Значение", "Тело/изделие", "Метод", "Источник", "Точность"] if russian else ["Measurement", "Value", "Basis", "Definition", "Source", "Precision"]
    measurement_rows = []
    for m in prepared["measurements"]:
        basis = {"body": "тело", "garment": "изделие"}.get(m["basis"], m["basis"]) if russian else m["basis"]
        precision = ("примерно" if russian else "approximate") if m.get("approximate") else ("сообщено" if russian else "reported")
        if m.get("confirmed") is False:
            precision += "; " + ("нужно уточнить" if russian else "needs clarification")
        measurement_rows.append([m["label"], f"{m['value']} {m['unit']}", basis, m["definition"], m["source"], precision])
    def table(headers, rows):
        return ["| " + " | ".join(md(v) for v in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] + ["| " + " | ".join(md(v) for v in row) + " |" for row in rows]
    lines += table(columns, measurement_rows) + [""]
    for gap in prepared["missing_measurements"]:
        lines += [f"- {gap['label']}: {gap['how']}"]
    if prepared["missing_measurements"]:
        lines.append("")
    criteria_rows = [[c.get("category"), c.get("requirements", ""), "; ".join(c.get("queries", []))] for c in case.get("criteria", [])]
    lines += ["## " + ("Критерии поиска" if russian else "Search criteria"), ""] + table(["Категория", "Требования", "Запросы"] if russian else ["Category", "Requirements", "Queries"], criteria_rows) + [""]
    cards = []
    render_missing = []
    render_unreviewed = []
    item_columns = ["Вещь", "Вариант", "Цена", "Доставка", "Посадка", "Ссылка"] if russian else ["Item", "Variant", "Price", "Delivery", "Fit", "Link"]
    for outfit in case.get("outfits", []):
        lines += [f"## {outfit['title']}", "", outfit.get("style_summary", ""), "", outfit.get("note", ""), ""]
        render = outfit.get("render", {})
        render_path = render.get("path") if isinstance(render, dict) else None
        if render_path and Path(render_path).is_file():
            if not all(render.get("visual_review", {}).get(k) is True for k in ("identity", "garments", "framing")):
                render_unreviewed.append(outfit["id"])
            lines += [f"![{md(outfit['title'])}](<{Path(render_path).resolve()}>)", ""]
            image_html = f'<img class="render" src="{escape(str(Path(render_path).resolve()), quote=True)}" alt="{escape(outfit["title"], quote=True)}">'
        else:
            render_missing.append(outfit["id"])
            image_html = '<p class="gap">' + ("Рендер ещё не создан" if russian else "Render not created") + '</p>'
        rows, html_rows = [], []
        for iid in outfit.get("items", []):
            if iid not in case.get("items", {}):
                continue
            item = case["items"][iid]
            price = item.get("price", {})
            fit = item.get("fit", {})
            fit_text = fit.get("note", fit.get("status", "unknown"))
            if fit.get("unresolved"):
                fit_text += "; " + "; ".join(fit["unresolved"])
            variant = item.get("variant")
            if item.get("selection_instructions"):
                variant = str(variant) + "; " + item["selection_instructions"]
            item_price = formatted_money(price.get("ordinary"), price.get("currency"), russian)
            if item.get("quantity", 1) != 1:
                item_price += f" × {item['quantity']}"
            cells = [item["name"], variant, item_price, delivery_text(item, case), fit_text]
            url = item.get("url", "")
            valid_url = url.startswith(("http://", "https://"))
            link_label = "Открыть" if russian else "Open"
            link = f"[{link_label}](<{url}>)" if valid_url else "—"
            rows.append(cells + [link])
            hlink = f'<a href="{escape(url, quote=True)}">{link_label}</a>' if valid_url else "—"
            html_rows.append("<tr>" + "".join(f"<td>{escape(str(c if c is not None else '—'))}</td>" for c in cells) + f"<td>{hlink}</td></tr>")
        subtotal = next((t for t in check["outfits"] if t["id"] == outfit["id"]), {"goods_by_currency": {}, "unknown_price_items": []})
        total_text = ("Вещи: " if russian else "Goods: ") + totals_text(subtotal, russian)
        lines += table(item_columns, rows) + ["", f"**{total_text}**", ""]
        cards.append(f'<section><h2>{escape(outfit["title"])}</h2><p>{escape(outfit.get("style_summary", ""))}</p>{image_html}<p>{escape(outfit.get("note", ""))}</p><div class="scroll"><table><thead><tr>' + "".join(f"<th>{escape(c)}</th>" for c in item_columns) + "</tr></thead><tbody>" + "".join(html_rows) + f'</tbody></table></div><strong>{escape(total_text)}</strong></section>')
    capsule_text = ("Все уникальные вещи: " if russian else "All unique pieces: ") + totals_text(check["capsule"], russian)
    lines += [f"**{capsule_text}**", ""]
    notices = check["errors"] + check["warnings"]
    if render_unreviewed:
        lines += [("Нужна визуальная проверка рендеров: " if russian else "Renders need visual review: ") + ", ".join(render_unreviewed), ""]
    if notices:
        lines += ["## " + ("Осталось проверить" if russian else "Remaining checks"), ""]
        lines += [f"- {md(notice_text(n, case))}" for n in notices]
    measurement_html = "<table><thead><tr>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in measurement_rows) + "</tbody></table>"
    criteria_html = "<ul>" + "".join(f"<li><strong>{escape(str(c[0]))}</strong>: {escape(str(c[1]))}; {escape(str(c[2]))}</li>" for c in criteria_rows) + "</ul>"
    notice_html = "<ul>" + "".join(f"<li>{escape(notice_text(n, case))}</li>" for n in notices) + "</ul>"
    status_banner = "<p><strong>" + ("Предварительный подбор: предложения требуют проверки." if russian else "Provisional selection: offers require verification.") + "</strong></p>" if check["errors"] else ""
    html = f'''<!doctype html><html lang="{'ru' if russian else 'en'}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>body{{font:16px/1.45 system-ui;margin:32px;background:#f5f3ee;color:#242424}}h1,h2{{line-height:1.2}}.looks{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}}section{{padding:20px;background:white;border-radius:12px;min-width:0}}.render{{width:100%;height:auto;border-radius:8px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}a{{color:#164c81}}.scroll{{overflow:auto}}.gap{{padding:70px 10px;background:#eee}}@media(max-width:1100px){{.looks{{grid-template-columns:1fr}}}}</style><h1>{escape(title)}</h1><p>{escape(disclaimer)}</p><p>{escape(budget_line)}</p><p>{escape(check['observed_at'])}</p><details><summary>{'Мерки и критерии' if russian else 'Measurements and criteria'}</summary><div class="scroll">{measurement_html}</div>{criteria_html}</details><div class="looks">{''.join(cards)}</div><p><strong>{escape(capsule_text)}</strong></p><details><summary>{'Проверки' if russian else 'Checks'}</summary>{notice_html}</details></html>'''
    markdown_path, gallery_path = output_path(directory / "report.md"), output_path(directory / "gallery.html")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    html = html.replace(f"<h1>{escape(title)}</h1>", f"<h1>{escape(title)}</h1>{status_banner}", 1)
    gallery_path.write_text(html, encoding="utf-8")
    validation_path = save(directory / "validation.json", {**check, "renders_missing": render_missing, "renders_unreviewed": render_unreviewed})
    return {"markdown": str(markdown_path), "gallery": str(gallery_path), "validation": validation_path,
            "renders_missing": render_missing, "renders_unreviewed": render_unreviewed,
            "complete": not check["errors"] and not render_missing and not render_unreviewed}
