"""Normalize observed Ozon widgets; never export account widgets or tracking fields."""
import datetime as dt
import re
from urllib.parse import urljoin, urlsplit
from ozon import allowed, safe_url

ORIGIN = 'https://www.ozon.ru'
CART_URL = ORIGIN + '/cart'
PRODUCT_WIDGETS = ['webProductHeading','webProductMainWidget','webPrice','webAspects',
                   'webGallery','webCurrentSeller','webReviewProductScore','webShortCharacteristics',
                   'webAddToCart','webDelivery','tileGridDesktop','emptyCart']


def rubles(value):
    if value is None or isinstance(value,bool): return None
    if isinstance(value,(int,float)): return value
    text = re.sub(r'[\s\u2009\u202f\u00a0₽]','',str(value)).replace(',','.')
    return float(text) if re.fullmatch(r'\d+(?:\.\d+)?',text) else None


def text(value):
    if isinstance(value,str): return value.strip()
    if isinstance(value,list): return ' '.join(filter(None,(text(v) for v in value)))
    if isinstance(value,dict):
        for key in ('text','content','textRs'):
            if key in value:return text(value[key])
    return ''


def canonical(link):
    url = urljoin(ORIGIN,link or '')
    return safe_url(url) if link and allowed(url) else None


def sku_from_url(url):
    match = re.search(r'/product/(?:[^/?]*-)?(\d+)(?:/|$)',urlsplit(url).path)
    return match.group(1) if match else None


def image_url(value):
    if not isinstance(value,str):return None
    url=urlsplit(value)
    return value if url.scheme=='https' and url.hostname in ('ir.ozone.ru','cdn1.ozone.ru','cdn1.ozonusercontent.com') else None


def first(snapshot,name):
    return next(iter(snapshot.get('states',{}).get(name,[])),{})


def collect(page):
    return page.evaluate('''(names) => {
      const states={};
      for (const n of document.querySelectorAll('[id^="state-"][data-state]')) {
        const name=names.find(name=>n.id.startsWith('state-'+name+'-'));
        if (!name) continue;
        try { (states[name] ||= []).push(JSON.parse(n.dataset.state)); } catch {}
      }
      const descriptions=[...document.querySelectorAll('[id="section-description"]')];
      return {states, url:location.href,
        title:document.querySelector('h1')?.innerText?.trim(),
        characteristics:[...document.querySelectorAll('#section-characteristics dl')].map(n=>({
          name:n.querySelector('dt')?.innerText?.trim(), value:n.querySelector('dd')?.innerText?.trim()})),
        descriptions:descriptions.map(n=>n.innerText.trim()),
        description_images:descriptions.flatMap(n=>[...n.querySelectorAll('img')].map(i=>({url:i.currentSrc||i.src,alt:i.alt}))),
        description_loaded:descriptions.length>0,
        characteristics_loaded:!!document.querySelector('#section-characteristics'),
        delivery_text:document.querySelector('[data-widget="webDelivery"]')?.innerText?.trim()||null,
        jsonld:[...document.querySelectorAll('script[type="application/ld+json"]')].flatMap(n=>{try{return [JSON.parse(n.textContent)]}catch{return []}})
      };
    }''', PRODUCT_WIDGETS)


def product(snapshot):
    main=first(snapshot,'webProductMainWidget')
    sku=str(main.get('sku') or sku_from_url(snapshot['url']) or '')
    heading=first(snapshot,'webProductHeading')
    price=first(snapshot,'webPrice')
    review=first(snapshot,'webReviewProductScore')
    seller=first(snapshot,'webCurrentSeller')
    cell=seller.get('sellerCell',{})
    specs=[{'name':r['name'],'value':r['value']} for r in snapshot.get('characteristics',[]) if r.get('name') and r.get('value')]
    if not specs:
        for row in first(snapshot,'webShortCharacteristics').get('characteristics',[]):
            specs.append({'name':text(row.get('title')), 'value':text(row.get('values',[]))})
    ld=next((x for x in snapshot.get('jsonld',[]) if isinstance(x,dict) and x.get('@type')=='Product'),{})
    images=[]
    for row in first(snapshot,'webGallery').get('images',[]):
        if image_url(row.get('src')):images.append({'url':row['src'],'alt':row.get('alt',''),'source':'gallery'})
    for row in snapshot.get('description_images',[]):
        if image_url(row.get('url')):images.append({**row,'source':'description'})
    images=list({r['url']:r for r in images}.values())
    aspects=[]
    for aspect in first(snapshot,'webAspects').get('aspects',[]):
        variants=[]
        for item in aspect.get('variants',[]):
            data=item.get('data',{})
            variants.append({'sku':str(item.get('sku','')), 'url':canonical(item.get('link')),
                             'value':data.get('searchableText') or text(data.get('textRs')),
                             'title':data.get('title'),'selected':bool(item.get('active')),
                             'availability':item.get('availability'), 'displayed_price_rub':rubles(data.get('price')),
                             'price_requires_product_check':True})
        aspects.append({'name':aspect.get('aspectName'),'key':aspect.get('aspectKey'),'variants':variants})
    ordinary=rubles(price.get('price'))
    conditional=rubles(price.get('cardPrice'))
    original=rubles(price.get('originalPrice'))
    warnings=[]
    if ordinary and original and original/ordinary>=10: warnings.append('inflated_reference_price')
    delivery=snapshot.get('delivery_text')
    return {'sku':sku,'url':canonical(snapshot['url']), 'title':heading.get('title') or snapshot.get('title') or ld.get('name'),
            'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'price':{'currency':'RUB','regular':ordinary,'bank_offer':conditional,'reference':original,
                     'bank_condition':price.get('lexemes',{}).get('withOzonCard') if conditional else None},
            'available':price.get('isAvailable'),
            'seller':{'name':text(cell.get('centerBlock',{}).get('title')) or None,
                      'url':canonical(cell.get('common',{}).get('action',{}).get('link')),
                      'rating':rubles(seller.get('rating',{}).get('title',{}).get('text'))},
            'reviews':{'rating':rubles(review.get('totalScore') or ld.get('aggregateRating',{}).get('ratingValue')),
                       'count':review.get('reviewsCount') or ld.get('aggregateRating',{}).get('reviewCount')},
            'aspects':aspects,'selected_options':{a['name']:[v['value'] for v in a['variants'] if v['selected']] for a in aspects},
            'characteristics':specs,'description':snapshot.get('descriptions') or [ld.get('description','')],
            'images':images,'vision_status':'pending' if images else 'no_images',
            'delivery':{'status':'observed' if delivery else 'not_loaded','text':delivery,
                        'destination':'current_account_location','cost_rub':None},
            'completeness':{'description':snapshot.get('description_loaded',False),
                            'characteristics':snapshot.get('characteristics_loaded',False)},'warnings':warnings}


def search_products(snapshot):
    products={}
    for grid in snapshot.get('states',{}).get('tileGridDesktop',[]):
        for row in grid.get('items',[]):
            url=canonical(row.get('action',{}).get('link'))
            if not url or not sku_from_url(url):continue
            title=None; current=None; reference=None; rating=[]
            for item in row.get('mainState',[]):
                if item.get('id')=='name':title=text(item.get('textDS'))
                if item.get('type')=='priceV2':
                    prices=item['priceV2'].get('price',[])
                    current=next((rubles(p.get('text')) for p in prices if p.get('textStyle')=='PRICE'),None)
                    reference=next((rubles(p.get('text')) for p in prices if p.get('textStyle')=='ORIGINAL_PRICE'),None)
                labels=item.get('labelListV2',{})
                if labels.get('testInfo',{}).get('automatizationId')=='tile-list-rating':
                    rating=[text(i.get('text')) for i in labels.get('items',[]) if i.get('type')=='text']
            cart=row.get('multiButton',{}).get('ozonButton',{}).get('addToCart',{})
            products[url]={'sku':str(row.get('sku') or row.get('id')), 'url':url,'title':title,
                           'displayed_price_rub':current,'reference_price_rub':reference,
                           'price_requires_product_check':True,'rating_labels':rating,
                           'delivery_label':cart.get('actionButton',{}).get('title'),
                           'warnings':['inflated_reference_price'] if current and reference and reference/current>=10 else []}
    return list(products.values())


def load_details(page):
    # Lazy product sections have no data-state nodes; collect their real DOM.
    for _ in range(10):
        if page.locator('#section-characteristics').count() and page.locator('[id="section-description"]').count():break
        page.mouse.wheel(0,800)
        page.wait_for_timeout(400)
    page.wait_for_timeout(700)
    return collect(page)


def cart(page):
    raw=page.locator('[id^="state-cartSplit-"][data-state]').evaluate_all('(nodes)=>nodes.map(n=>JSON.parse(n.dataset.state))')
    empty=page.locator('[data-widget="emptyCart"]').count()>0
    return parse_cart(raw,empty)


def parse_cart(raw,empty=False):
    if not raw and not empty:raise ValueError('cart_structure_unrecognized')
    rows=[]
    for split_index,split in enumerate(raw):
        for item_index,item in enumerate(split.get('cartItems',[])):
            p=item.get('product',{})
            controls=item.get('controls',{})
            q=controls.get('quantity',{})
            prices={}
            for block in p.get('priceColumn',[]):
                for price in block.get('prices',{}).get('elements',[]):
                    key=price.get('testInfo',{}).get('automatizationId')
                    value=next((rubles(v.get('text')) for v in price.get('price',[]) if v.get('textStyle')=='PRICE'),None)
                    if key=='cart-split-item-price':prices['regular']=value
                    if key=='cart-split-item-ozon-card-price':prices['bank_offer']=value
            title=next((text(v.get('text')) for v in p.get('titleColumn',[]) if v.get('type')=='text'),None)
            rows.append({'sku':str(p.get('id','')),'title':title,'quantity':q.get('current'),
                         'minimum':q.get('minimum'),'maximum':q.get('maximum'), 'multiplicity':q.get('multiplicity',1),
                         'selected':item.get('checkbox',{}).get('isChecked'), 'line_total':{'currency':'RUB',**prices},
                         'split_index':split_index,'item_index':item_index})
    if any(not r['sku'].isdigit() for r in rows):raise ValueError('cart_identity_missing')
    if len({r['sku'] for r in rows})!=len(rows):raise ValueError('cart_duplicate_sku')
    return {'url':CART_URL,'items':rows,'empty':empty and not rows}
