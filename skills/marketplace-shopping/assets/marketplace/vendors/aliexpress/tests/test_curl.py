#!/usr/bin/env python3
"""Offline diagnostic behavior tests; no actual credentials or network."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import curl_probe


class CurlTests(unittest.TestCase):
    def fake(self, responses, records):
        def run(args, **kwargs):
            records.append((args,kwargs))
            status, location, body, exitcode = responses.pop(0)
            headers = f'HTTP/2 {status}\r\nContent-Type: text/html\r\n'
            if location: headers += f'Location: {location}\r\n'
            Path(args[args.index('--dump-header')+1]).write_text(headers+'\r\n')
            Path(args[args.index('--output')+1]).write_text(body)
            return subprocess.CompletedProcess(args,exitcode,f'{status}\t1.0\t2\t192.0.2.1\t0\t0','')
        return run

    def test_direct_mode_ignores_client_proxy_configuration(self):
        records=[]
        with patch.dict('os.environ',{'HTTPS_PROXY':'http://example.invalid:1234','ALL_PROXY':'socks5://example.invalid:1234'}):
            with patch('curl_probe.subprocess.run',self.fake([(200,None,'<html></html>',0)],records)):
                result=curl_probe.probe('test')
        args,options=records[0]
        self.assertEqual(args[:2],['curl','-q'])
        self.assertEqual(args[args.index('--proxy')+1],'')
        self.assertEqual(args[args.index('--noproxy')+1],'*')
        self.assertNotIn('HTTPS_PROXY',options['env'])
        self.assertNotIn('ALL_PROXY',options['env'])
        self.assertEqual(result['status'],'http_ok')

    def test_external_redirect_never_requested(self):
        records=[]
        with patch('curl_probe.subprocess.run',self.fake([(302,'https://evil.test/?token=SECRET','',0)],records)):
            result=curl_probe.probe('test',follow=True)
        self.assertEqual(len(records),1)
        self.assertEqual(result['status'],'redirect_outside_scope')
        self.assertNotIn('SECRET',json.dumps(result))

    def test_timeout_200_partial_html_is_not_success(self):
        records=[]
        with patch('curl_probe.subprocess.run',self.fake([(200,None,'<html><title>Test</title>',28)],records)):
            result=curl_probe.probe('test')
        self.assertEqual(result['status'],'timeout')
        self.assertFalse(result['hops'][0]['complete_transfer'])
        self.assertFalse(result['hops'][0]['html_closing_tag'])

    def test_authorized_redirect_is_followed_without_query_in_report(self):
        records=[]
        responses=[(302,'https://login.aliexpress.ru/sync?token=SECRET','',0),(200,None,'<html></html>',0)]
        with patch('curl_probe.subprocess.run',self.fake(responses,records)):
            result=curl_probe.probe('test',follow=True)
        self.assertEqual(len(records),2)
        self.assertEqual(result['hops'][-1]['host'],'login.aliexpress.ru')
        self.assertNotIn('SECRET',json.dumps(result))


if __name__ == '__main__': unittest.main(verbosity=2)
