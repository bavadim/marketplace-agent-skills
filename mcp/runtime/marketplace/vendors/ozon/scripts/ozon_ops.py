"""Bounded browser workflows for product variants, images and cart operations."""
from pathlib import Path
import re
import sys
import json
from urllib.parse import urlsplit
from ozon import allowed, classify
from ozon_data import collect, product, load_details, search_products, canonical, image_url, sku_from_url


def goto_product(page,url):
    if not allowed(url) or not sku_from_url(url):raise ValueError('product_url_required')
    response=page.goto(url,wait_until='domcontentloaded')
    if not allowed(page.url):raise ValueError('outside_scope_redirect')
    if classify(response.status if response else 0,page.content())[0]!='ok':raise ValueError('product_access_failed')
    page.wait_for_timeout(2500)


def inspect_product(page,variant_sku=None,variant_limit=0,images_dir=None,image_limit=12):
    selected=product(load_details(page))
    if not selected['sku'] or not selected['title']:raise ValueError('product_structure_unrecognized')
    if variant_sku and selected['sku']!=str(variant_sku):
        variants=[v for a in selected['aspects'] for v in a['variants']]
        match=next((v for v in variants if v['sku']==str(variant_sku) and v['url']),None)
        if not match:raise ValueError('variant_not_in_observed_card')
        goto_product(page,match['url'])
        selected=product(load_details(page))
        if selected['sku']!=str(variant_sku):raise ValueError('selected_variant_mismatch')
    result={'operation_status':'ok','product':selected}
    if images_dir:result['vision']=prepare_images(page,selected,images_dir,image_limit)
    if variant_limit:
        seen={selected['sku']}; queue=[v for a in selected['aspects'] for v in a['variants'] if v['url']]
        variants=[]
        while queue and len(variants)<variant_limit:
            item=queue.pop(0)
            if item['sku'] in seen:continue
            seen.add(item['sku'])
            goto_product(page,item['url'])
            p=product(load_details(page))
            if p['sku']!=item['sku']:raise ValueError('variant_sku_mismatch')
            variants.append(p)
            queue.extend(v for a in p['aspects'] for v in a['variants'] if v['url'] and v['sku'] not in seen)
        result.update(variants=variants,variants_complete=not any(v['sku'] not in seen for v in queue))
        goto_product(page,selected['url'])
    return result


def search(page,limit=24):
    items={}; stagnant=0; grids=[]
    def receive(response):
        if response.status!=200 or not allowed(response.url) or urlsplit(response.url).path!='/api/entrypoint-api.bx/page/json/v2':return
        try:
            for name,value in response.json().get('widgetStates',{}).items():
                if name.startswith('tileGridDesktop-'):
                    grids.append(json.loads(value) if isinstance(value,str) else value)
        except Exception:
            pass # Missing optional page data is reflected in returned_count, never guessed.
    page.on('response',receive)
    try:
        for step in range(12):
            snapshot=collect(page)
            snapshot['states'].setdefault('tileGridDesktop',[]).extend(grids)
            found=search_products(snapshot)
            old=len(items)
            items.update({r['sku']:r for r in found})
            if len(items)>=limit:break
            stagnant=stagnant+1 if len(items)==old else 0
            if stagnant>=4:break
            page.mouse.wheel(0,1100)
            page.wait_for_timeout(900)
    finally:
        page.remove_listener('response',receive)
    return {'products':list(items.values())[:limit], 'returned_count':min(len(items),limit),
            'search_exhaustive':False, 'operation_status':'ok' if items else 'search_structure_unrecognized'}


def prepare_images(page,selected,directory,limit):
    # Only image URLs already extracted from this product, no credentials in URLs.
    from ozon_browser import private_bytes
    sibling=Path(__file__).resolve().parents[2]/'aliexpress/scripts'
    sys.path.insert(0,str(sibling))
    from vision import prepare
    target=Path(directory).resolve()/selected['sku']
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    manifests=[]; errors=[]
    for index,row in enumerate(selected['images'][:limit],1):
        url=image_url(row['url'])
        if not url:continue
        try:
            response=page.context.request.get(url,timeout=20000,max_redirects=0)
        except Exception as error:
            errors.append({'index':index,'reason':'image_request_failed','error_type':type(error).__name__})
            continue
        mime=response.headers.get('content-type','').split(';')[0]
        ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}.get(mime)
        if response.status!=200 or not ext:
            errors.append({'index':index,'status':response.status,'reason':'image_unavailable'});continue
        if int(response.headers.get('content-length','0'))>20*1024*1024:
            errors.append({'index':index,'reason':'image_too_large'});continue
        content=response.body()
        if len(content)>20*1024*1024:
            errors.append({'index':index,'reason':'image_too_large'});continue
        path=target/f'{index:02}.{ext}'
        private_bytes(path,content)
        manifest=prepare(path,target/'tiles')
        manifests.append({'image_url':url,'source':row['source'],**manifest})
    return {'status':'pending_agent_review','manifests':manifests,'errors':errors,
            'remaining_images':max(0,len(selected['images'])-limit)}


def operation(args):
    def execute(page):
        mutation=args.command=='cart' and args.action!='list'
        try:
            if args.command=='product':
                return inspect_product(page,args.variant_sku,args.variants_limit,args.images_dir,args.image_limit)
            if args.command=='search':return search(page,args.limit)
            import ozon_cart
            if args.action=='list':return {'cart':ozon_cart.read(page),'operation_status':'ok'}
            if args.action=='apply':
                import json
                return ozon_cart.apply_plan(page,json.loads(args.plan.read_text()),args.output.with_suffix('.journal.json'))
            if args.action=='add':return ozon_cart.add(page,args.url,args.quantity,args.max_price,args.sku)
            if args.action=='set':return ozon_cart.set_quantity(page,args.sku,args.quantity,args.expect_quantity)
            return ozon_cart.remove(page,args.sku,args.expect_quantity)
        except ValueError as error:
            code=str(error)
            if not re.fullmatch(r'[a-z_]+',code):code='operation_validation_error'
            return {'operation_status':'operation_error','reason':code,
                    'cart_modified':None if mutation else False,'requires_cart_read':mutation}
    execute.mutates_cart = args.command=='cart' and args.action!='list'
    return execute
