#!/usr/bin/env python3
"""Single entry point for the AliExpress skill. Each command also supports --help."""
import os
from pathlib import Path
import sys

ROUTES={
    'session':('ali_session.py',()), 'search':('ali_http.py',('search',)),
    'search-bom':('batch_search.py',()), 'product':('ali_http.py',('product',)),
    'store':('ali_http.py',('store',)), 'images':('ali_http.py',('images',)),
    'destination':('pdp.py',('destination',)), 'quote':('pdp.py',('quote',)),
    'variants':('variants.py',()), 'offers':('shopping.py',()),
    'cart':('ali_cart.py',()), 'apply':('cart_plan.py',()),
    'check':('check.py',()),
}

def main():
    if len(sys.argv)<2 or sys.argv[1] in ('-h','--help'):
        print('Usage: python3 ali.py COMMAND [arguments]\n\n'+
              'Read:   search, search-bom, product, variants, images, store, quote\n'+
              'Setup:  session, destination\n'+
              'Review: offers collect|enrich|compare|plan\n'+
              'Cart:   cart list|get|skus|add|update|delete, apply\n'+
              'Tests:  check\n\nUse COMMAND --help for arguments. Output files are required.'); return 0
    if sys.argv[1] not in ROUTES:
        print('Unknown command; use --help',file=sys.stderr);return 2
    script,prefix=ROUTES[sys.argv[1]]
    import importlib.util
    if importlib.util.find_spec('patchright') is None:
        runtime = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'ozon-bom/venv/bin/python'
        if runtime.is_file():
            os.execv(str(runtime), [str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])
    os.execv(sys.executable,[sys.executable,str(Path(__file__).with_name(script)),*prefix,*sys.argv[2:]])

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    sys.exit(main())
