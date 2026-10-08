import copy,json,tempfile,unittest
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets/marketplace'))
from common import now,write_private
from schema import identity,product_ref,base_product,delivery,options
from providers.factory import create_provider
from providers.base import Provider
from providers.ozon import normalize as ozon_product,OzonProvider
from providers.aliexpress import normalize as ali_product,AliExpressProvider
from api import dispatch,validate,cart_apply
from compare import compare


def offer(ref='ozon:101',price='100',latest='2026-10-15',fee='0',pack=1,verified=True):
 site,pid,sku,url=identity(ref);p=base_product(site,pid,sku,url,'Synthetic part')
 p.update(variant_verified=True,available=True,stock=50)
 p['price']['unit']=price;p['delivery']=delivery(quote={'earliest':latest,'latest':latest,'cost':fee,'currency':'RUB','destination_key':'Test city','quantity':1})
 p['delivery']['verified']=verified;p['seller']={'id':ref,'name':ref,'rating':{'value':'4.8','scale':5,'count':50}}
 p['review']={'compatible':True,'complete':True,'pack_size':pack,'unit':'pcs','package_contents':['part'],'unresolved':[]}
 return p

class Unified(unittest.TestCase):
 def test_factory_implements_one_interface(self):
  for name in ('ozon','aliexpress'):self.assertIsInstance(create_provider(name),Provider)
  with self.assertRaises(ValueError):create_provider('untrusted')
 def test_opaque_refs_keep_exact_sku_and_reject_outside_scope(self):
  self.assertEqual(identity('aliexpress:100:200')[:3],('aliexpress','100','200'))
  self.assertEqual(identity('https://aliexpress.ru/item/100.html?sku_id=200')[:3],('aliexpress','100','200'))
  for r in ('https://ozon.ru.evil.com/product/100/','https://user@www.ozon.ru/product/100/','https://www.ozon.ru:444/product/100/','aliexpress:100:bad'):
   with self.assertRaises(ValueError):identity(r)
 def test_search_uses_both_and_keeps_success_if_other_fails(self):
  clients=[create_provider('ozon'),create_provider('aliexpress')]
  with patch('api.providers',return_value=clients),patch.object(clients[0],'search',return_value={'status':'ok','products':[offer()]}),patch.object(clients[1],'search',return_value={'status':'login_required','products':[]}):
   r=dispatch('search',{'query':'part'})
  self.assertEqual(r['status'],'partial');self.assertEqual(len(r['products']),1)
  self.assertEqual(r['sources'][1]['status'],'login_required')
 def test_product_shape_and_options_are_identical(self):
  ozon=ozon_product({'sku':'100','url':'https://www.ozon.ru/product/100/','title':'Part','selected_options':{'Voltage':['24 V']},'price':{'regular':100},'reviews':{},'aspects':[],'available':True})
  ali=ali_product({'product_id':'200','url':'https://aliexpress.ru/item/200.html','title':'Part','detail_status':'ok','selected_sku':{'sku_id':'300','price':{'value':'100','currency':'RUB'},'options':[{'name':'Voltage','value':'24 V'}],'available_quantity':10},'catalog':{'skus':[]}})
  self.assertEqual(set(ozon),set(ali));self.assertEqual(ozon['options'],ali['options']);self.assertEqual(ozon['price'],ali['price'])
 def test_cart_line_total_is_divided_by_quantity(self):
  raw={'status':'ok','authenticated':True,'cart':{'items':[{'sku':'100','quantity':2,'line_total':{'regular':600,'bank_offer':500},'selected':True}]}}
  with patch('providers.ozon.legacy',return_value=raw):r=OzonProvider().cart_list()
  self.assertTrue(r['complete']);self.assertEqual(r['items'][0]['price']['unit'],'300');self.assertEqual(r['items'][0]['line_total'],'600')
 def test_packing_and_shipping_quantity_are_not_confused(self):
  p=offer(pack=5);p['delivery']['quantity']=2;p['delivery']['cost']='50'
  r=compare([p],quantity=8);row=r['offers'][0]
  self.assertEqual(row['quantity'],2);self.assertEqual(row['goods_cost'],'200');self.assertEqual(row['total_cost'],'250')
  p['delivery']['quantity']=1;r=compare([p],quantity=8);self.assertIsNone(r['offers'][0]['total_cost']);self.assertEqual(r['decision'],'offer_choices')
 def test_balanced_prefers_faster_within_configured_premium(self):
  cheap=offer('ozon:101','100','2026-10-20');fast=offer('aliexpress:102:103','115','2026-10-10');expensive=offer('ozon:104','160','2026-10-09')
  r=compare([cheap,fast,expensive])
  self.assertEqual(r['cheapest_verified'],cheap['ref']);self.assertEqual(r['fastest_verified'],expensive['ref']);self.assertEqual(r['recommended'],[fast['ref']])
 def test_unknown_delivery_cost_returns_choices(self):
  known=offer();unknown=offer('aliexpress:102:103','90',fee=None)
  r=compare([known,unknown]);self.assertFalse(r['comparison_complete']);self.assertEqual(r['decision'],'offer_choices');self.assertIn(unknown['ref'],r['recommended'])
 def test_placeholder_is_checked_only_after_compatibility(self):
  rows=[offer('ozon:101','100'),offer('aliexpress:102:103','105'),offer('ozon:104','10000')]
  r=compare(rows);self.assertIn('price_outlier_requires_explanation',r['offers'][2]['reasons'])
  rows[2]['review']['price_explained']=True;r=compare(rows);self.assertTrue(r['offers'][2]['eligible'])
 def test_search_cards_and_unknown_required_specs_cannot_win(self):
  p=offer();p['stage']='search';r=compare([p]);self.assertEqual(r['decision'],'no_eligible_offers')
  p['stage']='product';p['review']['unresolved']=['Thread pitch'];r=compare([p]);self.assertFalse(r['offers'][0]['eligible'])
 def test_schema_rejects_unknown_fields_and_payment(self):
  for method,params in [('checkout',{}),('cart.set',{'ref':'ozon:1','quantity':True,'max_unit_price':100,'operation_id':'a'}),('search',{'query':'part','raw_url':'https://evil.com'}),('search',{'query':'part','providers':['unknown']})]:
   with self.assertRaises(ValueError):validate({'method':method,'params':params},'shopping')
 def test_review_cannot_replace_observed_price(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'p.json';r=Path(directory)/'r.json';write_private(p,{'product':offer()});write_private(r,{'ref':'ozon:101','price':1})
   with self.assertRaises(ValueError):dispatch('review',{'input':str(p),'observations':str(r)})
 def test_cart_plan_ensures_quantity_without_duplicates_on_retry(self):
  class Fake:
   name='ozon'
   def __init__(self):self.quantity=3;self.calls=0
   def cart_list(self):return {'provider':'ozon','status':'ok','complete':True,'items':[{'ref':'ozon:101','quantity':self.quantity,'selected':True}]}
   def cart_set(self,**row):self.calls+=1;self.quantity=row['quantity'];return {'status':'ok','changed':False}
  client=Fake()
  with tempfile.TemporaryDirectory() as directory,patch('api.state_root',return_value=Path(directory)),patch('api.create_provider',return_value=client),patch('api.for_product',return_value=client):
   params={'items':[{'ref':'ozon:101','quantity':1,'max_unit_price':100}],'operation_id':'test'}
   a=cart_apply(params);b=cart_apply(params)
   self.assertEqual(client.quantity,3);self.assertEqual(client.calls,1);self.assertTrue(b['complete']);self.assertTrue(Path(a['journal']).exists())
   with self.assertRaises(ValueError):cart_apply({**params,'items':[{'ref':'ozon:101','quantity':2,'max_unit_price':100}]})

 def test_cart_timeout_reconciles_instead_of_adding_again(self):
  class Fake:
   name='ozon'
   def __init__(self):self.quantity=0;self.calls=0
   def cart_list(self):return {'provider':'ozon','status':'ok','complete':True,'items':[{'ref':'ozon:101','quantity':self.quantity,'selected':True,'price':{'unit':'100','currency':'RUB'}}] if self.quantity else []}
   def cart_set(self,**row):self.calls+=1;self.quantity=row['quantity'];raise TimeoutError()
  client=Fake()
  with tempfile.TemporaryDirectory() as directory,patch('api.state_root',return_value=Path(directory)),patch('api.create_provider',return_value=client),patch('api.for_product',return_value=client):
   params={'items':[{'ref':'ozon:101','quantity':1,'max_unit_price':100,'expected_quantity':0}],'operation_id':'resume'}
   with self.assertRaises(TimeoutError):cart_apply(params)
   result=cart_apply(params)
   self.assertTrue(result['complete']);self.assertTrue(result['steps'][0]['result']['reconciled']);self.assertEqual(client.calls,1)
 def test_remove_uses_same_public_ref_contract(self):
  m,p=validate({'method':'cart.remove','params':{'ref':'aliexpress:1:2','operation_id':'delete'}},'shopping')
  self.assertEqual(m,'cart.remove')

if __name__=='__main__':unittest.main()
