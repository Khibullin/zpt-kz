"""Real Chromium layout checks, opt-in in CI; synthetic rendered pages only."""
import os
import re
from django.contrib.staticfiles import finders
from pathlib import Path
import subprocess
import tempfile
from unittest import skipUnless
from django.test import TestCase, override_settings
from django.urls import reverse
from django.template.loader import render_to_string
from types import SimpleNamespace
from core.services.request_connections import participate
from core.models import Seller, SellerSocialLink, SellerWorkInterval
from core.tests.test_approved_workflow import ApprovedWorkflowTests


@skipUnless(os.environ.get('ZPT_BROWSER_QA') == '1', 'Chromium QA runs in the browser CI job')
@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'])
class ApprovedBrowserTests(TestCase):
    setUp = ApprovedWorkflowTests.setUp
    buyer = ApprovedWorkflowTests.buyer

    def test_desktop_mobile_layouts(self):
        pages = {}
        self.req.transport_type, self.req.year = 'car', 2015
        self.req.model = 'E 400 4MATIC'
        self.req.description = self.req.public_summary = 'Передние наружные ШРУСы, левый и правый'
        self.req.save(update_fields=['transport_type', 'year', 'model', 'description', 'public_summary'])
        shop = Seller.objects.create(name='Магазин «Пример 1»', whatsapp='77009998877', city='Алматы', brand='Mercedes-Benz', address='Тестовый адрес магазина', directory_description='Новые запчасти для легковых автомобилей. Наличие, производителя и условия покупки уточняйте у продавца.',
            sells_new_parts=True, all_brands=True, all_categories=True, transport_type='car', catalog_only=True)
        shop.latitude, shop.longitude = '43.238949', '76.889709'
        shop.save(update_fields=['latitude', 'longitude'])
        SellerSocialLink.objects.create(seller=shop, kind='telegram', url='https://t.me/zpt_test_shop')
        SellerWorkInterval.objects.create(seller=shop, weekday=0, opens='09:00', closes='18:00')
        pages['buyer-directory-shop'] = self.client.get(reverse('workflow_buyer_shop', args=[self.req.pk, self.req.access_token, shop.pk])).content.decode()
        shop.whatsapp, shop.phone2 = '77271234567', '77009998877'
        shop.save(update_fields=['whatsapp', 'phone2'])
        pages['buyer-shop-city-phone'] = self.client.get(reverse('workflow_buyer_shop', args=[self.req.pk, self.req.access_token, shop.pk])).content.decode()
        from core.phone_utils import seller_whatsapp_phone
        self.assertEqual(seller_whatsapp_phone(shop), '77009998877')
        shop.whatsapp, shop.phone2 = '77009998877', ''
        shop.save(update_fields=['whatsapp', 'phone2'])
        for step in ('accepted', 'sellers', 'edit', 'result', 'search', 'close', 'done'):
            pages['buyer-' + step] = self.client.get(self.buyer(step)).content.decode()
        original_name = self.seller.name
        self.seller.name = 'Тойота Центр Жетысу | Официальный дилер — магазин запчастей и сервисный центр с длинным названием'
        self.seller.save(update_fields=['name'])
        pages['buyer-sellers-long-name'] = self.client.get(self.buyer('sellers')).content.decode()
        pages['buyer-shop-long-name'] = self.client.get(reverse('workflow_buyer_shop', args=[self.req.pk, self.req.access_token, self.seller.pk])).content.decode()
        self.seller.name = original_name
        self.seller.save(update_fields=['name'])
        pages['connect'] = self.client.get(reverse('workflow_connect')).content.decode()
        pages['claim'] = self.client.get(reverse('workflow_claim')).content.decode()
        from core.workflow_forms import ClaimPhoneForm
        pages['claim-found'] = render_to_string('core/connections/claim.html', {'form': ClaimPhoneForm(), 'candidates': [{'pk': shop.pk, 'name': shop.name, 'city': shop.city}], 'source': 'site'})
        from core.models import Country as CoreCountry, Brand as CoreBrand, PartCategory
        country = CoreCountry.objects.create(name='Япония QA')
        CoreBrand.objects.create(country=country, name='Toyota QA')
        PartCategory.objects.create(name='Фильтры QA')
        pages['new-shop'] = self.client.get(reverse('workflow_claim') + '?new=1').content.decode()
        for state in ('claim_status', 'claim_confirm', 'access_ready', 'expired', 'closed'):
            pages[state] = render_to_string('core/connections/' + state + '.html', {'claim': SimpleNamespace(seller=shop, get_status_display='Ожидает проверки'), 'shop_name': shop.name, 'city': shop.city})
        participate(self.req, self.seller)
        self.req.purchase_result = 'purchased'
        self.req.save(update_fields=['purchase_result'])
        pages['buyer-purchased'] = self.client.get(self.buyer('done')).content.decode()
        pages['buyer-seller'] = self.client.get(self.buyer('seller')).content.decode()
        from core.services.request_connections import record
        record(self.req, self.seller, 'buyer', 'purchase_confirmed')
        pages['buyer-review'] = self.client.get(self.buyer('review')).content.decode()
        self.req.purchase_result = ''
        self.req.save(update_fields=['purchase_result'])
        self.client.force_login(self.user)
        participate(self.req, self.seller)
        for name, args in [('connection_feed', []), ('connection_detail', [self.req.pk]),
                           ('workflow_seller_contact_page', [self.req.pk]), ('workflow_seller_result', [self.req.pk]),
                           ('connection_quality', []), ('workflow_profile_edit', []), ('workflow_channels', []),
                           ('workflow_telegram_connect', [])]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)
            pages[name] = response.content.decode()
        pages['telegram-prepared'] = self.client.post(reverse('workflow_telegram_connect'), follow=True).content.decode()
        from core.models import SellerChannelConnection
        SellerChannelConnection.objects.filter(seller=self.seller).update(telegram_id=900001)
        pages['telegram-connected'] = self.client.get(reverse('workflow_telegram_connect')).content.decode()
        self.client.force_login(self.staff)
        for name, args in [('workflow_admin_list', []), ('workflow_admin_control', [self.req.pk]),
                           ('workflow_admin_reconcile', [self.req.pk]), ('workflow_admin_activity', [self.seller.pk]),
                           ('workflow_admin_buyer_followup', [self.req.pk])]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)
            pages[name] = response.content.decode()
        from core.models import TelegramRequestPublication, SellerAccessClaim
        claim = SellerAccessClaim.objects.create(seller=shop, source='site')
        pages['admin-access-review'] = self.client.get(reverse('admin:core_selleraccessclaim_change', args=[claim.pk])).content.decode()
        pages['admin-telegram-prepare'] = self.client.get(reverse('admin:core_request_telegram', args=[self.req.pk])).content.decode()
        publication = TelegramRequestPublication.objects.create(request=self.req, text=self.req.public_summary, created_by=self.staff)
        for state in ('draft', 'published', 'unknown'):
            publication.status = state
            publication.message_id = 123 if state == 'published' else None
            publication.save()
            pages['admin-telegram-' + state] = self.client.get(reverse('admin:core_telegramrequestpublication_change', args=[publication.pk])).content.decode()
        from core.models import SellerChannelConnection
        SellerChannelConnection.objects.update_or_create(seller=self.seller, defaults={'whatsapp_consent': True})
        pages['admin-manual-prepared'] = self.client.post(reverse('workflow_admin_manual', args=[self.req.pk]), {'seller': self.seller.pk, 'channel': 'whatsapp', 'action': 'prepare'}).content.decode()
        self.assertIn('Скопировать сообщение', pages['admin-manual-prepared'])
        pages['admin-history'] = self.client.get(reverse('workflow_admin_control', args=[self.req.pk]) + '?view=history').content.decode()
        # Exercise the real public card families with their own stylesheet cascade.
        self.client.logout()
        from catalog.models import Product, Country, Brand, CarModel, MaintenanceKit, MaintenanceKitItem
        from service_requests.models import ServiceSeller
        product = Product.objects.create(title='Воздушный фильтр — крупное читаемое название',
            article='QA-FILTER', price=12500, status='active', stock_qty=5,
            seller_name='Магазин запчастей', whatsapp_number='+77009998877')
        country = Country.objects.create(name='QA страна')
        brand = Brand.objects.create(country=country, name='Toyota QA')
        model = CarModel.objects.create(brand=brand, name='Highlander QA')
        kit = MaintenanceKit.objects.create(name='Комплект ТО Toyota Highlander', slug='qa-readable-kit',
            brand=brand, car_model=model, is_active=True)
        MaintenanceKitItem.objects.create(kit=kit, product=product, quantity=1)
        shop.is_dismantler = True
        shop.save(update_fields=['is_dismantler'])
        sto = ServiceSeller.objects.create(name='Автосервис — проверка крупного текста',
            whatsapp='77005554433', seller_type='sto', city='Алматы', address='Тестовая улица, 1')
        for name, url in {
            'public-home': '/', 'public-seller-registration': reverse('seller_landing'),
            'public-buyer-entry': reverse('interface_find_part'), 'public-products': reverse('catalog_list'),
            'public-product': product.get_absolute_url(),
            'public-dismantlers': '/auto-dismantlers/',
            'public-dismantler': f'/parts-seller/{shop.pk}/',
            'public-parts-sellers': '/parts-sellers/',
            'public-services': '/catalog/services/?type=sto',
            'public-service': f'/catalog/services/{sto.pk}/',
            'public-kits': reverse('maintenance_kit_list'),
            'public-kit': reverse('maintenance_kit_detail', args=[kit.slug]),
        }.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, name)
            pages[name] = response.content.decode()
        css = Path('core/static/core/connections.css').read_text()
        helpers = Path('core/static/core/connections.js').read_text()
        output = Path('var/reports/workflow-ui').resolve()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            for name, html in pages.items():
                self.assertNotIn('Traceback', html)
                self.assertIn('<body' if name.startswith('public-') else '<main', html, name)
                def inline_css(match):
                    url = match.group(1).split('?')[0]
                    asset = finders.find(url.removeprefix('/static/')) if url.startswith('/static/') else None
                    return '<style>' + Path(asset).read_text() + '</style>' if asset else match.group(0)
                html = re.sub(r'<link[^>]*href="([^\"]+\.css(?:\?[^\"]*)?)"[^>]*>', inline_css, html)
                # Admin pages retain their existing synthetic workflow fallback.
                fallback = '' if 'card-refresh.css' in pages[name] else '<style>' + css + '</style>'
                Path(folder, name + '.html').write_text(html.replace('</head>', fallback + '<script>' + helpers + '</script></head>'))
            script = r'''
const {chromium}=require('playwright');
const fs=require('fs'); const path=require('path');
(async()=>{ const browser=await chromium.launch({headless:true, executablePath:process.env.ZPT_BROWSER_EXECUTABLE || undefined, channel:process.env.ZPT_BROWSER_CHANNEL || undefined});
for(const width of [360,390,1440]) { const page=await browser.newPage({viewport:{width,height:1000}});
await page.route('**/*',route=>route.abort());
for(const file of fs.readdirSync(process.argv[1])) {
await page.setContent(fs.readFileSync(path.join(process.argv[1],file),'utf8'));
if(file==='claim.html' || file==='new-shop.html') {
const phone=page.locator('[data-kz-phone]');
await phone.focus();
if(await phone.inputValue()!=='+7') throw new Error('Phone prefix is missing');
await phone.pressSequentially('7771234567');
if(await phone.inputValue()!=='+7 (777) 123-45-67') throw new Error('Phone typing format');
await phone.pressSequentially('9');
if(await phone.inputValue()!=='+7 (777) 123-45-67') throw new Error('Extra typed digit was not blocked');
if(!await page.locator('[data-phone-warning]').isVisible()) throw new Error('Extra digit warning missing');
for(const value of ['87771234567','7771234567','+7 (777) 123-45-67','77771234567']) {
await phone.fill(value);
if(await phone.inputValue()!=='+7 (777) 123-45-67') throw new Error('Phone paste format: '+value);
if(!await phone.evaluate(el=>el.checkValidity())) throw new Error('Full phone rejected');
}
await phone.press('End');
await phone.press('Backspace');
if(await phone.inputValue()!=='+7 (777) 123-45-6') throw new Error('Phone backspace');
if(await phone.evaluate(el=>el.checkValidity())) throw new Error('Incomplete phone accepted');
await phone.fill('+7 (777) 123-45-67');
await phone.evaluate(el=>el.setSelectionRange(4,7));
await page.keyboard.insertText('701');
if(await phone.inputValue()!=='+7 (701) 123-45-67') throw new Error('Phone middle editing');
await phone.fill('777123456789');
if(await phone.evaluate(el=>el.checkValidity())) throw new Error('Overlong phone silently truncated');
if(!await page.locator('[data-phone-warning]').isVisible()) throw new Error('Overlong paste warning missing');
await phone.fill('7771234567');
}
if(file==='new-shop.html') {
await page.locator('.field-brand summary').click();
const brand=page.locator('input[name=brand]').first();
await brand.check();
if(!await brand.isChecked()) throw new Error('Shop brand is not selectable');
await page.locator('.field-category summary').click();
const category=page.locator('input[name=category]').first();
await category.check();
if(!await category.isChecked()) throw new Error('Shop category is not selectable');
}
if(file==='buyer-edit.html') {
await page.locator('input[type=file]').setInputFiles({name:'test-photo.png',mimeType:'image/png',buffer:Buffer.from('synthetic filename check')});
if(await page.locator('.upload-name').textContent()!=='test-photo.png') throw new Error('Selected photo filename not visible');
}
if(file==='buyer-sellers-long-name.html') {
const title=page.locator('.seller-copy h2').first();
if(await title.evaluate(el=>el.getBoundingClientRect().height>parseFloat(getComputedStyle(el).lineHeight)*2+1)) throw new Error('Seller title exceeds two lines');
}
if(file==='buyer-shop-city-phone.html') {
const phone=page.locator('.shop-phone').first();
if(await phone.evaluate(el=>parseFloat(getComputedStyle(el).fontSize)<24)) throw new Error('Shop phone is too small');
if((await page.locator('body').innerText()).includes('Специализацию уточните')) throw new Error('Unapproved specialization placeholder');
}
if(file==='public-home.html' && width<540) {
const card=page.locator('#interface-products>.product').first();
const grid=page.locator('#interface-products');
if((await card.boundingBox()).width<(await grid.boundingBox()).width-2) throw new Error('Mobile product card is not full width');
}
if(file==='public-dismantler.html') {
if(await page.locator('.org-phones a').first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize)<22)) throw new Error('Directory phone is too small');
}
if(file==='public-home.html') {
const seller=page.locator('.card-seller-info').first();
if(await seller.count() && (await seller.boundingBox()).width<100) throw new Error('Seller copy stayed in the removed icon column');
}
for(const icon of await page.locator('svg[aria-hidden="true"]:not(.art):not(.service-art):not(.dismantle-company-art)').all()) {
if(await icon.isVisible()) throw new Error('Decorative icon is still visible: '+file);
}
if(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1)) throw new Error('Horizontal overflow: '+file+' width '+width);
if(file==='admin-manual-prepared.html') {
await page.getByRole('button',{name:'Скопировать сообщение'}).click();
if(await page.locator('input[name=confirmed]').isChecked()) throw new Error('Copy marked message as sent');
}
if(file==='admin-telegram-prepare.html') {
const originalText=await page.locator('#id_text').inputValue();
await page.locator('#id_text').fill('Проверка предпросмотра');
if(await page.locator('#telegram-preview-text').textContent()!=='Проверка предпросмотра') throw new Error('Preview did not update');
await page.locator('#id_text').fill(originalText);
}
await page.screenshot({path:path.join(process.argv[2],width+'-'+file+'.png'),fullPage:true});
} await page.close(); } await browser.close(); })().catch(e=>{console.error(e);process.exit(1)});
'''
            result = subprocess.run(['node', '-e', script, folder, str(output)], capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
