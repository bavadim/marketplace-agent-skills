"""Shared read/write verification around provider-specific low-level calls."""
from decimal import Decimal
import json
from common import now
from schema import identity,CART_URLS,options
from providers.base import Provider
from providers.transport import legacy,order_call,status

class BackendProvider(Provider):
    def orders_list(self,active_only=True,max_orders=30,max_pages=5):
        return self._orders('orders.list',{'active_only':active_only,'max_orders':max_orders,'max_pages':max_pages})
    def order_get(self,order_id):return self._orders('orders.get',{'order_id':order_id})
    def _orders(self,method,params):
        raw=order_call(self.name,method,params)
        orders=[]
        for row in raw.get('orders',[]):
            canonical={'ref':f"{self.name}:order:{row['id']}",'provider':self.name,'marketplace':self.name,
                       'id':str(row['id']),'url':row['url'],'observed_at':row.get('observed_at') or now(),
                       'placed':row.get('placed'),'status':row.get('status','unknown'),'status_text':row.get('status_text'),
                       'description':row.get('description'),'complete':row.get('complete',False),'missing':row.get('missing',[]),
                       'shipments':row.get('shipments',[])}
            orders.append(canonical)
        return {'status':status(raw),'provider':self.name,'orders':orders,'complete':raw.get('complete',False),
                'observed_at':raw.get('observed_at') or now(),'coverage':raw.get('coverage'),
                'warnings':raw.get('warnings',[]),'failures':raw.get('failures',[]),'reason':raw.get('reason')}
    def order_code(self,order_id,path):
        raw=order_call(self.name,'orders.code',{'order_id':order_id,'path':path})
        return {'status':status(raw),'provider':self.name,'artifact':raw.get('artifact'),'reason':raw.get('reason')}
    def cart_set(self,ref,quantity,max_unit_price,operation_id,expected_quantity=None,expected_options=None):
        site,pid,sku,url=identity(ref)
        if site!=self.name or not sku:raise ValueError('exact_variant_required')
        before=self.cart_list()
        if before['status']!='ok' or not before.get('complete'):return {**before,'requires_cart_read':True}
        key=ref if not ref.startswith('https://') else self.canonical_ref(pid,sku)
        line=next((r for r in before['items'] if r['ref']==key),None);current=line['quantity'] if line else 0
        if expected_quantity is not None and current!=expected_quantity:return {'status':'quantity_conflict','current_quantity':current,'provider':site}
        if current==quantity:return {'status':'ok','changed':False,'cart':before,'provider':site}
        if quantity and line and (line['price']['currency']!='RUB' or line['price']['unit'] is None or Decimal(line['price']['unit'])>Decimal(str(max_unit_price))):
            return {'status':'price_limit','changed':False,'provider':site}
        if quantity and (line is None or expected_options):
            checked=self.product(ref)
            product=checked.get('product')
            if not product or not product['variant_verified'] or product['available'] is False:return {'status':'variant_unavailable','provider':site,'changed':False}
            if product['price']['currency']!='RUB' or product['price']['unit'] is None or Decimal(product['price']['unit'])>Decimal(str(max_unit_price)):
                return {'status':'price_limit','changed':False,'provider':site}
            if product['stock'] is not None and product['stock']<quantity:return {'status':'insufficient_stock','provider':site,'changed':False}
            if expected_options and any(product['options'].get(k)!=v for k,v in options(expected_options).items()):return {'status':'options_changed','provider':site,'changed':False}
            url=product['url']
        raw=self._cart_write(pid,sku,url,line,quantity,max_unit_price,operation_id,expected_options)
        if status(raw)!='ok':return {'status':status(raw),'provider':site,'requires_cart_read':True,'journal_result':raw.get('journal_result')}
        after=self.cart_list()
        observed=next((r for r in after.get('items',[]) if r['ref']==key),None)
        actual=observed['quantity'] if observed else 0
        others_before={r['ref']:(r['quantity'],r.get('selected')) for r in before['items'] if r['ref']!=key}
        others_after={r['ref']:(r['quantity'],r.get('selected')) for r in after.get('items',[]) if r['ref']!=key}
        verified=after.get('complete') and actual==quantity and others_before==others_after
        if observed and (observed['price']['unit'] is None or observed['price']['currency']!='RUB' or Decimal(observed['price']['unit'])>Decimal(str(max_unit_price))):verified=False
        return {'status':'ok' if verified else 'cart_write_unverified','provider':site,'changed':True,
                'requires_cart_read':not verified,'cart':after,'journal_result':raw.get('journal_result')}
