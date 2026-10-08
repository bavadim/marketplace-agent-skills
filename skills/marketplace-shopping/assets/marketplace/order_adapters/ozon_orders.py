"""Read-only Ozon orders adapter. Only observed order widgets/actions are consumed."""
import base64
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode
from common import now, today, text, status_code, date_window, write_private

BASE='https://www.ozon.ru'
LIST=BASE+'/my/orderlist/'
TERMINAL={'delivered','cancelled','returned'}
WIDGETS=['orderList','shipmentWidget','orderDetailsItem','titleWithTimer','paginator']


def detail_url(order_id):
    if not re.fullmatch(r'[\w-]{1,100}',str(order_id)):raise ValueError('invalid_order_id')
    return BASE+'/my/orderdetails/?'+urlencode({'order':order_id})


def safe_link(link,path):
    url=urljoin(BASE,link or '')
    u=urlsplit(url)
    return url if u.scheme=='https' and u.hostname in ('www.ozon.ru','ozon.ru') and u.path.startswith(path) and not u.username and not u.password and u.port in (None,443) else None


def collect(page):
    return page.evaluate('''names => {
      const states={};
      for(const n of document.querySelectorAll('[id^="state-"][data-state]')) {
        const name=names.find(k=>n.id.startsWith('state-'+k+'-'));
        if(name) {try{(states[name] ||= []).push(JSON.parse(n.dataset.state))}catch{}}
      }
      return states;
    }''',WIDGETS)


def index_rows(states):
    rows={};warnings=[]
    for widget in states.get('orderList',[]):
        if 'ordersV2' not in widget:warnings.append('order_list_schema_changed');continue
        for row in widget['ordersV2']:
            label=text(row.get('leftBlock',{}).get('textIcon',{}).get('text'))
            action=row.get('common',{}).get('action',{})
            url=safe_link(action.get('link'),'/my/orderdetails/')
            ids=[]
            if url:ids=parse_qs(urlsplit(url).query).get('order',[])
            elif urlsplit(action.get('link','')).path=='v2/cacheOrderProducts':
                try:
                    encoded=action['params']['data']
                    if len(encoded)>500000:raise ValueError()
                    ids=[r['order_number'] for r in json.loads(base64.b64decode(encoded+'==='))['orders']]
                except (ValueError,KeyError,TypeError):warnings.append('grouped_orders_schema_changed')
            else:warnings.append('order_link_unrecognized')
            for oid in ids:
                key=str(oid);candidate={'id':key,'url':detail_url(oid),'list_status':status_code(label),'list_status_text':label}
                if key not in rows or rows[key]['list_status'] in TERMINAL:rows[key]=candidate
    return list(rows.values()),list(dict.fromkeys(warnings))


def normalize_detail(states,order_id,reference=None):
    url=detail_url(order_id);address=None;method=None
    for w in states.get('orderDetailsItem',[]):
        if w.get('testInfo',{}).get('automatizationId')=='details-address':
            cell=w.get('cell',{});address=text(cell.get('subtitle')) or None
            label=text(cell.get('title'))
            method='pickup' if re.search(r'пункт|постамат',label,re.I) else 'courier' if 'курьер' in label.lower() else 'unknown'
    shipments=[]
    for w in states.get('shipmentWidget',[]):
        status_label='';delivery_label=''
        for row in w.get('header',[]):
            ti=row.get('textIcon',{}).get('text',{});key=ti.get('testInfo',{}).get('automatizationId') if isinstance(ti,dict) else None
            if key=='shipment-status':status_label=text(ti)
            if key=='shipment-delivery':delivery_label=text(ti)
        status=status_code(status_label);items=[]
        for group in w.get('items',[]):
            for seller in group.get('sellers',[]):
                for p in seller.get('products',[]):
                    action=p.get('title',{}).get('common',{}).get('action',{})
                    quantity=p.get('addToCartButton',{}).get('action',{}).get('quantity')
                    if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<1:quantity=None
                    captions=[text(v) for v in p.get('price',{}).get('price',[])]
                    for caption in captions:
                        match=re.match(r'^(\d+)\s*[xх×]\s*',caption)
                        if match:quantity=int(match[1]);break
                    items.append({'sku':str(action.get('id') or ''),'title':text(p.get('title',{}).get('name')),
                                  'url':safe_link(action.get('link'),'/product/'),'quantity':quantity,
                                  'options':[text(v) for v in p.get('attributes',[])],
                                  'seller':text(seller.get('name')),'measurements':None})
        window=date_window(delivery_label,reference,context='past' if status in TERMINAL else 'future')
        deadline=window['to'] if status=='ready_for_pickup' and re.search(r'\bдо\s',delivery_label,re.I) else None
        shipments.append({'id':str(w.get('shipmentId') or ''),'status':status,'status_text':status_label,
                          'delivery_text':delivery_label,'delivery_method':method,'address':address,
                          'expected':window if status not in TERMINAL|{'ready_for_pickup'} else None,
                          'pickup_until':deadline,'pickup_until_inclusive':bool(deadline and 'включительно' in delivery_label),
                          'items':items,'source_url':url})
    title=text((states.get('titleWithTimer') or [{}])[0].get('title'))
    return {'marketplace':'ozon','id':str(order_id),'url':url,'observed_at':now(),
            'placed':date_window(title,reference,'past'),'shipments':shipments,
            'complete':bool(shipments) and all(s['id'] and s['status']!='unknown' for s in shipments)}


def navigate(page,url):
    response=page.goto(url,wait_until='domcontentloaded',timeout=30000)
    if not response or response.status!=200:raise ValueError('order_navigation_failed')
    page.wait_for_timeout(1800)
    states=collect(page)
    if not states.get('shipmentWidget'):
        page.wait_for_timeout(1500);states=collect(page)
    return states


def run(method,params,session):
    from ozon_browser import browse
    if not session.is_file():return {'status':'login_required'}
    order_id=params.get('order_id')
    initial=detail_url(order_id) if method!='orders.list' else LIST
    def operation(page):
        page.wait_for_timeout(1500)
        if method=='orders.code':
            widget=page.locator('[data-widget="receiptCode"]')
            if widget.count()!=1 or not widget.is_visible():return {'operation_status':'pickup_code_unavailable'}
            # The observed widget presents a numeric daily code, not a machine barcode.
            raw=page.locator('[id^="state-receiptCode-"][data-state]').first.get_attribute('data-state')
            state=json.loads(raw or '{}')
            code=text(state.get('code'))
            if not (re.fullmatch(r'[\d\s-]{4,30}',code) or re.fullmatch(r'\d{4} \d{4} \* \d{4}',code)):return {'operation_status':'code_schema_changed'}
            path=Path(params['path']).expanduser().resolve()
            if path.suffix.lower()!='.png':raise ValueError('code_path_must_be_png')
            write_private(path,widget.screenshot())
            return {'operation_status':'ok','artifact':{'path':str(path),'kind':'numeric_pickup_code','machine_scannable':False,
                    'order_id':order_id,'observed_at':now(),'valid_on':today().isoformat(),'timezone':'Europe/Moscow',
                    'refresh_before_forwarding':True,'validity_text':text(state.get('subtitle'))}}
        if method=='orders.get':
            states=collect(page);order=normalize_detail(states,order_id)
            return {'operation_status':'ok' if order['complete'] else 'partial','orders':[order],'complete':order['complete']}
        refs={};warnings=[];states=collect(page);has_widget=bool(states.get('orderList'))
        max_pages=params.get('max_pages',5);max_orders=params.get('max_orders',30);exhausted=False
        seen_pages=set()
        for step in range(max_pages):
            found,notes=index_rows(states);warnings.extend(notes)
            for row in found:
                if row['id'] not in refs or refs[row['id']]['list_status'] in TERMINAL:refs[row['id']]=row
            if len(refs)>=max_orders:break
            paginator=next((p for p in states.get('paginator',[]) if p.get('layoutContainer')=='order-list-desktop'),None)
            if paginator is None or not paginator.get('nextPage'):
                exhausted=bool(states.get('orderList'));break
            link=safe_link(paginator.get('nextPage'),'/my/orderlist/')
            if not link or link in seen_pages:
                warnings.append('pagination_unresolved');break
            if step+1>=max_pages:break
            seen_pages.add(link)
            response=page.goto(link,wait_until='domcontentloaded',timeout=30000)
            page.wait_for_timeout(1200);states=collect(page)
            if not response or response.status!=200 or not states.get('orderList'):
                warnings.append('pagination_unavailable');break
        if not has_widget:return {'operation_status':'order_list_unavailable','orders':[],'complete':False}
        selected=[r for r in refs.values() if not params.get('active_only',True) or r['list_status'] not in TERMINAL][:max_orders]
        orders=[];failed=[]
        for row in selected:
            try:
                order=normalize_detail(navigate(page,row['url']),row['id']);orders.append(order)
                if not order['complete']:failed.append({'order_id':row['id'],'reason':'detail_schema_changed'})
            except Exception as error:
                failed.append({'order_id':row['id'],'reason':type(error).__name__})
                # Do not retry or continue many navigations after lost access.
                break
        return {'operation_status':'partial' if failed or warnings else 'ok','orders':orders,
                'complete':exhausted and not failed and not warnings and len(selected)==len(orders),'coverage':{'index_orders_observed':len(refs),'orders_requested':len(selected),
                'orders_read':len(orders),'scope':'active' if params.get('active_only',True) else 'history',
                'history_exhaustive':exhausted,'max_pages':max_pages,'max_orders':max_orders},
                'warnings':list(dict.fromkeys(warnings+([] if exhausted else ['bounded_order_history']))),'failures':failed}
    result=browse(initial,session=session,operation=operation,mode='native')
    return {k:result[k] for k in ('status','authenticated','session_saved','group','process_groups','orders','complete','coverage','warnings','failures','artifact') if k in result}
