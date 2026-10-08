"""Public orchestration API: provider choice is optional; default reads use both."""
from concurrent.futures import ThreadPoolExecutor
import copy,datetime as dt,hashlib,json,os
from pathlib import Path
from common import now,read_json,write_private,measurements
from schema import SITES,identity,order_identity,order_rows,options,CART_URLS
from providers.factory import create_provider,for_product,for_order
from providers.transport import state_root
from compare import compare

METHODS={
 'search':(['query'],['queries','providers','limit']),
 'product':(['ref'],['variant_limit','quantity','city','images_dir']),
 'quote':(['ref','city'],['quantity']),
 'images':(['ref','path'],['limit']),
 'review':(['input','observations'],[]),
 'compare':(['input'],['quantity','unit','budget','deadline','policy']),
 'cart.list':([],['providers']),
 'cart.set':(['ref','quantity','max_unit_price','operation_id'],['expected_quantity','expected_options']),
 'cart.apply':(['items','operation_id'],['mode']),
 'cart.remove':(['ref','operation_id'],['expected_quantity']),
 'session.ensure':([],['providers','profile']),
 'session.refresh':([],['providers','profile']),
 'session.status':([],['providers']),
 'session.validate':([],['providers']),
 'session.login.start':([],['providers','ttl_seconds']),
 'session.login.status':(['handoff_id'],[]),
 'session.login.finish':(['handoff_id'],[]),
 'session.login.cancel':(['handoff_id'],[]),
 'session.forget':(['providers'],[]),
 'orders.list':([],['providers','active_only','max_orders','max_pages']),
 'orders.get':(['ref'],[]),'orders.code':(['ref','path'],[]),
 'orders.schedule':([],['input','providers','active_only','max_orders','max_pages','date','days','warn_days','state']),
 'orders.enrich':(['input'],['max_products','images_dir']),
 'orders.ack':(['state','fingerprints'],[]),
 'measurements.parse':(['attributes'],['source_url']),
}
SCOPES={'shopping':{'search','product','quote','images','review','compare','cart.list','cart.set','cart.apply','cart.remove','session.ensure','session.refresh','session.status'},
        'orders':{m for m in METHODS if m.startswith('orders.')}|{'session.ensure','session.refresh','session.status','measurements.parse'}}
SESSION_METHODS={m for m in METHODS if m.startswith('session.')}
SCOPES['accounts']=SESSION_METHODS
SCOPES['shopping']|=SESSION_METHODS
SCOPES['orders']|=SESSION_METHODS


def capabilities(scope):
 return {'schema_version':2,'scope':scope,'default_providers':list(SITES),'methods':{m:{'required':METHODS[m][0],'optional':METHODS[m][1]} for m in sorted(SCOPES[scope])},
         'limitations':{'aliexpress':'Live empty active orders verified; shipment details and pickup-code export unavailable.',
                        'ozon':'Delivery widget may omit cost/address; quotes are unknown until observed. Receipt export is a daily numeric card.'}}


def validate(request,scope):
 if not isinstance(request,dict) or set(request)-{'method','params'}:raise ValueError('invalid_request')
 method=request.get('method');params=request.get('params',{})
 if method not in SCOPES[scope] or not isinstance(params,dict):raise ValueError('unknown_method')
 required,optional=METHODS[method]
 if method=='search' and 'queries' in params:required=[]
 if set(required)-set(params) or set(params)-set(required+optional+(['query'] if method=='search' else [])):raise ValueError('invalid_parameters')
 if 'providers' in params:
  if not isinstance(params['providers'],list) or not params['providers'] or len(set(params['providers']))!=len(params['providers']) or any(s not in SITES for s in params['providers']):raise ValueError('invalid_providers')
 for key in ['quantity','expected_quantity','limit','variant_limit','max_orders','max_pages','max_products','days','warn_days']:
  if key in params:
   v=params[key]
   if type(v) is not int or v<0:raise ValueError('invalid_integer')
   if key in ('limit','max_orders','max_pages','max_products','days') and not v:raise ValueError('positive_integer_required')
 for key,maximum in [('limit',100),('variant_limit',24),('max_orders',100),('max_pages',20),('max_products',30),('days',31),('warn_days',30)]:
  if params.get(key,0)>maximum:raise ValueError('limit_exceeded')
 if method=='images' and params.get('limit',12)>32:raise ValueError('image_limit_max_32')
 if method in ('product','quote') and params.get('quantity',1)<1:raise ValueError('positive_quantity_required')
 if method=='cart.remove':validate({'method':'cart.set','params':{**params,'quantity':0,'max_unit_price':1}},scope)
 if method=='cart.set':
  identity(params['ref']);validate_cap(params['max_unit_price'])
  if not isinstance(params['operation_id'],str) or not 1<=len(params['operation_id'])<=160:raise ValueError('operation_id_required')
 if method=='cart.apply':
  if params.get('mode','ensure') not in ('ensure','set'):raise ValueError('invalid_cart_mode')
  if not isinstance(params['items'],list) or not 1<=len(params['items'])<=20:raise ValueError('invalid_cart_plan')
  if not isinstance(params['operation_id'],str) or not 1<=len(params['operation_id'])<=160:raise ValueError('operation_id_required')
  canonical_refs=set()
  for row in params['items']:
   if not isinstance(row,dict) or set(row)-{'ref','quantity','max_unit_price','expected_quantity','expected_options'} or not {'ref','quantity','max_unit_price'}<=set(row):raise ValueError('invalid_cart_plan')
   validate({'method':'cart.set','params':{**row,'operation_id':params['operation_id']}},scope)
   from schema import product_ref
   site,pid,sku,url=identity(row['ref']);canonical=product_ref(site,pid,sku)
   if canonical in canonical_refs:raise ValueError('duplicate_cart_ref')
   canonical_refs.add(canonical)
 if 'policy' in params:
  if not isinstance(params['policy'],dict) or set(params['policy'])-{'max_age_hours','high_price_factor','fast_delivery_premium','min_seller_rating_5'}:raise ValueError('invalid_policy')
  if 'min_seller_rating_5' in params['policy']:
   from decimal import Decimal
   n=Decimal(str(params['policy']['min_seller_rating_5']))
   if not n.is_finite() or not 0<=n<=5:raise ValueError('invalid_seller_rating')
 if 'budget' in params:validate_cap(params['budget'])
 if 'expected_options' in params and (not isinstance(params['expected_options'],dict) or any(not isinstance(v,(str,list)) for v in params['expected_options'].values())):raise ValueError('invalid_options')
 if 'active_only' in params and type(params['active_only']) is not bool:raise ValueError('boolean_required')
 for k in ('date','deadline'):
  if k in params:dt.date.fromisoformat(params[k])
 for key in ('ref','query','city','path','input','images_dir','profile','state','unit','source_url','handoff_id'):
  if key in params and (not isinstance(params[key],str) or not params[key] or len(params[key])>8192):raise ValueError('invalid_string')
 if method=='search':
  queries=params.get('queries',[params.get('query')])
  if not isinstance(queries,list) or not 1<=len(queries)<=8 or any(not isinstance(q,str) or not q.strip() or len(q)>500 or q.startswith('-') for q in queries):raise ValueError('invalid_queries')
 if 'ttl_seconds' in params and (type(params['ttl_seconds']) is not int or not 60<=params['ttl_seconds']<=3600):raise ValueError('invalid_ttl_seconds')
 if 'handoff_id' in params:
  import uuid
  if str(uuid.UUID(params['handoff_id']))!=params['handoff_id']:raise ValueError('invalid_handoff_id')
 if method in ('product','quote','images'):identity(params['ref'])
 if method in ('orders.get','orders.code'):order_identity(params['ref'])
 return method,params


def validate_cap(value):
 from compare import positive
 positive(value)


def providers(params):return [create_provider(name) for name in params.get('providers',SITES)]


def safely(fn,provider):
 try:return fn(provider)
 except Exception as error:
  return {'status':'provider_error','provider':provider.name,'error_type':type(error).__name__,'complete':False}


def read_all(params,fn):
 clients=providers(params)
 with ThreadPoolExecutor(max_workers=len(clients)) as pool:
  return list(pool.map(lambda provider:safely(fn,provider),clients))


def summary(results):
 good=[r for r in results if r.get('status') in ('ok','partial')]
 return 'ok' if len(good)==len(results) and all(r.get('status')=='ok' for r in good) else 'partial' if good else 'providers_unavailable'


def sources(results):
 return [{'provider':r['provider'],'status':r.get('status'),'complete':r.get('complete',False),
          'warnings':r.get('warnings',[]),'failures':r.get('failures',[]),'coverage':r.get('coverage'),'reason':r.get('reason'),'observed_at':r.get('observed_at') or now()} for r in results]


def order_list(params):
 results=read_all(params,lambda p:p.orders_list(params.get('active_only',True),params.get('max_orders',30),params.get('max_pages',5)))
 orders=[o for result in results for o in result.get('orders',[])]
 # Namespace every identifier; identical provider order numbers are unrelated.
 orders=list({o['ref']:o for o in orders}.values())
 return {'status':summary(results),'orders':orders,'items':order_rows(orders),'complete':all(r.get('complete',False) for r in results),
         'sources':sources(results),'observed_at':now(),'scope':'active' if params.get('active_only',True) else 'history'}


def product_from_file(path):
 doc=read_json(path);data=doc.get('data',doc)
 return data.get('product',data)


def apply_review(params):
 product=copy.deepcopy(product_from_file(params['input']));review=read_json(params['observations'])
 if not isinstance(review,dict) or set(review)-{'ref','complete','compatible','pack_size','unit','package_contents','facts','evidence','unresolved','price_explained'}:raise ValueError('invalid_review')
 if review.get('ref')!=product.get('ref'):raise ValueError('review_variant_mismatch')
 if type(review.get('complete')) is not bool or type(review.get('compatible')) is not bool:raise ValueError('review_flags_required')
 if not isinstance(review.get('evidence'),list) or not review['evidence']:raise ValueError('review_evidence_required')
 for evidence in review['evidence']:
  if not isinstance(evidence,dict) or not evidence.get('source') or not evidence.get('text') or evidence.get('reviewed') is not True:raise ValueError('review_evidence_required')
 if review.get('complete'):
  validate_cap(review.get('pack_size'))
  if not review.get('unit') or not isinstance(review.get('package_contents'),list) or not review['package_contents'] or review.get('unresolved'):raise ValueError('review_incomplete')
 product['review']={**review,'observed_at':now()}
 return {'status':'ok','product':product}


def cart_apply(params):
 # Durable plan identity and pending marker; do not lose an uncertain write in /tmp.
 plan=copy.deepcopy(params['items']);mode=params.get('mode','ensure')
 for row in plan:
  from schema import product_ref
  site,pid,sku,url=identity(row['ref']);row['ref']=product_ref(site,pid,sku)
 operation=params['operation_id'];journal=state_root()/'plans'/(hashlib.sha256(operation.encode()).hexdigest()+'.json')
 identity_hash=hashlib.sha256(json.dumps({'items':plan,'mode':mode},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 import fcntl
 journal.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 lockpath=state_root()/'cart.lock';fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if journal.exists():
   record=read_json(journal)
   if record['identity']!=identity_hash:raise ValueError('operation_id_plan_mismatch')
  else:record={'identity':identity_hash,'steps':[],'complete':False,'mode':mode}
  # Read every selected provider before the first write.
  names=list(dict.fromkeys(identity(row['ref'])[0] for row in plan))
  baseline={r['provider']:r for r in read_all({'providers':names},lambda p:p.cart_list())}
  if any(r['status']!='ok' or not r['complete'] for r in baseline.values()):return {'status':'cart_read_required','requires_cart_read':True,'sources':sources(list(baseline.values()))}
  if 'targets' not in record:
   record['targets']={row['ref']:max(row['quantity'],next((i['quantity'] for i in baseline[identity(row['ref'])[0]]['items'] if i['ref']==row['ref']),0)) if mode=='ensure' else row['quantity'] for row in plan}
   write_private(journal,record)
  for index,original in enumerate(plan):
   row={**original,'quantity':record['targets'][original['ref']]}
   ref=row['ref'];provider=for_product(ref)
   if index<len(record['steps']) and record['steps'][index].get('status')=='ok':
    current=provider.cart_list();line=next((i for i in current.get('items',[]) if i['ref']==ref),None)
    if current['status']=='ok' and current['complete'] and (line['quantity'] if line else 0)==row['quantity']:continue
    return {'status':'completed_step_changed','requires_cart_read':True,'journal':str(journal)}
   if record.get('pending_ref')==ref and record.get('pending_before'):
    prior=record['pending_before'];current=provider.cart_list()
    if current['status']!='ok' or not current.get('complete'):return {'status':'pending_write_unverified','requires_cart_read':True,'journal':str(journal)}
    current_line=next((i for i in current['items'] if i['ref']==ref),None)
    actual=current_line['quantity'] if current_line else 0
    def others(cart):return {i['ref']:(i['quantity'],i.get('selected')) for i in cart['items'] if i['ref']!=ref}
    if actual==row['quantity'] and others(prior)==others(current):
     from decimal import Decimal
     price=(current_line or {}).get('price') or {}
     if current_line and (price.get('unit') is None or price.get('currency')!='RUB' or Decimal(price['unit'])>Decimal(str(row['max_unit_price']))):
      return {'status':'pending_price_unverified','requires_cart_read':True,'journal':str(journal)}
     step={'ref':ref,'status':'ok','result':{'status':'ok','reconciled':True,'cart':current}}
     if index<len(record['steps']):record['steps'][index]=step
     else:record['steps'].append(step)
     record.pop('pending_ref',None);record.pop('pending_before',None);write_private(journal,record);continue
    prior_line=next((i for i in prior['items'] if i['ref']==ref),None)
    if actual!=(prior_line['quantity'] if prior_line else 0) or others(prior)!=others(current):
     return {'status':'pending_write_conflict','requires_cart_read':True,'journal':str(journal)}
   record['pending_ref']=ref;write_private(journal,record)
   step_id=hashlib.sha256((operation+':'+str(index)).encode()).hexdigest()
   current_before=provider.cart_list()
   record['pending_before']=current_before;write_private(journal,record)
   result=provider.cart_set(operation_id=step_id,**row)
   step={'ref':ref,'status':result['status'],'result':result}
   if index<len(record['steps']):record['steps'][index]=step
   else:record['steps'].append(step)
   if result['status']=='ok':record.pop('pending_ref',None);record.pop('pending_before',None)
   write_private(journal,record)
   if result['status']!='ok':return {'status':'partial','steps':record['steps'],'requires_cart_read':True,'journal':str(journal),'complete':False}
  record['complete']=True;write_private(journal,record)
 return {'status':'ok','steps':record['steps'],'complete':True,'journal':str(journal),'cart_links':[CART_URLS[s] for s in names]}


def dispatch(method,params):
 if method=='search':
  queries=params.get('queries',[params.get('query')]);limit=params.get('limit',12)
  def search_provider(p):
   unique={};states=[]
   for query in queries:
    result=p.search(query,limit);states.append(result)
    for row in result.get('products',[]):unique.setdefault(row['ref'],row)
    if result['status'] not in ('ok','partial'):break
   return {'status':states[-1]['status'] if not any(r['status'] in ('ok','partial') for r in states) else summary(states),'provider':p.name,'products':list(unique.values())[:limit],'complete':False,'warnings':['bounded_search'],'failures':[{'query':q,'status':r['status']} for q,r in zip(queries,states) if r['status'] not in ('ok','partial')],'observed_at':now()}
  results=read_all(params,search_provider)
  return {'status':summary(results),'products':[p for r in results for p in r.get('products',[])],'sources':sources(results),'complete':False,'queries':queries}
 if method=='product':return for_product(params['ref']).product(**params)
 if method=='quote':
  result=for_product(params['ref']).quote(**params);product=result.get('product')
  return {'status':result['status'],'product':product,'delivery':product.get('delivery') if product else None,'options':product.get('delivery_options',[]) if product else []}
 if method=='images':return for_product(params['ref']).images(**params)
 if method=='review':return apply_review(params)
 if method=='compare':
  doc=read_json(params['input']);data=doc.get('data',doc)
  rows=data if isinstance(data,list) else data.get('products') or data.get('offers') or [data.get('product',data)]
  return compare(rows,**{k:v for k,v in params.items() if k!='input'})
 if method=='cart.list':
  results=read_all(params,lambda p:p.cart_list())
  return {'status':summary(results),'items':[i for r in results for i in r.get('items',[])],'complete':all(r.get('complete',False) for r in results),
          'sources':sources(results),'cart_links':[CART_URLS[p.name] for p in providers(params)]}
 if method=='cart.set':return cart_apply({'items':[{k:v for k,v in params.items() if k!='operation_id'}],'operation_id':params['operation_id'],'mode':'set'})
 if method=='cart.apply':return cart_apply(params)
 if method=='cart.remove':return dispatch('cart.set',{**params,'quantity':0,'max_unit_price':1})
 if method.startswith('session.'):
  if not params.get('profile'):
   from accounts import dispatch as account_dispatch
   return account_dispatch(method,params)
  results=read_all(params,lambda p:p.session(method.split('.')[1],params.get('profile')))
  return {'status':summary(results),'sessions':results,'sources':sources(results)}
 if method=='orders.list':return order_list(params)
 if method=='orders.get':
  site,oid=order_identity(params['ref']);result=for_order(params['ref']).order_get(oid)
  return {**result,'items':order_rows(result.get('orders',[]))}
 if method=='orders.code':
  site,oid=order_identity(params['ref']);return for_order(params['ref']).order_code(oid,params['path'])
 if method=='orders.schedule':
  from agenda import agenda
  from common import today
  document=read_json(params['input']) if params.get('input') else {'data':order_list(params),'observed_at':now()}
  data=document.get('data',document)
  documents=[{'marketplace':source['provider'],'observed_at':source.get('observed_at') or document.get('observed_at') or data.get('observed_at'),
              'data':{'status':source['status'],'complete':source.get('complete',False),'orders':[o for o in data.get('orders',[]) if o['provider']==source['provider']]}} for source in data.get('sources',[])]
  if not documents:documents=[document]
  result=agenda(documents,params.get('date') or today().isoformat(),params.get('days',7),params.get('warn_days',2))
  seen=read_json(params['state']).get('acknowledged',{}) if params.get('state') and Path(params['state']).exists() else {}
  result['pending_alerts']=[a for a in result['alerts'] if a['fingerprint'] not in seen]
  result['status']=data.get('status','ok');result['sources']=data.get('sources',[])
  return result
 if method=='orders.enrich':
  document=read_json(params['input']);data=document.get('data',document);data=copy.deepcopy(data)
  cache={};review=[];limit=params.get('max_products',10)
  for order in data.get('orders',[]):
   for shipment in order.get('shipments',[]):
    for item in shipment.get('items',[]):
     url=item.get('url');facts=None
     if url:
      key=(order['provider'],url)
      if key not in cache and len(cache)<limit:
       result=for_product(url).product(url);p=result.get('product')
       parsed=measurements(p.get('specifications',[]),url) if p else {'unknown':True,'needs_review':[]}
       parsed['status']=result['status'];parsed['observed_at']=now();parsed['provenance']='current_product_listing_not_shipping_measurement'
       if p and (parsed['unknown'] or parsed['needs_review']):
        parsed['review']={'description':p.get('description'),'images':p.get('images')}
        if params.get('images_dir'):
         path=str(Path(params['images_dir'])/hashlib.sha256(url.encode()).hexdigest()[:16]);parsed['review']['image_evidence']=for_product(url).images(url,path)['evidence']
       cache[key]=parsed
      facts=cache.get(key)
     item['measurements']=facts
     if not facts or facts.get('status')!='ok' or facts.get('unknown') or facts.get('needs_review'):review.append({'order_ref':order['ref'],'shipment_id':shipment['id'],'title':item.get('title'),'url':url})
  data.update(status=data.get('status','ok'),enriched_at=now(),measurement_review_queue=review,measurement_coverage={'products_read':len(cache),'limit':limit,'complete':not review})
  data['items']=order_rows(data.get('orders',[]));return data
 if method=='orders.ack':
  import re
  if not isinstance(params['fingerprints'],list) or not params['fingerprints'] or any(not re.fullmatch(r'[0-9a-f]{64}',f) for f in params['fingerprints']):raise ValueError('invalid_fingerprints')
  path=Path(params['state']);seen=read_json(path).get('acknowledged',{}) if path.exists() else {}
  cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=60)).isoformat();seen={k:v for k,v in seen.items() if v>=cutoff}
  for fingerprint in params['fingerprints']:seen[fingerprint]=now()
  write_private(path,{'acknowledged':seen});return {'status':'ok','acknowledged':len(params['fingerprints'])}
 if method=='measurements.parse':return {'status':'ok',**measurements(params['attributes'],params.get('source_url'))}
 raise ValueError('unknown_method')
