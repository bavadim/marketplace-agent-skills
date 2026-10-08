"""Synthetic regression: 120 W / 3000 K must not stand in for a 320 W kit."""
import copy
import datetime as dt
import unittest

import bom
import shopping
import variants
from test_shopping import offer, choose


def catalog():
    return {"kind":"skus", "status":"ok", "product_id":"10001", "source_id":0,
        "observed_at":dt.datetime.now(dt.timezone.utc).isoformat(),
        "source":{"url":"https://aliexpress.ru/aer-api/v3/bx/pdp/webProductToCart?productId=10001", "response_sha256":"a"*64},
        "skus":[{"sku_id":sid,"sku_attr":"synthetic","available_quantity":stock,
                 "options":[{"name":"Power","value":power},{"name":"Spectrum","value":color},{"name":"Plug","value":"EU"}],
                 "price":{"value":price,"currency":"RUB"}}
                for sid,power,color,price,stock in [
                    ("20001","120W","3000K","7000",10),
                    ("20002","320W","3500K","17000",4),
                    ("20003","320W","4000K","18000",3),
                    ("20004","320W","3000K","16000",0)]]}


class VariantTests(unittest.TestCase):
    def test_full_product_is_accepted_without_manual_catalog_extraction(self):
        result=variants.matrix({'kind':'product','status':'needs_review','catalog':catalog()}, {'Power':'320W'})
        self.assertEqual(result['matching_sku_ids'],['20002','20003'])

    def test_all_actual_combinations_listed_not_cartesian_product(self):
        result=variants.matrix(catalog())
        self.assertEqual(len(result["variants"]),4)
        self.assertEqual(result["dimensions"]["Power"],["120W","320W"])
        self.assertEqual(result["status"],"needs_choice")
        self.assertIsNone(result["selected_sku_id"])

    def test_exact_power_and_spectrum_do_not_pick_cheapest_120w(self):
        result=variants.matrix(catalog(),{"Power":"320W","Spectrum":"3500K","Plug":"EU"})
        self.assertEqual(result["matching_sku_ids"],["20002"])
        row=next(r for r in result["variants"] if r["sku_id"]=="20002")
        self.assertEqual(row["price"]["value"],"17000")
        self.assertEqual(row["url"],"https://aliexpress.ru/item/10001.html?sku_id=20002")
        self.assertFalse(row["technical_suitability_verified"])

    def test_partial_options_still_require_choice_and_unknown_dimension_fails(self):
        result=variants.matrix(catalog(),{"Power":"320W"})
        self.assertEqual(result["matching_sku_ids"],["20002","20003"])
        self.assertEqual(result["status"],"needs_choice")
        self.assertEqual(variants.matrix(catalog(),{"Power":"320W","Spectrum":"3000K"})["status"],"no_match")
        with self.assertRaises(ValueError): variants.matrix(catalog(),{"Wrong option":"320W"})
        with self.assertRaises(ValueError): variants.matrix({"status":"ok","kind":"search","products":[]})

    def test_rebind_discards_old_sku_specs_contents_and_delivery(self):
        old=offer();old["product"]["facts"]["actual_input_w"]={"value":120,"verified":True,"evidence_ids":["fixture"]}
        result=shopping.bind([old],old["offer_id"],catalog(),"20002")[0]["product"]
        self.assertEqual(result["selected_url"],"https://aliexpress.ru/item/10001.html?sku_id=20002")
        self.assertEqual(bom.fact(result,"price_per_pack"),"17000")
        for field in ("actual_input_w","delivery","package_contents","matches_request"):
            self.assertNotIn(field,result["facts"])
        self.assertEqual(result["status"],"needs_review")
        self.assertTrue(result["unresolved"])

    def test_clone_supports_multiple_skus_in_one_listing(self):
        old=offer()
        result=shopping.bind([old],old["offer_id"],catalog(),"20002",new_offer_id="R1-320w")
        self.assertEqual(result[0],old)
        self.assertEqual(result[1]["offer_id"],"R1-320w")
        self.assertEqual(bom.fact(result[1]["product"],"sku_id"),"20002")

    def test_final_plan_links_selected_sku(self):
        line=choose()["lines"][0]
        self.assertEqual(line["url"],f"https://aliexpress.ru/item/{line['product_id']}.html?sku_id={line['sku_id']}")

    def test_variant_image_remains_bound_to_the_matching_sku(self):
        data=catalog()
        data['skus'][0]['options'][0]['image_url']='https://ae-pic-a1.aliexpress-media.com/kf/120w.jpg'
        data['skus'][1]['options'][0]['image_url']='https://ae-pic-a1.aliexpress-media.com/kf/320w.jpg'
        result=variants.matrix(data,{'Power':'320W','Spectrum':'3500K'})
        self.assertEqual(len(result['images']),1)
        self.assertEqual(result['images'][0]['sku_ids'],['20002'])
        self.assertTrue(result['images'][0]['url'].endswith('/320w.jpg'))


if __name__=="__main__": unittest.main()
