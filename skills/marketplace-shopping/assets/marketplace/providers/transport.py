"""Run isolated adapters without a shell; keep resumable cart outputs private."""
import contextlib,fcntl,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from common import read_json,write_private
RUNTIME=Path(__file__).resolve().parents[1]
VENDORS=RUNTIME/'vendors'
SUCCESS={'ok','empty','success','verified','updated','unchanged','saved','imported','ready'}


def state_root():return Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'marketplace-shopping'


@contextlib.contextmanager
def provider_lock(site):
    root=state_root();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(root/(site+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        yield


def run_json(script,args,operation_id=None):
    with tempfile.TemporaryDirectory(prefix='marketplace-') as temp:
        output=Path(temp)/'result.json'
        if operation_id:
            key=hashlib.sha256((str(script)+'|'+operation_id).encode()).hexdigest()
            output=state_root()/'operations'/f'{key}.json';output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        before=output.stat().st_mtime_ns if output.exists() else None
        try:
            subprocess.run([sys.executable,str(script),*map(str,args),'--output',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        except (OSError,subprocess.SubprocessError):return {'status':'adapter_error','requires_cart_read':bool(operation_id)}
        if not output.is_file() or output.stat().st_mtime_ns==before:return {'status':'adapter_error','reason':'no_fresh_json','requires_cart_read':bool(operation_id)}
        result=read_json(output)
        if operation_id:result['journal_result']=str(output)
        return result


def legacy(site,args,operation_id=None):
    with provider_lock(site):
        return run_json(VENDORS/site/'scripts'/('ozon.py' if site=='ozon' else 'ali.py'),args,operation_id)


def status(raw):
    value=raw.get('status','ok')
    if value in SUCCESS or raw.get('detail_status')=='ok':return 'ok'
    if value in ('authentication_required','login_required'):return 'login_required'
    if value in ('needs_review','partial','structured_data_only'):return 'partial'
    return value


def order_call(site,method,params):
    with tempfile.TemporaryDirectory(prefix='market-orders-') as temp:
        request=Path(temp)/'request.json';write_private(request,params)
        with provider_lock(site):
            return run_json(RUNTIME/'order_worker.py',['--provider',site,'--method',method,'--request',request])
