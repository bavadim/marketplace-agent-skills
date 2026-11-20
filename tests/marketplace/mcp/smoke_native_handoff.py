"""Run inside the Marketplace image with --network none and its sandbox profile."""
import os, sys, tempfile, time, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0,'/app/mcp')
import viewer_worker as w
from browser_runtime import ManagedBrowser, metadata
seen=[]
class Fixture(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        if self.path.startswith('/report?'): seen.append(self.path)
        body=b'<script>if(location.pathname==="/seed"){localStorage.setItem("test","ok");fetch("/report?driver="+navigator.webdriver)}</script>'
        self.send_response(200); self.send_header('Content-Type','text/html')
        if self.path=='/seed':
            self.send_header('Set-Cookie','fixture_session=ok; Path=/')
            self.send_header('Set-Cookie','fixture_persistent=ok; Path=/; Max-Age=3600')
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
with tempfile.TemporaryDirectory() as tmp, ThreadingHTTPServer(('127.0.0.1',0),Fixture) as fixture:
    os.environ.update(HOME=tmp,XDG_STATE_HOME=tmp+'/state',XDG_DATA_HOME=tmp+'/data',XDG_CACHE_HOME=tmp+'/cache',XDG_CONFIG_HOME=tmp+'/cfg',MARKETPLACE_BROWSER_ROOT=tmp+'/profiles')
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    base=Path(tmp)/'v'; base.mkdir(); origin=f'http://127.0.0.1:{fixture.server_port}'
    w.SITES['ozon']['url']=origin+'/seed'
    def finish():
        for _ in range(60):
            if seen:
                time.sleep(2)
                (base/'check').touch(); return
            time.sleep(.2)
    threading.Thread(target=finish,daemon=True).start()
    result=w.run(base,time.monotonic()+20)
    print('native status',result,'webdriver report',seen,flush=True)
    assert result=='handed_off' and seen==['/report?driver=false'] and metadata('ozon')['authenticated'] is None
    with ManagedBrowser('ozon') as b:
        b.open(); assert b.profile==Path(metadata('ozon')['profile_path']);
        page=b.context.new_page(); page.goto(origin+'/check')
        assert page.evaluate('localStorage.getItem("test")')=='ok'
        assert 'fixture_persistent=ok' in page.evaluate('document.cookie')
        assert 'fixture_session=ok' in page.evaluate('document.cookie')
    print("Native handoff preserved browser storage and both cookie types.")
