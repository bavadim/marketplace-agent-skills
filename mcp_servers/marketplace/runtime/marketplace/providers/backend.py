"""Shared read/write verification around provider-specific low-level calls."""
from decimal import Decimal
from schema import identity,options
from providers.base import Provider
from providers.transport import status

class BackendProvider(Provider):
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
