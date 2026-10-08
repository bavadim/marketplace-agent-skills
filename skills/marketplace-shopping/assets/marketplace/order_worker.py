#!/usr/bin/env python3
"""Isolated order reads using owned headless sessions."""
import argparse,importlib.util,os,sys
from pathlib import Path
from common import read_json,write_private
ROOT=Path(__file__).resolve().parent


def main():
 p=argparse.ArgumentParser();p.add_argument('--provider',choices=['ozon','aliexpress'],required=True)
 p.add_argument('--method',choices=['orders.list','orders.get','orders.code'],required=True)
 p.add_argument('--request',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 try:
  if importlib.util.find_spec('patchright') is None:
   runtime=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'ozon-bom/venv/bin/python'
   if not runtime.is_file():raise ValueError('browser_setup_required')
   os.execv(str(runtime),[str(runtime),str(Path(__file__).resolve()),*sys.argv[1:]])
  sys.path.insert(0,str(ROOT/'vendors'/a.provider/'scripts'));sys.path.insert(0,str(ROOT/'order_adapters'))
  if a.provider=='ozon':from ozon_orders import run
  else:from ali_orders import run
  session=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/(a.provider+'-bom')/'session.json'
  result=run(a.method,read_json(a.request),session)
 except Exception as e:
  result={'status':getattr(e,'status','adapter_error'),'error_type':type(e).__name__,'complete':False}
 write_private(a.output,result)

if __name__ == '__main__':
    from launcher import launch
    launch()
    main()
