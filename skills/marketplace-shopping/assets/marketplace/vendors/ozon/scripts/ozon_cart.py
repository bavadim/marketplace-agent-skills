"""Cart CRUD through observed UI controls; absolute quantities and read-after-write."""
from ozon_data import CART_URL, cart, collect, product, sku_from_url


def find(cart_state,sku):
    return next((r for r in cart_state['items'] if r['sku']==str(sku)),None)


def read(page):
    if page.url.split('?')[0].rstrip('/') != CART_URL:page.goto(CART_URL,wait_until='domcontentloaded')
    page.locator('[data-widget="cartSplit"], [data-widget="emptyCart"]').first.wait_for(timeout=15000)
    # SSR controls appear before handlers hydrate. An early click can silently vanish.
    page.wait_for_timeout(2500)
    return cart(page)


def reload_cart(page):
    page.reload(wait_until='domcontentloaded')
    return read(page)


def quantity_input(page,state,row):
    split=page.locator('[data-widget="cartSplit"]').nth(row['split_index'])
    inputs=split.locator('input[inputmode="decimal"]')
    items=[r for r in state['items'] if r['split_index']==row['split_index']]
    # Refuse to infer row positions when a changed layout omits a quantity input.
    if inputs.count()!=len(items):raise ValueError('cart_controls_ambiguous')
    field=inputs.nth(row['item_index'])
    if field.input_value()!=str(row['quantity']):raise ValueError('cart_quantity_changed')
    matched=field.evaluate('''(input,title) => {
        const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
        for(let p=input.parentElement;p && !p.hasAttribute('data-widget');p=p.parentElement){
            if(p.querySelectorAll('input[inputmode="decimal"]').length!==1)break;
            if([...p.querySelectorAll('span')].some(n=>norm(n.textContent)===norm(title)))return true;
        }
        return false;
    }''',row['title'])
    if not matched:raise ValueError('cart_row_identity_mismatch')
    return field


def verify_others(before,after,sku):
    def values(c):return {r['sku']:(r['quantity'],r['selected']) for r in c['items'] if r['sku']!=str(sku)}
    if values(before)!=values(after):raise ValueError('cart_other_items_changed')


def validate_quantity(row,target):
    if isinstance(target,bool) or not isinstance(target,int) or target<1:raise ValueError('positive_integer_quantity_required')
    if row.get('minimum') is None or row.get('maximum') is None:raise ValueError('quantity_limits_missing')
    if not row['minimum']<=target<=row['maximum']:raise ValueError('quantity_out_of_stock_range')
    if target % (row.get('multiplicity') or 1):raise ValueError('quantity_multiplicity_mismatch')


def set_quantity(page,sku,target,expected=None):
    before=read(page); row=find(before,sku)
    if not row:raise ValueError('cart_sku_not_found')
    validate_quantity(row,target)
    if expected is not None and row['quantity']!=expected:raise ValueError('cart_precondition_failed')
    if row['quantity']==target:return {'operation_status':'ok','cart_modified':False,'cart':before,'action':'set','sku':str(sku)}
    field=quantity_input(page,before,row)
    field.fill(str(target)); field.press('Enter')
    page.wait_for_timeout(2000)
    after=reload_cart(page)
    verify_others(before,after,sku)
    observed=find(after,sku)
    return {'operation_status':'ok' if observed and observed['quantity']==target else 'cart_write_unverified',
            'cart_modified':True,'cart':after,'action':'set','sku':str(sku)}


def remove(page,sku,expected=None):
    before=read(page); row=find(before,sku)
    if not row:return {'operation_status':'ok','cart_modified':False,'cart':before,'action':'remove','sku':str(sku)}
    if expected is not None and row['quantity']!=expected:raise ValueError('cart_precondition_failed')
    if row['quantity']!=1:
        changed=set_quantity(page,sku,1,expected=row['quantity'])
        if changed['operation_status']!='ok':return changed
    current=read(page); row=find(current,sku)
    field=quantity_input(page,current,row)
    buttons=field.locator('xpath=../..').locator('button')
    if buttons.count()!=2:raise ValueError('cart_decrement_control_ambiguous')
    # Observed count-picker minus icon; never click a neighboring purchase button.
    if not (buttons.first.locator('svg path').first.get_attribute('d') or '').startswith('M13.416 6.75'):
        raise ValueError('cart_decrement_icon_changed')
    buttons.first.click()
    page.wait_for_timeout(1300)
    confirmation=page.get_by_role('button',name='Удалить',exact=True)
    if confirmation.count()!=1:raise ValueError('cart_delete_confirmation_missing')
    confirmation.click()
    page.wait_for_timeout(1300)
    after=reload_cart(page)
    verify_others(before,after,sku)
    return {'operation_status':'ok' if find(after,sku) is None else 'cart_write_unverified',
            'cart_modified':True,'cart':after,'action':'remove','sku':str(sku)}


def add(page,url,quantity=1,max_price=None,expected_sku=None):
    # The supplied URL identifies the reviewed variant; no auto-choice on mutation.
    sku=sku_from_url(url)
    if not sku or (expected_sku and str(expected_sku)!=sku):raise ValueError('product_sku_mismatch')
    p=product(collect(page))
    if p['sku']!=sku:raise ValueError('product_sku_mismatch')
    if p['available'] is not True:raise ValueError('product_unavailable')
    if max_price is not None and (p['price']['regular'] is None or p['price']['regular']>max_price):
        raise ValueError('product_price_limit_exceeded')
    before=read(page)
    existing=find(before,sku)
    if existing:
        return set_quantity(page,sku,quantity,expected=existing['quantity'])
    from ozon_ops import goto_product
    goto_product(page,url)
    checked=product(collect(page))
    if checked['sku']!=sku:raise ValueError('product_sku_mismatch')
    if max_price is not None and (checked['price']['regular'] is None or checked['price']['regular']>max_price):
        raise ValueError('product_price_limit_exceeded')
    controls=collect(page)['states'].get('webAddToCart',[])
    control=next((c['firstButton'] for c in controls if str(c.get('firstButton',{}).get('sku'))==sku),None)
    if not control:raise ValueError('add_control_missing')
    validate_quantity({'minimum':control.get('minAddToCartQuantity'), 'maximum':control.get('freeRest'), 'multiplicity':1},quantity)
    if control.get('isInCart'):raise ValueError('cart_changed_before_add')
    page.locator('[data-widget="webAddToCart"]').get_by_text('В корзину',exact=True).first.click()
    page.locator('[data-widget="webAddToCart"]').get_by_text('В корзине',exact=True).first.wait_for(timeout=15000)
    after=read(page); verify_others(before,after,sku)
    row=find(after,sku)
    if not row:return {'operation_status':'cart_write_unverified','cart_modified':True,'cart':after}
    if row['quantity']!=quantity:
        changed=set_quantity(page,sku,quantity,expected=row['quantity'])
        return {**changed,'action':'add','cart_modified':True,'product':checked}
    return {'operation_status':'ok','cart_modified':True,'cart':after,'action':'add','product':checked,'sku':sku}


def validate_plan(document):
    import math
    from ozon import allowed
    rows=document.get('items') if isinstance(document,dict) else None
    if not isinstance(rows,list) or not 1<=len(rows)<=20:raise ValueError('plan_requires_one_to_twenty_items')
    seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('invalid_plan_item')
        sku=str(row.get('sku','')); url=row.get('url','')
        if not sku.isdigit() or not allowed(url) or sku_from_url(url)!=sku:raise ValueError('plan_sku_url_mismatch')
        if sku in seen:raise ValueError('plan_duplicate_sku')
        seen.add(sku)
        qty=row.get('quantity')
        if type(qty)!=int or qty<1:raise ValueError('plan_quantity_invalid')
        price=row.get('max_price')
        if type(price) not in (int,float) or not math.isfinite(price) or price<=0:raise ValueError('plan_price_limit_required')
    return rows


def apply_plan(page,document,journal_path):
    import json
    from ozon_browser import private_bytes
    from ozon_ops import goto_product
    rows=validate_plan(document)
    before=read(page)
    inspected=[]
    # Validate every reviewed SKU before making the first change.
    for row in rows:
        goto_product(page,row['url'])
        snapshot=collect(page); p=product(snapshot)
        if p['sku']!=str(row['sku']) or p['available'] is not True:raise ValueError('plan_product_unavailable')
        if p['price']['regular'] is None or p['price']['regular']>row['max_price']:raise ValueError('plan_price_limit_exceeded')
        for name,value in row.get('expected_options',{}).items():
            if p['selected_options'].get(name)!=value:raise ValueError('plan_variant_options_changed')
        control=next((c.get('firstButton',{}) for c in snapshot['states'].get('webAddToCart',[]) if str(c.get('firstButton',{}).get('sku'))==p['sku']),{})
        validate_quantity({'minimum':control.get('minAddToCartQuantity'),'maximum':control.get('freeRest'),'multiplicity':1},row['quantity'])
        inspected.append(p)
    journal={'before':before,'steps':[],'complete':False}
    def save():private_bytes(journal_path,json.dumps(journal,ensure_ascii=False,indent=2).encode())
    save()
    for row in rows:
        goto_product(page,row['url'])
        journal['pending_sku']=str(row['sku']);save()
        changed=add(page,row['url'],row['quantity'],row['max_price'],row['sku'])
        journal['steps'].append(changed);journal.pop('pending_sku',None);save()
        if changed['operation_status']!='ok':
            return {**changed,'journal':str(journal_path),'partial':True}
    journal.update(complete=True,after=read(page));save()
    changed=any(s.get('cart_modified') for s in journal['steps'])
    return {'operation_status':'ok','cart_modified':changed,'cart':journal['after'],
            'journal':str(journal_path),'steps':len(rows),'products':inspected}
