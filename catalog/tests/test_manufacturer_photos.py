import json
from django.test import SimpleTestCase
from catalog.manufacturer_photos import valid_page,matching_manufacturer_image,discovered_page


class ManufacturerPhotoTests(SimpleTestCase):
    def test_febest_public_lookup_requires_exact_heading_brand_and_photo(self):
        url=discovered_page('FEBEST','0101-ACA30F')
        self.assertEqual(url,'https://febest.com.ua/catalogue/?product=0101-ACA30F')
        photo='https://febest.com.ua/newfolder/product_image/0101-ACA30F.jpg?v=123'
        html='<h1>0101-ACA30F</h1><img class="official-logo official-logo-eu" alt="FEBEST" src="/logo.png"><img class="zoomable" alt="0101-ACA30F" src="/newfolder/schemes/0101-ACA30F.png"><img class="zoomable" alt="0101-ACA30F" src="'+photo+'">'
        self.assertEqual(matching_manufacturer_image(html,url,'FEBEST','0101-ACA30F'),photo)
        self.assertEqual(matching_manufacturer_image(html,url,'OTHER','0101-ACA30F'),'')
        self.assertEqual(matching_manufacturer_image(html,url,'FEBEST','0101-ACA30R'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('<h1>0101-ACA30F','<h1>0101-ACA30R'),url,'FEBEST','0101-ACA30F'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('/product_image/','/schemes/'),url,'FEBEST','0101-ACA30F'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('v=123','token=123'),url,'FEBEST','0101-ACA30F'),'')
        self.assertEqual(discovered_page('OTHER','0101-ACA30F'),'')
        self.assertEqual(discovered_page('FEBEST','../secret'),'')

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

    def test_autodoc_mando_requires_exact_jsonld_identity_and_first_product_image(self):
        url='https://www.autodoc.co.uk/mando/14579627'
        image='https://cdn.autodoc.de/thumb?id=14579627&m=0&n=0&lng=en&rev=94078121'
        data={
            '@type':'Product','name':'Mando EX54661A7100 Shock absorber for KIA CERATO',
            'sku':'EX54661A7100','mpn':'EX54661A7100','brand':{'name':'Mando'},
            'additionalProperty':[
                {'name':'Item number','value':'EX54661A7100'},
                {'name':'Manufacturer','value':'Mando'},
            ],
            'image':[image,'https://cdn.autodoc.de/thumb?id=14579627&m=0&n=1&lng=en&rev=94078121'],
        }
        def markup():return '<script type="application/ld+json">'+json.dumps(data)+'</script>'
        self.assertEqual(matching_manufacturer_image(markup(),url,'Mando','EX54661A7100'),image)
        self.assertEqual(matching_manufacturer_image(markup(),url,'OTHER','EX54661A7100'),'')
        self.assertEqual(matching_manufacturer_image(markup(),url,'Mando','EX54661A7200'),'')
        data['image'][0]=image.replace('n=0','n=1')
        self.assertEqual(matching_manufacturer_image(markup(),url,'Mando','EX54661A7100'),'')

    def test_evocar_mando_requires_exact_page_execution_and_product_photo(self):
        url='https://evocar.ua/ru/mando/ex54661a7100'
        image='https://cdn.evocar.ua/786ef0b604d0b4428b926fc1db1a4185/mando-ex54661a7100.jpg'
        html=(
            '<h1>Амортизатор Mando EX54661A7100</h1>'
            '<div>Сторона установки Передний мост справа</div>'
            '<div>Парные номера артикулов EX54651A7100</div>'
            '<img alt="Mando EX54661A7100" src="'+image+'">'
        )
        self.assertEqual(matching_manufacturer_image(html,url,'Mando','EX54661A7100'),image)
        self.assertEqual(matching_manufacturer_image(html,url,'Mando','EX54661A7200'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('справа','слева'),url,'Mando','EX54661A7100'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('cdn.evocar.ua','evil.example'),url,'Mando','EX54661A7100'),'')

    def test_ebay_ssangyong_requires_exact_article_single_unit_and_main_photo(self):
        url='https://www.ebay.com/itm/157310193004'
        image='https://i.ebayimg.com/images/g/XLIAAeSwdGFowS-W/s-l1600.webp'
        html=(
            '<h1>Genuine Rear Gas Shock Absorber 4530132100 for Ssangyong Korando Sports</h1>'
            '<div>Buy 1$159.00/ea</div>'
            '<img alt="4530132100" src="'+image+'">'
        )
        self.assertEqual(matching_manufacturer_image(html,url,'SsangYong','4530132100'),image)
        self.assertEqual(matching_manufacturer_image(html,url,'SsangYong','4530132200'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('Buy 1','Buy 2'),url,'SsangYong','4530132100'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('s-l1600','s-l500'),url,'SsangYong','4530132100'),'')

    def test_gmarket_ssangyong_requires_exact_article_single_unit_and_main_photo(self):
        url='https://item.gmarket.co.kr/Item?goodsCode=4348280358'
        image='https://gdimg.gmarket.co.kr/4348280358/still/600?ver=1741740426'
        html=(
            '<h1>쌍용/엑티언 스포츠/자동차 뒷쇽업쇼바/GAS타입/45301-32100/1개당</h1>'
            '<img alt="45301-32100" src="'+image+'">'
        )
        self.assertEqual(matching_manufacturer_image(html,url,'SsangYong','4530132100'),image)
        self.assertEqual(matching_manufacturer_image(html,url,'SsangYong','4530132200'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('/1개당','/2개'),url,'SsangYong','4530132100'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('/still/600','/still/80'),url,'SsangYong','4530132100'),'')

    def test_kaspi_jashi_requires_exact_card_identity_and_main_photo(self):
        url='https://kaspi.kz/shop/p/jashi-perednii-c281f260202-0200-1-sht-130050225/'
        image='https://resources.cdn-kaspi.kz/img/m/p/p62/p7d/8356815.png?format=gallery-large'
        html=(
            '<h1>Амортизатор JASHI передний C281F260202-0200 1 шт</h1>'
            '<div>Код товара: 130050225</div>'
            '<meta property="og:image" content="'+image+'">'
        )
        self.assertEqual(matching_manufacturer_image(html,url,'JASHI','C281F260202-0200'),image)
        self.assertEqual(matching_manufacturer_image(html,url,'JASHI','C281F260202-0400'),'')
        self.assertEqual(matching_manufacturer_image(html,url,'OTHER','C281F260202-0200'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('130050225','130050226'),url,'JASHI','C281F260202-0200'),'')
        self.assertEqual(matching_manufacturer_image(html.replace('gallery-large','gallery-medium'),url,'JASHI','C281F260202-0200'),'')

    def test_unapproved_pages_are_rejected(self):
        for url in ['http://aywiparts.ru/product/view/7','https://aywiparts.ru.evil.example/product/view/7','https://aywiparts.ru/product/view/7?redirect=1','https://[',None]:
            self.assertFalse(valid_page(url,'AYWIPARTS'))
