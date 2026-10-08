import base64,json,tempfile,unittest
from pathlib import Path
import sys
from unittest.mock import patch
runtime=Path(__file__).resolve().parents[2]/'marketplace-shopping/assets/marketplace'
sys.path.insert(0,str(runtime));sys.path.insert(0,str(runtime/'order_adapters'))
from common import now,date_window,status_code,write_private,measurements
from schema import order_rows
from ozon_orders import index_rows,normalize_detail
from api import dispatch

class Orders(unittest.TestCase):
 def test_combined_orders_namespace_same_order_and_shipment_ids(self):
  def provider(name):
   class Fake:
    def orders_list(self,*args):return {'provider':name,'status':'ok','complete':True,'observed_at':now(),'orders':[{'ref':name+':order:1','provider':name,'marketplace':name,'id':'1','url':'https://example.test/1','observed_at':now(),'complete':True,'shipments':[{'id':'s','status':'ready_for_pickup','address':'Example 5','items':[]}]}]}
   return Fake()
  with patch('api.providers',return_value=[provider('ozon'),provider('aliexpress')]):r=dispatch('orders.list',{})
  self.assertEqual(len(r['items']),2);self.assertEqual(len({x['row_ref'] for x in r['items']}),2);self.assertEqual(set(r['items'][0]),set(r['items'][1]))
 def test_partial_order_stays_in_flat_list(self):
  rows=order_rows([{'provider':'aliexpress','id':'1','url':'example','status':'in_transit','status_text':'В пути','shipments':[],'missing':['address']}])
  self.assertEqual(len(rows),1);self.assertFalse(rows[0]['complete']);self.assertIsNone(rows[0]['address'])
 def test_ready_group_and_delivered_part_do_not_hide_active_order(self):
  action={'link':'v2/cacheOrderProducts?data=redacted','params':{'data':base64.b64encode(json.dumps({'orders':[{'order_number':'1-1'},{'order_number':'1-2'}]}).encode()).decode()}}
  def row(label,a):return {'leftBlock':{'textIcon':{'text':{'text':label}}},'common':{'action':a}}
  rows,warnings=index_rows({'orderList':[{'ordersV2':[row('Можно забирать',action),row('Получен',{'link':'/my/orderdetails/?order=1-1'})]}]})
  self.assertFalse(warnings);self.assertEqual(len(rows),2);self.assertEqual(rows[0]['list_status'],'ready_for_pickup')
 def test_dates_and_receipt_status(self):
  self.assertEqual(status_code('Ожидает получения'),'ready_for_pickup');self.assertEqual(status_code('Получен 2 октября'),'delivered')
  self.assertEqual(date_window('30 декабря — 2 января','2026-12-20')['to'],'2027-01-02')
  self.assertEqual(date_window('Ожидаем 17–24 октября','2026-10-07')['from'],'2026-10-17')
 def test_product_axes_must_not_be_mixed_with_package_axes(self):
  parsed=measurements([{'name':'Вес, г','value':'100'},{'name':'Длина, см','value':'5'},{'name':'Ширина упаковки, см','value':'3'},{'name':'Высота упаковки, см','value':'2'}])
  self.assertTrue(parsed['unknown']);self.assertFalse(parsed['completeness']['product']['dimensions'])
 def test_private_file_mode_and_symlink_refusal(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'private.json';write_private(p,{});self.assertEqual(p.stat().st_mode&0o777,0o600)
   link=Path(directory)/'link';link.symlink_to(p)
   with self.assertRaises(OSError):write_private(link,{})
 def test_schedule_consumes_combined_snapshot_without_network(self):
  snapshot={'observed_at':now(),'data':{'status':'ok','sources':[{'provider':'aliexpress','status':'partial','complete':False,'observed_at':now()}],
    'orders':[{'provider':'aliexpress','marketplace':'aliexpress','id':'1','ref':'aliexpress:order:1','url':'example','shipments':[],'missing':['address']} ]}}
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'orders.json';write_private(p,snapshot)
   with patch('api.order_list',side_effect=AssertionError('Network not allowed')):r=dispatch('orders.schedule',{'input':str(p),'days':7})
  self.assertFalse(r['complete']);self.assertEqual(len(r['unresolved_orders']),1);self.assertFalse(r['receipt_codes_included'])

if __name__=='__main__':unittest.main()
