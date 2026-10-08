"""Provider-neutral models. Money is a decimal string, unknown fields are null."""
from decimal import Decimal,InvalidOperation
import re
from urllib.parse import urlsplit,parse_qs,urlencode,urlunsplit
from common import now,text,date_window

SITES=('ozon','aliexpress')
CART_URLS={'ozon':'https://www.ozon.ru/cart','aliexpress':'https://aliexpress.ru/cart'}


def amount(value):
    if value is None or isinstance(value,bool):return None
    try:
        n=Decimal(str(value).replace('\u202f','').replace('\xa0','').replace(' ','').replace(',','.'))
        return format(n,'f') if n.is_finite() and n>=0 else None
    except InvalidOperation:return None


def product_ref(site,product_id,variant_id=None):
    if site not in SITES or not re.fullmatch(r'\d{1,30}',str(product_id)):raise ValueError('invalid_product_ref')
    if site=='ozon':return f'ozon:{product_id}'
    if variant_id is not None and not re.fullmatch(r'\d{1,30}',str(variant_id)):raise ValueError('invalid_variant_ref')
    return f'aliexpress:{product_id}'+(f':{variant_id}' if variant_id else '')


def identity(ref):
    if not isinstance(ref,str):raise ValueError('ref_required')
    if ref.startswith('https://'):
        u=urlsplit(ref)
        if u.username or u.password or u.port not in (None,443):raise ValueError('outside_scope')
        if u.hostname in ('ozon.ru','www.ozon.ru'):
            m=re.search(r'(\d{1,30})/?$',u.path)
            if u.path.startswith('/product/') and m:return 'ozon',m[1],m[1],ref
        if u.hostname=='aliexpress.ru':
            m=re.fullmatch(r'/item/(\d{1,30})\.html',u.path)
            sku=parse_qs(u.query).get('sku_id',[None])[0]
            if m:
                if sku and not re.fullmatch(r'\d{1,30}',sku):raise ValueError('invalid_variant_ref')
                return 'aliexpress',m[1],sku,'https://aliexpress.ru'+u.path+('?'+urlencode({'sku_id':sku}) if sku else '')
        raise ValueError('outside_scope')
    parts=ref.split(':')
    if parts[0]=='ozon' and len(parts)==2 and re.fullmatch(r'\d{1,30}',parts[1]):return 'ozon',parts[1],parts[1],f'https://www.ozon.ru/product/{parts[1]}/'
    if parts[0]=='aliexpress' and len(parts) in (2,3) and all(re.fullmatch(r'\d{1,30}',p) for p in parts[1:]):
        return 'aliexpress',parts[1],parts[2] if len(parts)==3 else None,'https://aliexpress.ru/item/'+parts[1]+'.html'+('?sku_id='+parts[2] if len(parts)==3 else '')
    raise ValueError('invalid_product_ref')


def order_identity(ref):
    parts=str(ref).split(':',2)
    if len(parts)!=3 or parts[0] not in SITES or parts[1]!='order' or not re.fullmatch(r'[\w-]{1,100}',parts[2]):raise ValueError('invalid_order_ref')
    return parts[0],parts[2]


def money(unit=None,currency='RUB',reference=None,conditional=None):
    return {'unit':amount(unit),'currency':currency,'reference':amount(reference),'conditional':conditional}


def delivery(label=None,quote=None,quantity=1,city=None):
    w=date_window(label) if label else {};quote=quote or {}
    earliest=quote.get('earliest') or w.get('from');latest=quote.get('latest') or w.get('to')
    def date_only(v):return str(v)[:10] if v and re.match(r'^\d{4}-\d{2}-\d{2}',str(v)) else None
    return {'earliest':date_only(earliest),'latest':date_only(latest),'cost':amount(quote.get('cost')),
            'currency':quote.get('currency','RUB'),'destination':quote.get('destination_key') or city,
            'quantity':quote.get('quantity',quantity),'method':quote.get('method'),
            'verified':bool(quote and earliest and latest),'source_text':label,
            'status':'quoted' if quote else 'preliminary' if earliest else 'unknown'}


def options(values):
    return {str(k):[str(x) for x in v] if isinstance(v,list) else [str(v)] for k,v in (values or {}).items()}


def rating(value=None,scale=None,count=None):return {'value':amount(value),'scale':scale,'count':count}


def base_product(site,pid,sku,url,title,stamp=None,stage='product'):
    return {'ref':product_ref(site,pid,sku),'provider':site,'product_id':str(pid),'variant_id':str(sku) if sku else None,
            'url':url,'title':title,'observed_at':stamp or now(),'stage':stage,'variant_verified':False,
            'price':money(None),'delivery':delivery(),'seller':{'name':None,'id':None,'url':None,'rating':rating()},
            'product_rating':rating(),'available':None,'stock':None,'options':{},'variants':[],
            'variant_products':[],'variants_complete':False,'delivery_options':[],'image_evidence':None,'specifications':[],'description':None,'images':[],'evidence':[],'review':None,'warnings':[]}


def order_rows(orders):
    rows=[]
    for order in orders:
        site=order['provider'];oid=order['id'];oref=f'{site}:order:{oid}'
        for shipment in order.get('shipments') or [None]:
            row={'ref':oref,'provider':site,'order_id':oid,'order_url':order['url'],'observed_at':order.get('observed_at'),
                 'shipment_id':shipment.get('id') if shipment else None,'status':shipment.get('status') if shipment else order.get('status','unknown'),
                 'status_text':shipment.get('status_text') if shipment else order.get('status_text'),
                 'delivery_text':shipment.get('delivery_text') if shipment else order.get('description'),
                 'delivery_method':shipment.get('delivery_method') if shipment else None,'address':shipment.get('address') if shipment else None,
                 'expected':shipment.get('expected') if shipment else None,'pickup_until':shipment.get('pickup_until') if shipment else None,
                 'pickup_until_inclusive':shipment.get('pickup_until_inclusive') if shipment else None,
                 'items':shipment.get('items',[]) if shipment else [],'complete':order.get('complete',False) if shipment else False,
                 'missing':order.get('missing',[]) if not shipment else []}
            row['row_ref']=oref+(':shipment:'+str(row['shipment_id']) if shipment else ':unresolved')
            rows.append(row)
    priority={'ready_for_pickup':0,'out_for_delivery':1,'in_transit':2,'processing':3,'unknown':4,'awaiting_payment':5,'delivered':6,'returned':7,'cancelled':8}
    rows.sort(key=lambda r:(priority.get(r['status'],4),r.get('pickup_until') or (r.get('expected') or {}).get('to') or '9999',r['row_ref']))
    return rows
