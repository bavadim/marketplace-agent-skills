"""Thin JSON/CLI boundary. Credentials never appear in requests or responses."""
import argparse,json,os,sys,tempfile
from pathlib import Path
from common import now,read_json,write_private
from api import capabilities,validate,dispatch


def main(scope):
 os.umask(0o077)
 parser=argparse.ArgumentParser(description='Unified marketplace JSON API')
 parser.add_argument('command',choices=['capabilities','call']);parser.add_argument('--request',help='JSON file or - for stdin')
 parser.add_argument('--output',type=Path);a=parser.parse_args();method=None
 try:
  if a.command=='capabilities':result=capabilities(scope)
  else:
   if not a.request:raise ValueError('request_required')
   request=json.loads(sys.stdin.read(1024*1024)) if a.request=='-' else read_json(a.request)
   method,params=validate(request,scope);data=dispatch(method,params);status=data.get('status','ok')
   observed=data.get('observed_at') or now()
   result={'schema_version':2,'method':method,'ok':status in ('ok','partial','ready','starting','closed','cancelled'),'status':status,'observed_at':observed,'data':data}
 except Exception as error:
  result={'schema_version':2,'method':method,'ok':False,'status':'error','observed_at':now(),'data':{'error_type':type(error).__name__}}
  if isinstance(error,ValueError) and str(error).replace('_','').isalnum():result['data']['reason']=str(error)
  if method in ('cart.set','cart.apply','cart.remove'):result['data']['requires_cart_read']=True
 if a.output:
  write_private(a.output,result);print(json.dumps({'status':result.get('status','ok'),'output':str(a.output)},ensure_ascii=False))
 else:print(json.dumps(result,ensure_ascii=False,indent=2))
 return 0 if result.get('ok',True) else 1
