import datetime as dt
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'marketplace-shopping/assets/marketplace'))
from agenda import agenda
CLOCK=dt.datetime(2026,10,7,10,tzinfo=dt.timezone.utc)

def snapshot(status='ready_for_pickup',stamp='2026-10-07T09:00:00+00:00',deadline='2026-10-08'):
 return {'marketplace':'ozon','observed_at':stamp,'data':{'complete':True,'orders':[{'id':'A','url':'https://www.ozon.ru/my/orderdetails/?order=A','shipments':[{'id':'1','status':status,'address':'Example 5','pickup_until':deadline,'items':[]}]}]}}

class Agenda(unittest.TestCase):
 def test_inclusive_deadline_and_missing(self):
  r=agenda([snapshot()], '2026-10-07',clock=CLOCK)
  self.assertEqual(r['alerts'][0]['kind'],'deadline_soon');self.assertEqual(r['alerts'][0]['days_remaining'],1)
  self.assertEqual(len(r['pickup_addresses']),1);self.assertFalse(r['receipt_codes_included'])
  r=agenda([snapshot(deadline='2026-10-07')], '2026-10-07',clock=CLOCK);self.assertEqual(r['alerts'][0]['days_remaining'],0)
  r=agenda([snapshot(deadline=None)],'2026-10-07',clock=CLOCK);self.assertIsNone(r['alerts'][0]['days_remaining'])
 def test_new_delivered_snapshot_replaces_old_ready(self):
  r=agenda([snapshot(stamp='2026-10-06T10:00:00+00:00'),snapshot('delivered')],'2026-10-07',clock=CLOCK)
  self.assertEqual(r['ready_for_pickup'],[]);self.assertEqual(r['alerts'],[])
 def test_range_and_stale(self):
  d=snapshot('in_transit',stamp='2026-10-01T10:00:00+00:00');d['data']['orders'][0]['shipments'][0]['expected']={'from':'2026-10-06','to':'2026-10-08'}
  r=agenda([d],'2026-10-07',days=7,clock=CLOCK)
  self.assertTrue(r['warnings']);self.assertEqual(len(r['by_day']['2026-10-07']),1);self.assertEqual(len(r['by_day']['2026-10-09']),0)
 def test_overdue_not_assumed_returned(self):
  r=agenda([snapshot(deadline='2026-10-05')],'2026-10-07',clock=CLOCK)
  self.assertEqual(len(r['ready_for_pickup']),1);self.assertEqual(r['alerts'][0]['kind'],'overdue')

 def test_normal_ready_alert_dedupes_across_days(self):
  a=agenda([snapshot(deadline='2026-10-17')],'2026-10-07',clock=CLOCK)
  b=agenda([snapshot(deadline='2026-10-17')],'2026-10-08',clock=CLOCK+dt.timedelta(days=1))
  self.assertEqual(a['alerts'][0]['fingerprint'],b['alerts'][0]['fingerprint'])
 def test_unparsed_orders_remain_visible(self):
  d={'marketplace':'aliexpress','observed_at':CLOCK.isoformat(),'data':{'orders':[{'marketplace':'aliexpress','id':'a','url':'example','shipments':[],'missing':['address']}]}}
  r=agenda([d],'2026-10-07',clock=CLOCK)
  self.assertFalse(r['complete']);self.assertEqual(len(r['unresolved_orders']),1)

if __name__=='__main__':unittest.main()
