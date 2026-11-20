"""Ozon implementation of the common provider contract."""
from decimal import Decimal
from common import now, text
from schema import base_product,identity,product_ref,money,delivery,rating,amount,CART_URLS,options
from providers.backend import BackendProvider
from providers.transport import legacy,status


def normalize(raw,stage='product'):
    pid=str(raw['sku']);p=base_product('ozon',pid,pid,raw['url'],raw.get('title'),raw.get('observed_at'),stage)
    if stage=='search':
        p['price']=money(raw.get('displayed_price_rub'),reference=raw.get('reference_price_rub'))
        p['delivery']=delivery(raw.get('delivery_label'));p['warnings']=['search_price_requires_product_check']+raw.get('warnings',[])
        p['product_rating']['display_labels']=raw.get('rating_labels',[]);return p
    p.update(variant_verified=True,available=raw.get('available'),options=options(raw.get('selected_options',{})),
             specifications=raw.get('characteristics',[]),description=raw.get('description'),images=raw.get('images',[]),warnings=raw.get('warnings',[]))
    price=raw.get('price',{});p['price']=money(price.get('regular'),reference=price.get('reference'),
        conditional={'unit':amount(price.get('bank_offer')),'condition':price.get('bank_condition')} if price.get('bank_offer') is not None else None)
    seller=raw.get('seller') or {};p['seller']={'name':seller.get('name'),'id':seller.get('url'),'url':seller.get('url'),'rating':rating(seller.get('rating'),5)}
    review=raw.get('reviews') or {};p['product_rating']=rating(review.get('rating'),5,review.get('count'))
    p['delivery']=delivery((raw.get('delivery') or {}).get('text'))
    seen=set()
    for aspect in raw.get('aspects',[]):
        for v in aspect.get('variants',[]):
            sku=str(v.get('sku') or '')
            if not sku.isdigit() or sku in seen:continue
            seen.add(sku);p['variants'].append({'ref':product_ref('ozon',sku),'url':v.get('url'),'options':options({aspect.get('name'):v.get('value')}),
                'selected':v.get('selected',False),'price':money(v.get('displayed_price_rub')),'variant_verified':False})
    p['evidence']=[{'kind':'product_listing','url':p['url'],'observed_at':p['observed_at']}]
    return p

class OzonProvider(BackendProvider):
    name='ozon'
    def canonical_ref(self,pid,sku):return product_ref(self.name,pid)
    def search(self,query,limit=12):
        raw=legacy(self.name,['search',query,'--limit',limit]);rows=[]
        for r in raw.get('products',[]):
            try:rows.append(normalize(r,'search'))
            except (ValueError,KeyError):pass
        return {'status':status(raw),'provider':self.name,'products':rows,'complete':False,'observed_at':now(),'warnings':['bounded_search']}
    def product(self,ref,variant_limit=0,quantity=1,city=None,images_dir=None):
        site,pid,sku,url=identity(ref)
        args=['product',url,'--variants-limit',variant_limit]
        if images_dir:args+=['--images-dir',images_dir]
        raw=legacy(self.name,args)
        if not raw.get('product'):return {'status':status(raw),'provider':self.name,'product':None}
        p=normalize(raw['product']);p['image_evidence']=raw.get('vision');p['variant_products']=[normalize(v) for v in raw.get('variants',[])]
        p['variants_complete']=raw.get('variants_complete',False)
        if city:
            label=p['delivery'].get('source_text') or ''
            p['delivery']['destination']=city if city.casefold() in label.casefold() else None
            if p['delivery']['destination'] is None:p['warnings'].append('destination_unverified')
        p['delivery']['quantity']=quantity
        if p['delivery']['cost'] is None:p['warnings'].append('shipping_cost_unknown')
        return {'status':status(raw),'provider':self.name,'product':p}
    def quote(self,ref,quantity=1,city=None):
        result=self.product(ref,quantity=quantity,city=city)
        return {**result,'delivery':result.get('product',{}).get('delivery') if result.get('product') else None}
    def images(self,ref,path,limit=12):
        site,pid,sku,url=identity(ref);raw=legacy(self.name,['product',url,'--images-dir',path,'--image-limit',limit])
        return {'status':status(raw),'provider':self.name,'ref':product_ref(self.name,pid),'evidence':raw.get('vision')}
    def cart_list(self):
        raw=legacy(self.name,['cart','list']);cart=raw.get('cart') or {};rows=[]
        for r in cart.get('items',[]):
            qty=r.get('quantity');total=amount((r.get('line_total') or {}).get('regular'))
            unit=format(Decimal(total)/qty,'f') if total is not None and isinstance(qty,int) and qty>0 else None
            rows.append({'ref':product_ref(self.name,r['sku']),'line_ref':product_ref(self.name,r['sku']),'provider':self.name,
                'title':r.get('title'),'url':r.get('url'),'quantity':qty,'price':money(unit),'line_total':total,
                'selected':r.get('selected'),'available':r.get('available'),'options':{},'variant_text':r.get('variant_text')})
        return {'status':status(raw),'provider':self.name,'items':rows,'complete':status(raw)=='ok' and raw.get('authenticated') is True and 'items' in cart,'url':CART_URLS[self.name]}
    def _cart_write(self,pid,sku,url,line,quantity,max_unit_price,operation_id,expected_options):
        args=['cart','remove' if quantity==0 else 'set' if line else 'add','--sku',sku]
        if quantity:args+=['--quantity',quantity]
        if line:args+=['--expect-quantity',line['quantity']]
        elif quantity:args+=['--url',url,'--max-price',max_unit_price]
        return legacy(self.name,args,operation_id)
    def session(self,action,profile=None):
        args=['session',action]
        if profile:args+=['--profile',profile]
        raw=legacy(self.name,args)
        return {'status':status(raw),'provider':self.name,'authenticated':raw.get('authenticated'),
            'session_saved':raw.get('session_saved'),'login_verified':raw.get('login_verified'),
            'next_action':'Log in to Ozon in your Chromium and refresh this provider session.' if status(raw)=='login_required' else None}
