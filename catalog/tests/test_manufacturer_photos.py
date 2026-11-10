import json
from django.test import SimpleTestCase
from catalog.manufacturer_photos import valid_page,matching_manufacturer_image


class ManufacturerPhotoTests(SimpleTestCase):
    def test_555_requires_catalogue_article_and_matching_product_image_id(self):
        url='https://www.sankei-555.com/search/detail.php?p_id=3&sch_p_555no=SA-1631R'
        image='https://www.sankei-555.com/save_img/products/3/l_01.jpg'
        html='<th>555 No.<span>555 No.</span></th><td>SA-1631R</td><img id="rollover_view" src="'+image+'">'
        self.assertEqual(matching_manufacturer_image(html,url,'555','SA-1631R'),image)
        self.assertEqual(matching_manufacturer_image(html,url,'555','SA-1631L'),'')
        self.assertEqual(matching_manufacturer_image(html,url,'OTHER','SA-1631R'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('/3/l_01','/4/l_01'),url,'555','SA-1631R'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('<td>SA-1631R','<td>SA-1762R'),url,'555','SA-1631R'),'')

    def test_asva_uses_exact_model_and_brand_not_internal_mpn(self):
        url='https://asva-trading.com/catalog/suspension/0120-002'
        image='https://asva-trading.com/sites/default/files/0120-002.jpg'
        data={'@type':'Product','brand':{'name':'ASVA'},'model':'0120-002','mpn':'140933','image':image}
        def markup():return '<script type="application/ld+json">'+json.dumps(data)+'</script>'
        self.assertEqual(matching_manufacturer_image(markup(),url,'ASVA','0120-002'),image)
        self.assertEqual(matching_manufacturer_image(markup(),url,'ASVA','140933'),'')
        data['image']='https://evil.example/sites/default/files/0120-002.jpg'
        self.assertEqual(matching_manufacturer_image(markup(),url,'ASVA','0120-002'),'')
        data['image']='https://asva-trading.com/sites/default/files/no-image.jpg'
        self.assertEqual(matching_manufacturer_image(markup(),url,'ASVA','0120-002'),'')

    def test_aywiparts_matches_specification_and_product_gallery(self):
        url='https://aywiparts.ru/product/view/7'
        html='Артикул:<div class="product__info-specifications-item-value"> AW1310010L</div><a class="product-gallery item active" href="/images/product/AW1310010L/main.jpg"><img src="/images/logo2.jpg"></a>'
        image='https://aywiparts.ru/images/product/AW1310010L/main.jpg'
        self.assertEqual(matching_manufacturer_image(html,url,'AYWIPARTS','AW1310010L'),image)
        self.assertEqual(matching_manufacturer_image(html,url,'AYWIPARTS','AW1310010R'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('/AW1310010L/','/AW1310010R/'),url,'AYWIPARTS','AW1310010L'),'')

    def test_unapproved_pages_are_rejected(self):
        for url in ['http://aywiparts.ru/product/view/7','https://aywiparts.ru.evil.example/product/view/7','https://aywiparts.ru/product/view/7?redirect=1','https://[',None]:
            self.assertFalse(valid_page(url,'AYWIPARTS'))
