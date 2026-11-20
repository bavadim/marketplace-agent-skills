"""AliExpress implementation; exact SKU references hide platform-specific IDs."""
from pathlib import Path
import tempfile,json
from common import now,write_private,text
from schema import base_product,identity,product_ref,money,delivery,rating,amount,CART_URLS,options
from providers.backend import BackendProvider
from providers.transport import legacy,status


def normalize(raw,stage='product'):
    pid=str(raw['product_id']);selected=raw.get('selected_sku') or {};listing=raw.get('listing') or {}
    sku=selected.get('sku_id') or listing.get('preselected_sku_id')
    url=raw.get('selected_url') or raw.get('listing_url') or raw['url']
    p=base_product('aliexpress',pid,sku,url,raw.get('title'),raw.get('observed_at'),stage)
    sourceprice=selected.get('price') or listing.get('price') or {}
    p['price']=money(sourceprice.get('value'),sourceprice.get('currency') or 'RUB')
    p['variant_verified']=bool(selected and raw.get('detail_status') in ('ok','partial'))
    p.update(options=options({r['name']:r['value'] for r in selected.get('options',[])}),stock=selected.get('available_quantity'),
        available=(selected.get('available_quantity',0)>0) if selected else None,
        specifications=raw.get('attributes',[]),description=raw.get('description'),images=raw.get('images',[]),evidence=raw.get('evidence',[]))
    p['variants']=[{'ref':product_ref('aliexpress',pid,s['sku_id']),'url':raw['url']+'?sku_id='+s['sku_id'],
        'options':options({v['name']:v['value'] for v in s.get('options',[])}),'price':money(s.get('price',{}).get('value'),s.get('price',{}).get('currency')),
        'stock':s.get('available_quantity'),'selected':s['sku_id']==sku,'variant_verified':True} for s in raw.get('catalog',{}).get('skus',[])]
    seller=(raw.get('seller') or {}).get('seller') or {};store=listing.get('store') or {}
    p['seller']={'name':seller.get('name') or store.get('name'),'id':seller.get('id'),'url':seller.get('url') or store.get('url'),
                 'rating':rating(seller.get('rating'),seller.get('scale'),seller.get('review_count'))}
    reviews=raw.get('reviews') or {};p['product_rating']=rating(reviews.get('rating') or listing.get('rating'),5)
    p['warnings']=raw.get('detail_missing',[])+([] if stage!='search' else ['search_price_requires_product_check'])
    return p

class AliExpressProvider(BackendProvider):
    name='aliexpress'
    def canonical_ref(self,pid,sku):return product_ref(self.name,pid,sku)
    def search(self,query,limit=12):
        raw=legacy(self.name,['search','--query',query]);rows=[]
        for r in raw.get('products',[])[:limit]:
            try:rows.append(normalize(r,'search'))
            except (KeyError,ValueError):pass
        return {'status':status(raw),'provider':self.name,'products':rows,'complete':False,'observed_at':raw.get('observed_at') or now(),'warnings':['bounded_search']}
    def orders_list(self,active_only=True,max_orders=30,max_pages=5):
        if active_only:return super().orders_list(True,max_orders,max_pages)
        active=super().orders_list(True,max_orders,max_pages);archive=super().orders_list(False,max_orders,max_pages)
        unique={}
        for order in active.get('orders',[])+archive.get('orders',[]):unique.setdefault(order['ref'],order)
        good=[r for r in (active,archive) if r['status'] in ('ok','partial')]
        return {'status':'ok' if len(good)==2 and all(r['status']=='ok' for r in good) else 'partial' if good else 'providers_unavailable',
                'provider':self.name,'orders':list(unique.values())[:max_orders],
                'complete':active['complete'] and archive['complete'] and len(unique)<=max_orders,
                'observed_at':now(),'coverage':{'scope':'active_and_history','active':active.get('coverage'),'archive':archive.get('coverage')},
                'warnings':active.get('warnings',[])+archive.get('warnings',[])}
    def _read(self,ref):
        site,pid,sku,url=identity(ref);return legacy(self.name,['product','--url',url])
    def product(self,ref,variant_limit=0,quantity=1,city=None,images_dir=None):
        raw=self._read(ref)
        if not raw.get('product_id'):return {'status':status(raw),'provider':self.name,'product':None}
        p=normalize(raw)
        if city and p['variant_verified']:
            q=self._quote(raw,p['variant_id'],quantity,city)
            if q.get('options'):
                chosen=q['options'][0];p['delivery']=delivery(quote=chosen,quantity=quantity,city=city);p['price']=money(q.get('price',{}).get('value'),q.get('price',{}).get('currency'))
                p['delivery_options']=[delivery(quote=o,quantity=quantity,city=city) for o in q['options']]
            else:p['warnings'].append(q.get('status','delivery_unknown'))
        for variant in [v for v in p['variants'] if not v['selected']][:variant_limit]:
            alternative=self._read(variant['ref'])
            if alternative.get('selected_sku'):p['variant_products'].append(normalize(alternative))
        p['variants_complete']=len(p['variant_products'])>=len([v for v in p['variants'] if not v['selected']])
        if images_dir:p['image_evidence']=self._images(raw,images_dir,12)
        return {'status':status(raw),'provider':self.name,'product':p}
    def _quote(self,raw,sku,quantity,city):
        with tempfile.TemporaryDirectory(prefix='ali-quote-') as temp:
            product=Path(temp)/'product.json';write_private(product,raw)
            dest=legacy(self.name,['destination','--product',product,'--city',city])
            if status(dest)!='ok':return dest
            target=Path(temp)/'destination.json';write_private(target,dest)
            return legacy(self.name,['quote','--product',product,'--sku-id',sku,'--quantity',quantity,'--destination',target])
    def quote(self,ref,quantity=1,city=None):
        if not city:raise ValueError('destination_required')
        return self.product(ref,quantity=quantity,city=city)
    def _images(self,raw,path,limit):
        root=Path(path);root.mkdir(parents=True,exist_ok=True,mode=0o700)
        source=root/'product.json';output=root/'images.json';write_private(source,raw)
        # Assets are relative to output, so the manifest must outlive this invocation.
        from providers.transport import VENDORS,provider_lock
        import subprocess,sys
        before=output.stat().st_mtime_ns if output.exists() else None
        with provider_lock(self.name):
            subprocess.run([sys.executable,str(VENDORS/self.name/'scripts/ali.py'),'images','--input',str(source),'--limit',str(limit),'--output',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if not output.exists() or output.stat().st_mtime_ns==before:return {'status':'adapter_error'}
        from common import read_json
        return read_json(output)
    def images(self,ref,path,limit=12):
        raw=self._read(ref)
        if not raw.get('product_id'):return {'status':status(raw),'provider':self.name,'evidence':None}
        return {'status':'ok','provider':self.name,'ref':normalize(raw)['ref'],'evidence':self._images(raw,path,limit)}
    def cart_list(self):
        raw=legacy(self.name,['cart','list']);rows=[]
        from decimal import Decimal
        for r in raw.get('items',[]):
            unit=amount((r.get('price') or {}).get('value'));qty=r['quantity']
            rows.append({'ref':product_ref(self.name,r['product_id'],r['sku_id']),'line_ref':str(r['item_id']),'provider':self.name,
                'title':r.get('title'),'url':r['url']+'?sku_id='+r['sku_id'],'quantity':qty,
                'price':money(unit,(r.get('price') or {}).get('currency')),'line_total':format(Decimal(unit)*qty,'f') if unit is not None else None,
                'selected':r.get('selected'),'available':r.get('available'),'options':{},'variant_text':r.get('variant_text')})
        return {'status':status(raw),'provider':self.name,'items':rows,'complete':raw.get('complete',False),'url':CART_URLS[self.name]}
    def _cart_write(self,pid,sku,url,line,quantity,max_unit_price,operation_id,expected_options):
        action='delete' if quantity==0 else 'update' if line else 'add'
        args=['cart',action,'--operation-id',operation_id]
        if line:args+=['--item-id',line['line_ref']]
        else:
            args+=['--product-id',pid,'--sku-id',sku]
            if expected_options:args+=['--expected-options',json.dumps({k:v[0] if isinstance(v,list) and len(v)==1 else v for k,v in expected_options.items()},ensure_ascii=False)]
        if quantity:args+=['--quantity',quantity,'--max-unit-price',max_unit_price,'--currency','RUB']
        return legacy(self.name,args,operation_id)
    def session(self,action,profile=None):
        args=['session','check' if action=='status' else action,'--browser','chromium']
        if profile:args+=['--profile',profile]
        raw=legacy(self.name,args)
        return {'status':status(raw),'provider':self.name,'authenticated':raw.get('authenticated'),
                'session_saved':raw.get('saved_session_replaced') or raw.get('reused_saved_session'),
                'next_action':raw.get('next_action'),'login_url':raw.get('login_url')}
