import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ali_http as http
import pdp
import shopping
import bom

PID='123456789'; SKU='901'; URL=f'https://aliexpress.ru/item/{PID}.html'
IMG='https://ae-pic-a1.aliexpress-media.com/kf/test.jpg'
GEO={'status':'ok','destination_key':'City','city':'City','city_code':'12','region_code':'10','country':'RU'}

def data():
    price={'value':120,'currency':'RUB','formatted':'120 ₽'}
    sku={'skuId':SKU,'skuAttr':'1:2','skuPropIds':'2','amount':{**price,'value':200},'activityAmount':price,
         'availQuantity':3,'inCartQuantity':0,'sendGoodsCountryCode':'','freightExt':'{"p1":"99"}',
         'logisticAmount':{'value':99,'currency':'USD'},'buyerPriceForLogistic':'12000'}
    return {'id':PID,'sourceId':'0','activeSkuId':SKU,'name':'Example','price':{'activity':True},
            'gallery':[IMG],'skuInfo':{'propertyList':[{'id':'1','name':'Power','values':[{'id':'2','name':'240W','displayName':'240 W','imageMainUrl':IMG}]}],'priceList':[sku]},
            'delivery':{'displayMultipleMethods':False,'to':{'city':'other'}},'analytics':{'private':'SECRET'}}

def page():
    widgets=[{'widgetId':'bx/GlowAsyncProductContextWidget/0.14.0','props':{'sourceId':'0'}},
        {'widgetId':'bx/SnowProductContent/0.8.0','uuid':'desc-1','asyncType':'IMMEDIATELY'},
        {'widgetId':'bx/HazeProductCharacteristics/0.5.1','uuid':'spec-1','asyncType':'IMMEDIATELY'},
        {'widgetId':'bx/RedReviewsProductFeedbackList/0.35.0','uuid':'review-1','asyncType':'IMMEDIATELY'},
        {'widgetId':'bx/ShipToHeaderItem/0.33.0','props':{'userGeoInfo':{'country':{'code':'RU'},'region':{'code':'10','name':'Region'},'city':{'code':'12','name':'City'},'street':'SECRET'}}}]
    return http.PageData('<script id="__AER_DATA__">'+json.dumps({'widgets':widgets})+'</script>')

def widgets():
    return {'widgets':[
        {'name':'SnowProductContent','uuid':'desc-1','state':{'data':{'html':'<p>240 W with driver</p><img src="'+IMG.replace('https:','http:')+'"><script>SECRET</script>'}}},
        {'name':'HazeProductCharacteristics','uuid':'spec-1','state':{'data':{'groups':[{'properties':[{'name':'Dimming','value':'Yes'}]}]}}},
        {'name':'RedReviewsProductFeedbackList','uuid':'review-1','state':{'data':{'reviews':[{'reviewer':{'name':'SECRET'},'root':{'date':'2026-01-01','grade':5,'text':'Complete kit'},'analytics':'SECRET'}]}}},
    ]}

def freight(city='12', methods=True):
    day=(dt.date.today()+dt.timedelta(days=10)).isoformat()
    return {'to':{'country':'RU','region':'10','city':city},'methods':[{'groupName':'Pickup','serviceName':'standard','amount':{'value':50,'currency':'RUB'},'etaStartDeliveryDate':day,'etaEndDeliveryDate':day}] if methods else []}

class Client:
    def __init__(self, responses):self.responses=list(responses);self.calls=[]
    def fetch(self,url,payload=None,**kwargs):
        self.calls.append((url,payload,kwargs)); return json.dumps(self.responses.pop(0)).encode(),{}


class ProductTests(unittest.TestCase):
    def product(self):
        c=Client([{'data':data()},widgets()])
        card={'product_id':PID,'url':URL,'facts':{},'evidence':[],'images':[],'observed_at':http.now()}
        return pdp.read_product(c,URL+'?sku_id='+SKU,page(),card),c

    def test_async_product_reads_all_layers_without_leaking_account_fields(self):
        product,c=self.product()
        self.assertEqual(product['detail_status'],'ok')
        self.assertEqual(product['catalog']['skus'][0]['price']['value'],'120')
        self.assertEqual(product['description']['text'],'240 W with driver')
        self.assertEqual(product['attributes'],[{'name':'Dimming','value':'Yes'}])
        self.assertEqual(product['reviews']['items'][0]['text'],'Complete kit')
        self.assertNotIn('SECRET',json.dumps(product))
        self.assertEqual(len(product['images']),1)
        self.assertIn('description',product['images'][0]['roles'])
        self.assertEqual(c.calls[1][2]['aer_url'],URL+'?sku_id='+SKU)
        self.assertEqual(product['facts'],{})

    def test_requested_sku_mismatch_stops_before_widget_calls(self):
        value=data();value['activeSkuId']='902'
        with self.assertRaises(http.ReadFailure) as e:pdp.product_data(Client([{'data':value}]),PID,SKU)
        self.assertEqual(e.exception.status,'identity_mismatch')

    def test_non_discounted_sku_uses_regular_price(self):
        value=data();value['price']['activity']=False
        self.assertEqual(pdp.catalog(value,{'observed_at':http.now()})['skus'][0]['price']['value'],'200')

    def test_missing_widget_is_partial_not_complete(self):
        c=Client([{'data':data()},{'widgets':[]}])
        result=pdp.read_product(c,URL,page(),{'product_id':PID,'url':URL})
        self.assertEqual(result['detail_status'],'partial')
        self.assertIn('SnowProductContent',result['detail_missing'])
        broken=widgets();broken['widgets'][0]['state']['data']={}
        result=pdp.read_product(Client([{'data':data()},broken]),URL,page(),{'product_id':PID,'url':URL})
        self.assertIn('SnowProductContent',result['detail_missing'])

    def test_quote_uses_selected_sku_quantity_and_checked_destination(self):
        product,_=self.product();c=Client([{'data':data()},freight()])
        result=pdp.quote(c,product,SKU,2,GEO)
        self.assertEqual(c.calls[1][1]['count'],2)
        self.assertEqual(c.calls[1][1]['skuId'],SKU)
        self.assertEqual(result['options'][0]['cost'],'50') # NOT logisticAmount 99 USD
        self.assertIsNone(result['options'][0]['is_default'])
        self.assertFalse(result['options'][0]['checkout_selected'])

    def test_wrong_city_and_empty_methods_are_not_free_shipping(self):
        product,_=self.product()
        with self.assertRaises(http.ReadFailure) as e:pdp.quote(Client([{'data':data()},freight('99')]),product,SKU,1,GEO)
        self.assertEqual(e.exception.status,'destination_mismatch')
        result=pdp.quote(Client([{'data':data()},freight(methods=False)]),product,SKU,1,GEO)
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['options'],[])

    def test_unobserved_or_ambiguous_city_requires_selection(self):
        product,_=self.product()
        self.assertEqual(pdp.choose_destination(product,'CITY')['city_code'],'12')
        with self.assertRaises(http.ReadFailure):pdp.choose_destination(product,'Unknown')
        product['destination_choices'].append({**product['destination_choices'][0],'city_code':'13'})
        with self.assertRaises(http.ReadFailure):pdp.choose_destination(product,'City')

    def test_collect_imports_sources_but_never_claims_technical_review(self):
        product,_=self.product()
        quote=pdp.quote(Client([{'data':data()},freight()]),product,SKU,1,GEO)
        result=shopping.collect(product,'R1',SKU,quote)
        card=result[0]['product']
        self.assertEqual(bom.fact(card,'delivery')['cost'],'50')
        self.assertEqual(card['status'],'needs_review')
        self.assertNotIn('matches_request',card['facts'])
        for index in (-1,10):
            with self.assertRaises(ValueError):shopping.collect(product,'R1',SKU,quote,shipping_index=index)
        quote['price']['value']='121'
        with self.assertRaises(ValueError):shopping.collect(product,'R1',SKU,quote)

    def test_image_cache_reuses_download_and_tiles_but_preserves_variant_attribution(self):
        card={'product_id':PID,'images':[{'url':IMG.replace('https:','http:'),'role':'description','sku_ids':[SKU]}]}
        def prepare(path,out,language=None):
            return {'source':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'tiles':[]}
        c=Client([]);c.fetch=lambda *a,**kw:(b'image',{})
        with tempfile.TemporaryDirectory() as tmp,patch('vision.prepare',side_effect=prepare) as render:
            out=Path(tmp)/'images.json'
            first=http.download_images(c,card,None,30,out)
            c.fetch=lambda *a,**kw: self.fail('Cached image fetched twice')
            card['images'][0]['sku_ids']=['902']
            card['images'][0]['role']='sku_option'
            second=http.download_images(c,card,None,30,out)
            self.assertTrue(second['images'][0]['cache_hit'])
            self.assertEqual(second['images'][0]['sku_ids'],['902']);self.assertEqual(render.call_count,1)
            self.assertEqual(second['images'][0]['roles'],['sku_option'])
        self.assertEqual(first['status'],'ok')

    def test_images_reordering_and_broken_cache_do_not_reuse_another_image(self):
        images=[{'url':IMG,'role':'description'},{'url':IMG+'2','role':'description'}]
        card={'product_id':PID,'images':images}
        def prepare(path,out,language=None):
            return {'source':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'tiles':[]}
        c=Client([]);c.fetch=lambda url,**kw:(url.encode(),{})
        with tempfile.TemporaryDirectory() as tmp,patch('vision.prepare',side_effect=prepare):
            out=Path(tmp)/'images.json'
            first=http.download_images(c,card,None,1,out)
            self.assertTrue(first['truncated']);self.assertEqual(first['available_count'],2)
            card['images']=list(reversed(images))
            second=http.download_images(c,card,None,2,out)
            self.assertTrue(second['images'][1]['cache_hit'])
            for image in second['images']:
                self.assertEqual(Path(image['source']).read_text(),image['url'])
            cache=out.parent/(out.stem+'-assets')/(hashlib.sha256(IMG.encode()).hexdigest()+'.json')
            cache.write_text('{broken')
            self.assertEqual(http.download_images(c,card,None,2,out)['status'],'ok')

    def test_collected_product_can_be_reviewed_compared_and_planned_without_manual_fact_wrappers(self):
        product,_=self.product()
        product['seller']={'status':'ok','observed_at':http.now(),
            'seller':{'id':'7','url':'https://aliexpress.ru/store/7','name':'Synthetic seller',
                      'rating_kind':'seller','rating':'96','scale':100,'review_count':None},
            'source':{'url':'https://aliexpress.ru/store/7','sha256':'a'*64}}
        quote=pdp.quote(Client([{'data':data()},freight()]),product,SKU,1,GEO)
        offers=shopping.collect(product,'R1',SKU,quote)
        oid=offers[0]['offer_id']
        packet={'sku_id':SKU,'observed_at':http.now(),'review_complete':True,'unresolved':[],
            'observations':[{'method':'dom','reviewed':True,'url':URL,
                'text':'Synthetic reviewed fixture: 240 W board with driver, one complete kit.',
                'facts':{'pack_size':1,'unit':'pcs','package_contents':['board','driver'],
                         'input_w':240,'matches_request':True}}]}
        offers=shopping.enrich(offers,oid,packet)
        request={'currency':'RUB','destination_key':'City','items':[
            {'id':'R1','quantity':1,'unit':'pcs','required_facts':{'input_w':240}}]}
        result=shopping.compile_plan(request,offers,[{'row_id':'R1','offer_id':oid,'reason':'Reviewed synthetic kit'}])
        self.assertEqual(result['status'],'ready_for_cart',result['blockers'])
        self.assertEqual(result['quoted_total'],'170.00')
        self.assertFalse(result['lines'][0]['delivery']['checkout_selected'])

if __name__=='__main__':unittest.main()
