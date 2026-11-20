#!/usr/bin/env python3
"""Live product data, deferred descriptions, regional cookies and SKU freight quotes.

Contracts observed in GlowAsyncProductContextWidget 0.14.0, mixer index,
and account-utils 0.1.8 on 2026-10-07. No address-book or checkout writes.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

from ali_http import (ORIGIN, Client, PageData, ReadFailure, clean_text,
                      image_url, now, price_value, product_identity, walk)


def api_json(client, url, payload=None, **kwargs):
    body, meta = client.fetch(url, payload=payload, **kwargs)
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or value.get('error'):
            raise ValueError()
    except ValueError:
        raise ReadFailure('unsupported_response', 'Expected a successful JSON object')
    source = {'url':url, 'observed_at':now(), 'response_sha256':hashlib.sha256(body).hexdigest()}
    return value, source


def numeric(value, name):
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not re.fullmatch(r'\d{1,30}', str(value)):
        raise ValueError('Invalid ' + name)
    return str(value)


def product_data(client, pid, sku=None, source_id=0):
    query = {'productId':numeric(pid,'product ID'), 'sourceId':numeric(source_id,'source ID')}
    if sku is not None:
        query['sku_id'] = numeric(sku,'SKU ID')
    value, source = api_json(client, ORIGIN+'/aer-jsonapi/v1/bx/pdp/web/productData?'+urlencode(query),
                             referer=ORIGIN+'/item/'+pid+'.html')
    data = value.get('data')
    if not isinstance(data, dict) or str(data.get('id')) != pid or str(data.get('sourceId')) != str(source_id):
        raise ReadFailure('identity_mismatch','Product identity changed')
    if sku and str(data.get('activeSkuId')) != sku:
        raise ReadFailure('identity_mismatch','Requested SKU was not selected by the product API')
    return data, source


def money(value):
    if not isinstance(value, dict) or price_value(value.get('value')) is None or not value.get('currency'):
        raise ReadFailure('unsupported_response','Price amount/currency missing')
    return {'value':price_value(value['value']), 'currency':value['currency'], 'display':clean_text(value.get('formatted'))}


def catalog(data, source):
    info = data.get('skuInfo') or {}
    if not isinstance(info.get('priceList'),list) or not isinstance(info.get('propertyList'),list):
        raise ReadFailure('unsupported_response','SKU matrix missing')
    skus, images = [], {}
    for row in info['priceList']:
        sid=numeric(row['skuId'],'SKU ID'); ids=set(row['skuPropIds'].split(',')); options=[]
        for prop in info['propertyList']:
            values=[v for v in prop['values'] if str(v['id']) in ids]
            if len(values)!=1: raise ReadFailure('unsupported_response','Ambiguous SKU option')
            v=values[0]; option={'name':prop['name'],'value':v.get('displayName') or v['name']}
            url=image_url(v.get('imageMainUrl'))
            if url:
                option['image_url']=url
                images.setdefault(url,{'url':url,'role':'sku_option','sku_ids':[],'vision_status':'pending'})['sku_ids'].append(sid)
            options.append(option)
        price=row.get('activityAmount') if data.get('price',{}).get('activity') else row.get('amount')
        skus.append({'sku_id':sid,'sku_attr':row['skuAttr'],'options':options,'price':money(price),
                     'available_quantity':int(row['availQuantity']),'quantity_in_cart':row.get('inCartQuantity',0)})
    if len({s['sku_id'] for s in skus})!=len(skus): raise ReadFailure('unsupported_response','Duplicate SKU')
    return {'schema_version':1,'kind':'skus','status':'ok','observed_at':source['observed_at'],
            'product_id':data['id'],'source_id':int(data['sourceId']),'title':data['name'],
            'source':source,'skus':skus,'images':list(images.values()),'account_cart_verified':False}


class Description(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.parts=[]; self.images=[]; self.hidden=0; self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag=='img':
            a=dict(attrs); url=image_url(a.get('src') or a.get('data-src'))
            if url and url not in self.images: self.images.append(url)

    def handle_endtag(self, tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)

    def handle_data(self, data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())


def geo_choices(page):
    choices=[]
    for widget in walk(page.aer):
        if 'ShipToHeaderItem/' not in str(widget.get('widgetId','')): continue
        for key in ('userGeoInfo','autoGeoInfo'):
            geo=widget.get('props',{}).get(key) or {}
            city,region,country=(geo.get(k) or {} for k in ('city','region','country'))
            if city.get('code') and region.get('code') and country.get('code'):
                choices.append({'city':city.get('name'),'city_code':str(city['code']),
                    'region':region.get('name'),'region_code':str(region['code']),
                    'country':country['code'],'source':key})
    return choices


def read_product(client, url, page, card):
    pid,canonical=product_identity(url)
    query=parse_qs(urlsplit(url).query); requested=(query.get('sku_id') or [None])[0]
    widgets=list(walk(page.aer))
    contexts=[w['props'] for w in widgets if 'GlowAsyncProductContextWidget/' in str(w.get('widgetId',''))]
    if len(contexts)!=1: raise ReadFailure('unsupported_response','Product context not unique')
    data,source=product_data(client,pid,requested,contexts[0].get('sourceId',0))
    cat=catalog(data,source)
    active=str(data.get('activeSkuId'))
    selected=next((s for s in cat['skus'] if s['sku_id']==active),None)
    card.update(title=data['name'],catalog=cat,selected_sku=selected,selection_explicit=bool(requested),
                selected_url=canonical+'?'+urlencode({'sku_id':active}) if selected else None,
                destination_choices=geo_choices(page),detail_status='ok',detail_source=source,
                attributes=[],description={'text':'','source':None},reviews={},seller=None)
    card['images']=[]
    for raw in data.get('gallery') or []:
        url=image_url(raw)
        if url: card['images'].append({'url':url,'role':'product_gallery','vision_status':'pending'})
    card['images'].extend(cat['images'])
    wanted=('SnowProductContent/','HazeProductCharacteristics/','RedReviewsProductFeedbackList/',
            'GlowReviewsProductRating/','RedForeignSellerInfo/')
    pending=[w for w in widgets if any(x in str(w.get('widgetId','')) for x in wanted) and w.get('asyncType')]
    ids=list(dict.fromkeys(w['uuid'] for w in pending))
    seen=set()
    if ids:
        if any(not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',i) for i in ids): raise ValueError('Invalid widget UUID')
        response,widget_source=api_json(client,ORIGIN+'/widget?'+urlencode([('uuid',i) for i in ids]),
                                        referer=canonical,aer_url=canonical+('?' + urlencode({'sku_id':requested}) if requested else ''))
        resolved=response.get('widgets')
        if not isinstance(resolved,list): raise ReadFailure('unsupported_response','Deferred widgets missing')
        card['widget_source']=widget_source
        for w in resolved:
            d=(w.get('state') or {}).get('data') or {}; name=w.get('name','')
            if (w.get('state') or {}).get('error'): continue
            required={'SnowProductContent':'html','HazeProductCharacteristics':'groups',
                      'GlowReviewsProductRating':'rating','RedReviewsProductFeedbackList':'reviews'}
            if not isinstance(d,dict) or (name in required and required[name] not in d): continue
            seen.add(w.get('uuid'))
            if name=='SnowProductContent':
                desc=Description(d.get('html') or '')
                card['description']={'text':'\n'.join(desc.parts),'source':widget_source}
                card['images'].extend({'url':u,'role':'description','vision_status':'pending'} for u in desc.images)
            elif name=='HazeProductCharacteristics':
                for group in d.get('groups') or []:
                    for prop in group.get('properties') or []:
                        value={'name':prop.get('name'),'value':prop.get('value')}
                        if value not in card['attributes']: card['attributes'].append(value)
            elif name=='GlowReviewsProductRating':
                card['reviews'].update({k:d.get(k) for k in ('rating','title','subtitle','statistics')})
            elif name=='RedReviewsProductFeedbackList':
                card['reviews']['items']=[{k:r.get('root',{}).get(k) for k in ('date','grade','text')}
                                          for r in d.get('reviews') or []]
            elif name=='RedForeignSellerInfo':
                # Store IDs are not sellerAdminSeq; only use an observed store link.
                links=[v for n in walk(d) for v in n.values() if isinstance(v,str)
                       and re.fullmatch(r'https://aliexpress\.ru/store/\d+/?',v)]
                if len(set(links))==1:
                    from ali_http import store
                    card['seller']=store(client,links[0].rstrip('/').rsplit('/',1)[1])
    card['detail_missing']=[w['widgetId'].split('/')[-2] for w in pending if w['uuid'] not in seen]
    for name in ('SnowProductContent', 'HazeProductCharacteristics'):
        if not any(name+'/' in str(w.get('widgetId','')) for w in pending):
            card['detail_missing'].append(name)
    if card['detail_missing']: card['detail_status']='partial'
    # Fetch text/variants automatically, leave technical interpretation to the agent.
    merged={}
    for entry in card['images']:
        if entry['url'] not in merged: merged[entry['url']]=entry
        else:
            previous=merged[entry['url']]
            previous['roles']=list(dict.fromkeys(previous.get('roles',[previous['role']])+[entry['role']]))
            if entry.get('sku_ids'): previous['sku_ids']=list(dict.fromkeys(previous.get('sku_ids',[])+entry['sku_ids']))
    card['images']=list(merged.values())
    store_url=card.get('listing',{}).get('store',{}).get('url')
    if card['seller'] is None and isinstance(store_url,str) and re.fullmatch(r'https://aliexpress\.ru/store/\d+',store_url):
        from ali_http import store
        card['seller']=store(client,store_url.rsplit('/',1)[1])
    card['unresolved']=['technical suitability of selected SKU','package contents and pack size','delivery quote']
    return card


def choose_destination(product, city):
    matches=[g for g in product.get('destination_choices',[]) if str(g.get('city','')).casefold()==city.casefold()]
    unique={(g['country'],g['region_code'],g['city_code']):g for g in matches}
    if len(unique)!=1:
        raise ReadFailure('destination_not_observed','Select the city in the browser, refresh session and re-read product')
    return next(iter(unique.values()))


def set_destination(client, product, city):
    geo=choose_destination(product,city)
    body={'countryCode':geo['country'],'cityCode':geo['city_code'],'provinceCode':geo['region_code']}
    client.fetch(ORIGIN+'/aer-api/v2/bx/account/v1/web/cookie/set-locale',body,referer=product['url'])
    raw,_=client.fetch(product['url'])
    observed=geo_choices(PageData(raw.decode('utf-8','replace')))
    if not any(g['source']=='userGeoInfo' and all(g[k]==geo[k] for k in ('country','city_code','region_code')) for g in observed):
        raise ReadFailure('destination_mismatch','City preference was not confirmed')
    return {'kind':'destination','status':'ok','destination_key':geo['city'],'observed_at':now(),**geo}


def quote(client, product, sku, quantity, destination):
    if type(quantity) is not int or quantity<1: raise ValueError('Quantity must be positive')
    if destination.get('status')!='ok': raise ValueError('Verified destination required')
    pid=product['product_id']; source_id=product['catalog']['source_id']
    data,source=product_data(client,pid,sku,source_id)
    current=catalog(data,source)
    row=next((s for s in data['skuInfo']['priceList'] if str(s['skuId'])==sku),None)
    selected=next((s for s in current['skus'] if s['sku_id']==sku),None)
    if row is None or selected is None or selected['available_quantity']<quantity: raise ReadFailure('insufficient_stock','Selected SKU unavailable')
    price_source=source
    freight=row.get('logisticAmount') or {}; d=data.get('delivery') or {}
    payload={'productId':int(pid),'productIdV2':pid,'sourceId':str(source_id),'skuId':sku,'count':quantity,
        'sendGoodsCountry':row.get('sendGoodsCountryCode',''),'country':destination['country'],
        'provinceCode':destination['region_code'],'cityCode':destination['city_code'],
        'maxPrice':freight.get('value',0),'minPrice':freight.get('value',0),'tradeCurrency':freight.get('currency',selected['price']['currency']),
        'displayMultipleFreight':d.get('displayMultipleMethods',False),'sourceType':row.get('sourceType',''),
        'buyerPrice':row.get('buyerPriceForLogistic',''),'freeDelivery':d.get('freeShipping'),
        'isLocalSeller':data.get('isLocalSeller',False)}
    if row.get('freightExt'): payload['ext']=json.loads(row['freightExt'])
    value,source=api_json(client,ORIGIN+'/aer-api/v1/pdp/web/freight/calculate?'+urlencode({'product_id':pid,'sourceId':source_id}),payload,referer=product['url'])
    target=value.get('to') or {}
    if any(str(target.get(remote))!=str(destination[local]) for remote,local in [('country','country'),('city','city_code'),('region','region_code')]):
        raise ReadFailure('destination_mismatch','Freight API used another city; set the city preference first')
    methods=value.get('methods')
    if not isinstance(methods,list): raise ReadFailure('unsupported_response','Freight methods missing')
    options=[]
    for method in methods:
        amount=money(method.get('amount'))
        options.append({'destination_key':destination['destination_key'],'sku_id':sku,'quantity':quantity,
            'earliest':method.get('etaStartDeliveryDate'),'latest':method.get('etaEndDeliveryDate'),
            'cost':amount['value'],'currency':amount['currency'],'method':method.get('groupName'),
            'service_name':method.get('serviceName'),'is_default':None,'checkout_selected':False})
    options.sort(key=lambda x:(x['latest'] or '9999',float(x['cost'])))
    return {'kind':'delivery_quote','status':'ok' if options else 'unavailable','observed_at':now(),
            'product_id':pid,'sku_id':sku,'quantity':quantity,'destination':destination,
            'price':selected['price'],'price_source':price_source,'variant':selected['options'],'source':source,'options':options}


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    d=sub.add_parser('destination'); d.add_argument('--city',required=True)
    q=sub.add_parser('quote'); q.add_argument('--sku-id',required=True); q.add_argument('--quantity',type=int,default=1); q.add_argument('--destination',type=Path,required=True)
    for c in (d,q):
        c.add_argument('--product',type=Path,required=True); c.add_argument('--output',type=Path,required=True)
        c.add_argument('--session')
    a=p.parse_args()
    from ali_cart import save_json
    try:
        product=json.loads(a.product.read_text())
        with Client(a.session) as client:
            result=set_destination(client,product,a.city) if a.command=='destination' else quote(client,product,a.sku_id,a.quantity,json.loads(a.destination.read_text()))
    except ReadFailure as e: result={'status':e.status,'reason':e.reason}
    except (KeyError,ValueError,TypeError,OSError) as e: result={'status':'failed','error_type':type(e).__name__}
    save_json(a.output,result); print(json.dumps({'status':result['status'],'output':str(a.output.resolve())}))
    return 0 if result['status']=='ok' else 2


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
