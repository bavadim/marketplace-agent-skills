"""Compare curl/headless egress without sending the user's Ozon session."""
import ipaddress
import json
import subprocess

from ozon_browser import launch_options, process_groups

IP_ENDPOINT = 'https://api.ipify.org?format=json'


def public_ip(text):
    try:
        return str(ipaddress.ip_address(json.loads(text)['ip']))
    except (ValueError, KeyError, TypeError):
        return None


def check(executable=None):
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError:
        return {'status':'browser_dependency_missing','client':'network-check'}
    options = launch_options(executable)
    result = {'cookies_supplied':False, 'cart_modified':False, 'client':'network-check',
              'ip_endpoint':IP_ENDPOINT}
    command = ['curl','-q','--proxy','','--noproxy','*','--silent','--show-error',
               '--connect-timeout','8','--max-time','20',IP_ENDPOINT]
    curl = subprocess.run(command,capture_output=True,text=True,env=options['env'],timeout=25)
    result.update(curl_exit=curl.returncode,curl_ip=public_ip(curl.stdout) if not curl.returncode else None)
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(**options)
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                groups = process_groups(browser)
                result['process_group_check'] = groups
                if groups['status'] == 'mismatch':
                    return {**result,'status':'browser_group_mismatch'}
                response = page.goto(IP_ENDPOINT,wait_until='domcontentloaded',timeout=30000)
                result.update(browser_ip=public_ip(page.locator('body').inner_text(timeout=5000)),
                              browser_http_status=response.status if response else None,
                              browser_version=browser.version)
                context.close()
            finally:
                browser.close()
    except Error as error:
        return {**result,'status':'network_check_incomplete','error_type':type(error).__name__}
    ips = (result.get('curl_ip'),result.get('browser_ip'))
    result['egress_matches'] = ips[0] == ips[1] if all(ips) else None
    result['status'] = ('ok' if result['egress_matches'] and groups['status'] == 'verified'
                        else 'network_check_incomplete' if not all(ips) or groups['status'] != 'verified'
                        else 'egress_mismatch')
    return result
