import json
import os
import shutil
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from ozon_data import product,search_products,rubles,canonical,sku_from_url,parse_cart
from ozon_cart import validate_quantity,verify_others,validate_plan,quantity_input
from ozon_browser import background_options

FIXTURES=Path(__file__).parent/'fixtures'

class ShoppingTests(unittest.TestCase):
    def test_cart_price_is_line_total_and_unknown_layout_is_not_empty(self):
        c=parse_cart(json.loads((FIXTURES/'cart.json').read_text()))
        self.assertEqual(c['items'][0]['quantity'],2)
        self.assertEqual(c['items'][0]['line_total']['regular'],25552)
        self.assertEqual(c['items'][0]['line_total']['bank_offer'],22998)
        self.assertFalse(c['empty'])
        with self.assertRaises(ValueError):parse_cart([])
        self.assertTrue(parse_cart([],True)['empty'])

    def test_exact_variant_and_real_payment_conditions(self):
        p=product(json.loads((FIXTURES/'product.json').read_text()))
        self.assertEqual(p['sku'],'2031616091')
        self.assertEqual(p['price']['regular'],12776)
        self.assertEqual(p['price']['bank_offer'],11499)
        self.assertIsNone(p['price']['reference'])
        self.assertEqual(p['selected_options']['Макс. мощность ламп, Вт'],['240'])
        self.assertEqual(len(p['aspects'][0]['variants']),5)
        self.assertEqual(p['seller']['name'],'Moduled')
        self.assertEqual(p['seller']['rating'],4.9)
        self.assertTrue(p['completeness']['characteristics'])
        self.assertEqual(p['delivery']['status'],'not_loaded')
        self.assertIsNone(p['delivery']['cost_rub'])
        self.assertEqual(p['vision_status'],'pending')
        self.assertNotIn('trackingInfo',json.dumps(p))

    def test_search_does_not_use_inflated_crossed_out_price_as_current(self):
        rows=search_products(json.loads((FIXTURES/'search.json').read_text()))
        self.assertEqual(rows[1]['displayed_price_rub'],6930)
        self.assertTrue(rows[1]['price_requires_product_check'])
        self.assertEqual(rows[1]['reference_price_rub'],988999)
        self.assertIn('inflated_reference_price',rows[1]['warnings'])
        self.assertEqual(rows[0]['sku'],'2031616091')
        self.assertNotIn('?at=',rows[0]['url'])
        self.assertNotIn('advert',json.dumps(rows))

    def test_url_and_currency_normalization(self):
        self.assertEqual(rubles('11\u2009499\u2009₽'),11499)
        self.assertEqual(rubles('3 540,50 ₽'),3540.5)
        self.assertIsNone(rubles('от 1 ₽'))
        self.assertIsNone(canonical('https://ozon.ru.evil.test/product/123/'))
        self.assertEqual(sku_from_url('https://www.ozon.ru/product/lamp-12345/?x=y'),'12345')
        self.assertIsNone(sku_from_url('https://www.ozon.ru/cart'))

    def test_native_mode_cannot_inherit_desktop_display(self):
        driver=Mock()
        version=Mock(stdout='Chromium 153.0.8010.47')
        with patch.dict(os.environ,{'DISPLAY':':0','WAYLAND_DISPLAY':'wayland-0','XDG_SESSION_TYPE':'wayland'}), patch('ozon_browser.subprocess.run',return_value=version):
            opts=background_options(driver,'/bin/chromium',mode='native')
            self.assertTrue(opts['headless'])
            self.assertNotIn('DISPLAY',opts['env'])
            self.assertNotIn('WAYLAND_DISPLAY',opts['env'])
            self.assertNotIn('HeadlessChrome',opts['user_agent'])
            with self.assertRaisesRegex(ValueError,'unsupported_browser_mode'):
                background_options(driver,'/bin/chromium',mode='virtual')

    def test_quantity_limits_and_other_items_are_preserved(self):
        row={'minimum':1,'maximum':4,'multiplicity':2}
        validate_quantity(row,2)
        for qty in [0,-1,1,5,True,2.5]:
            with self.assertRaises(ValueError):validate_quantity(row,qty)
        before={'items':[{'sku':'1','quantity':1,'selected':True},{'sku':'2','quantity':3,'selected':False}]}
        after={'items':[{'sku':'2','quantity':3,'selected':False}]}
        verify_others(before,after,'1')
        after['items'][0]['selected']=True
        with self.assertRaises(ValueError):verify_others(before,after,'1')

    def test_plan_refuses_duplicate_or_mismatched_skus_and_unbounded_prices(self):
        item={'sku':'123','url':'https://www.ozon.ru/product/lamp-123/','quantity':2,'max_price':1000}
        self.assertEqual(validate_plan({'items':[item]}),[item])
        with self.assertRaises(ValueError):validate_plan({'items':[item,item]})
        for changed in [{'sku':'124'},{'quantity':0},{'max_price':None},{'max_price':float('nan')},
                        {'url':'https://example.com/product/lamp-123/'}]:
            with self.assertRaises(ValueError):validate_plan({'items':[{**item,**changed}]})

    @unittest.skipUnless(os.environ.get('OZON_BROWSER_SMOKE')=='1','Synthetic cart DOM in real browser')
    def test_cart_controls_refuse_same_quantity_in_wrong_product_row(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as driver:
            binary=shutil.which('chromium') or shutil.which('chromium-browser')
            options={'executable_path':binary} if binary else {'channel':'chromium'}
            browser=driver.chromium.launch(headless=True,**options)
            try:
                page=browser.new_page()
                page.set_content('''<div data-widget="cartSplit">
                    <div><span>Lamp A</span><div><input inputmode="decimal" value="1"></div></div>
                    <div><span>Lamp B</span><div><input inputmode="decimal" value="1"></div></div>
                    </div>''')
                rows=[{'sku':str(i+1),'title':t,'quantity':1,'split_index':0,'item_index':i}
                      for i,t in enumerate(['Lamp A','Lamp B'])]
                self.assertEqual(quantity_input(page,{'items':rows},rows[0]).input_value(),'1')
                with self.assertRaisesRegex(ValueError,'cart_row_identity_mismatch'):
                    quantity_input(page,{'items':rows},{**rows[0],'title':'Lamp B'})
            finally:browser.close()

if __name__=='__main__':unittest.main()
