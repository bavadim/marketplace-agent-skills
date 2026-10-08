#!/usr/bin/env python3
"""Import only Ozon cookies from an authorized Chromium profile.

Uses the bundled AliExpress adapter's tested Chromium decryption and private-file
helpers; AliExpress cookies and its saved session are not read or modified.
"""
import argparse
import contextlib
import datetime as dt
import http.cookiejar
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

shared = Path(__file__).resolve().parents[2] / 'aliexpress/scripts'
if not (shared / 'chromium_session.py').is_file():
    raise ImportError('Bundled Chromium helpers are required')
sys.path.insert(0, str(shared))
from chromium_session import decrypt, storage_password
from firefox_session import acquire
from session_store import atomic_write, read_private, session_lock


def in_scope(host):
    return isinstance(host, str) and (host.lstrip('.') == 'ozon.ru' or host.endswith('.ozon.ru'))


def default_session():
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))) / 'ozon-bom/session.json'


def scoped_rows(profile):
    profile = Path(profile)
    database = profile/'Network/Cookies' if (profile/'Network/Cookies').is_file() else profile/'Cookies'
    copies = acquire(database.parent, database.name)
    with tempfile.TemporaryDirectory(prefix='ozon-chromium-') as tmp:
        root = Path(tmp)
        for name, content in copies.items():
            fd = os.open(root/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(content)
        del copies
        with contextlib.closing(sqlite3.connect(root/'Cookies')) as con:
            con.row_factory = sqlite3.Row
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('inconsistent_chromium_snapshot')
            version = int(con.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
            fields = {r[1] for r in con.execute('PRAGMA table_info(cookies)')}
            columns = {'host_key','name','value','encrypted_value','path','expires_utc',
                       'is_secure','is_httponly','is_persistent','has_expires'}
            if not columns <= fields: raise ValueError('unsupported_chromium_schema')
            columns |= {'top_frame_site_key', 'samesite'} & fields
            rows = [dict(r) for r in con.execute('SELECT '+','.join(sorted(columns))+
                ' FROM cookies WHERE host_key=? OR host_key LIKE ?', ('ozon.ru','%.ozon.ru'))]
    return version, rows


def export(profile, output):
    version, rows = scoped_rows(profile)
    eligible = []
    for row in rows:
        if not in_scope(row['host_key']) or row.get('top_frame_site_key'): continue
        persistent = row['is_persistent'] and row['has_expires']
        expiry = int(row['expires_utc'])//1000000 - 11644473600 if persistent else None
        if expiry is None or expiry > time.time(): eligible.append((row, expiry))
    password = storage_password() if any(r['encrypted_value'].startswith(b'v11') for r,_ in eligible) else None
    cookies = []
    for row, expiry in eligible:
        value = decrypt(row['encrypted_value'], row['host_key'], version, password) if row['encrypted_value'] else row['value']
        cookie = {'host':row['host_key'],'name':row['name'],'value':value,'path':row['path'],
                  'expiry':expiry,'isSecure':bool(row['is_secure']),'isHttpOnly':bool(row['is_httponly'])}
        same_site = {0:'None',1:'Lax',2:'Strict'}.get(row.get('samesite'))
        if same_site is not None: cookie['sameSite'] = same_site
        cookies.append(cookie)
    if not cookies: raise ValueError('no_unexpired_ozon_cookies')
    atomic_write(output, {'schema_version':1, 'domain_scope':'ozon.ru', 'browser':'chromium',
        'browser_profile':str(Path(profile).resolve()), 'exported_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'cookies':cookies, 'login_verified':False})
    return {'status':'imported', 'cookie_count':len(cookies), 'login_verified':False, 'session':str(output)}


def write_jar(session, output):
    document = read_private(session)
    if document.get('domain_scope') != 'ozon.ru': raise ValueError('session_scope_mismatch')
    lines = ['# Netscape HTTP Cookie File']
    for row in document['cookies']:
        if not in_scope(row['host']): raise ValueError('cookie_outside_scope')
        if row.get('expiry') and row['expiry'] <= time.time(): continue
        fields = [row[k] for k in ('host','name','value','path')]
        if any(not isinstance(v,str) or any(c in v for c in '\t\r\n\0') for v in fields):
            raise ValueError('invalid_cookie_characters')
        host = ('#HttpOnly_' if row.get('isHttpOnly') else '') + row['host']
        lines.append('\t'.join([host,'TRUE' if row['host'].startswith('.') else 'FALSE',row['path'],
            'TRUE' if row.get('isSecure') else 'FALSE',str(row.get('expiry') or 0),row['name'],row['value']]))
    Path(output).write_text('\n'.join(lines)+'\n')
    os.chmod(output,0o600)


def persist_jar(session, jar_path):
    """Called with the session lock held; retain only Ozon's unexpired cookies."""
    document = read_private(session)
    if document.get('domain_scope') != 'ozon.ru': raise ValueError('session_scope_mismatch')
    jar = http.cookiejar.MozillaCookieJar(str(jar_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    previous = {(c['host'],c['name'],c['path']):c for c in document['cookies']}
    cookies = []
    for cookie in jar:
        if not in_scope(cookie.domain): continue
        expiry = cookie.expires or None
        if expiry is not None and expiry <= time.time(): continue
        row = {'host':cookie.domain,'name':cookie.name,'value':cookie.value,'path':cookie.path,
            'expiry':expiry,'isSecure':cookie.secure,
            'isHttpOnly':cookie.has_nonstandard_attr('HTTPOnly') or cookie.has_nonstandard_attr('HttpOnly')}
        same_site = previous.get((cookie.domain,cookie.name,cookie.path),{}).get('sameSite')
        if same_site is not None: row['sameSite'] = same_site
        cookies.append(row)
    document.update(cookies=cookies, updated_at=dt.datetime.now(dt.timezone.utc).isoformat())
    atomic_write(session, document)


def status(path):
    if not Path(path).is_file(): return {'status':'session_missing','login_verified':False}
    doc = read_private(path)
    if doc.get('domain_scope') != 'ozon.ru': raise ValueError('session_scope_mismatch')
    return {'status':'saved','session':str(path),'browser':doc.get('browser'),
            'cookie_count':sum(in_scope(c['host']) and (not c.get('expiry') or c['expiry']>time.time()) for c in doc['cookies']),
            'login_verified':doc.get('login_verified') is True,'exported_at':doc.get('exported_at'),
            'last_verified_at':doc.get('last_verified_at')}


def prepare(path, action, profile=None):
    """Reuse the saved session; refresh only when explicitly requested."""
    path = Path(path)
    if action == 'status': return status(path)
    if action == 'ensure' and path.exists():
        return {**status(path), 'reused_saved_session':True}
    if profile is None and path.exists():
        profile = read_private(path).get('browser_profile')
    if profile is None: return {'status':'browser_profile_required','login_verified':False}
    with session_lock(path):
        result = export(profile,path)
    return {**result,'reused_saved_session':False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--session',type=Path,default=default_session())
    args = parser.parse_args()
    try:
        result = prepare(args.session,'refresh',args.profile)
    except Exception as error:
        print(json.dumps({'status':'import_failed','error_type':type(error).__name__}))
        return 2
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__ == '__main__': sys.exit(main())
