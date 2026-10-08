"""AliExpress observed v3 list contract. Detail/shipment schema remains unverified."""
from urllib.parse import urlsplit,urljoin
from common import now,text,status_code,date_window
LIST='https://aliexpress.ru/order-list'
API='https://aliexpress.ru/aer-api/bx/orders/v3/web/orders-list'


def normalize(data):
    rows={};unknown=0
    for group in data.get('items',[]):
        if not isinstance(group,dict) or not isinstance(group.get('orders'),list):unknown+=1;continue
        for parent in group['orders']:
            for order in parent.get('splitOrders') or [parent]:
                oid=str(order.get('orderId') or '')
                link=order.get('url',{}).get('pc') if isinstance(order.get('url'),dict) else None
                link=urljoin(LIST,link) if link else None
                u=urlsplit(link or '')
                if not oid or not link or u.scheme!='https' or u.hostname!='aliexpress.ru' or u.username or u.password:
                    unknown+=1;continue
                label=text(order.get('statusInfo',{}).get('title'));desc=text(order.get('statusInfo',{}).get('description'))
                status=status_code(label)
                rows[oid]={'marketplace':'aliexpress','id':oid,'url':link,'observed_at':now(),'complete':False,
                    'status':status,'status_text':label,'description':desc,'shipments':[],
                    'missing':['shipment_details','address','delivery_dates','items'],
                    'source_contract':'RedOrderList/0.32.0; live nonempty response not yet verified'}
    return list(rows.values()),unknown


def run(method,params,session):
    from ali_http import Client,PageData,login_state,walk
    if method=='orders.get':
        for active in (True,False):
            result=run('orders.list',{'active_only':active,'max_orders':100,'max_pages':5},session)
            found=[o for o in result.get('orders',[]) if o['id']==params['order_id']]
            if found:return {**result,'status':'partial','orders':found,'complete':False}
            if result.get('status') not in ('ok','partial'):return result
        return {'status':'not_found_in_bounded_history','orders':[],'complete':False}
    if method=='orders.code':return {'status':'not_supported','reason':'receipt_schema_not_observed'}
    with Client(session=session) as client:
        raw,meta=client.fetch(LIST);aer=PageData(raw.decode('utf-8','replace')).aer
        if not login_state(aer):return {'status':'login_required','orders':[],'complete':False}
        contexts=[r.get('props',{}).get('data') for r in walk(aer) if str(r.get('widgetId','')).startswith('bx/RedOrderListContext/')]
        data=next((r for r in contexts if isinstance(r,dict) and 'items' in r),None)
        if data is None:return {'status':'schema_changed','orders':[],'complete':False}
        # Active and archive are the actual tab types from the authenticated page.
        tab='active' if params.get('active_only',True) and method=='orders.list' else 'archive'
        tabs=[t.get('type') for t in data.get('tabs',[]) if t.get('isActive')]
        import json
        if tab not in tabs:
            body,_=client.fetch(API,payload={'tabType':tab,'page':1,'pageSize':20},referer=LIST)
            result=json.loads(body)
            if result.get('error') or not isinstance(result.get('data'),dict):return {'status':'order_list_failed','complete':False}
            data=result['data']
        orders=[];unknown=0
        for page in range(params.get('max_pages',5)):
            if not isinstance(data.get('items'),list):return {'status':'schema_changed','complete':False,'orders':orders}
            found,count=normalize(data);orders.extend(found);unknown+=count
            if not data.get('hasMore') or len(orders)>=params.get('max_orders',30):break
            raw,_=client.fetch(API,payload={'tabType':tab,'page':data.get('page',page+1)+1,'pageSize':20},referer=LIST)
            result=json.loads(raw)
            if result.get('error') or not isinstance(result.get('data'),dict):return {'status':'partial','orders':orders,'complete':False,'reason':'pagination_failed'}
            data=result['data']
    if method=='orders.get':
        orders=[o for o in orders if o['id']==params['order_id']]
        return {'status':'partial' if orders else 'not_found_in_archive','orders':orders,'complete':False}
    return {'status':'partial' if orders or unknown else 'ok','authenticated':True,
            'orders':orders[:params.get('max_orders',30)],'unparsed_order_count':unknown,
            'complete':not orders and not unknown and not data.get('hasMore',True),'observed_at':now(),
            'coverage':{'scope':tab,'has_more':data.get('hasMore'),'page':data.get('page')},
            'warnings':['AliExpress shipment/detail schema not live verified'] if orders else []}
