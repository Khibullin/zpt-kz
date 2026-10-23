"""Real Chromium layout checks, opt-in in CI; synthetic rendered pages only."""
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import skipUnless
from django.test import TestCase, override_settings
from django.urls import reverse
from django.template.loader import render_to_string
from types import SimpleNamespace
from core.services.request_connections import participate
from core.models import Seller
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
        pages['buyer-directory-shop'] = self.client.get(reverse('workflow_buyer_shop', args=[self.req.pk, self.req.access_token, shop.pk])).content.decode()
        for step in ('accepted', 'sellers', 'edit', 'result', 'search', 'close', 'done'):
            pages['buyer-' + step] = self.client.get(self.buyer(step)).content.decode()
        pages['connect'] = self.client.get(reverse('workflow_connect')).content.decode()
        pages['claim'] = self.client.get(reverse('workflow_claim')).content.decode()
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
        self.client.force_login(self.staff)
        for name, args in [('workflow_admin_list', []), ('workflow_admin_control', [self.req.pk]),
                           ('workflow_admin_reconcile', [self.req.pk]), ('workflow_admin_activity', [self.seller.pk]),
                           ('workflow_admin_buyer_followup', [self.req.pk])]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)
            pages[name] = response.content.decode()
        css = Path('core/static/core/connections.css').read_text()
        output = Path('var/reports/workflow-ui').resolve()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            for name, html in pages.items():
                self.assertNotIn('Traceback', html)
                self.assertIn('<main', html, name)
                Path(folder, name + '.html').write_text(html.replace('</head>', '<style>' + css + '</style></head>'))
            script = r'''
const {chromium}=require('playwright');
const fs=require('fs'); const path=require('path');
(async()=>{ const browser=await chromium.launch({headless:true, executablePath:process.env.ZPT_BROWSER_EXECUTABLE || undefined, channel:process.env.ZPT_BROWSER_CHANNEL || undefined});
for(const width of [390,1440]) { const page=await browser.newPage({viewport:{width,height:1000}});
await page.route('**/*',route=>route.abort());
for(const file of fs.readdirSync(process.argv[1])) {
await page.setContent(fs.readFileSync(path.join(process.argv[1],file),'utf8'));
if(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1)) throw new Error('Horizontal overflow: '+file+' width '+width);
await page.screenshot({path:path.join(process.argv[2],width+'-'+file+'.png'),fullPage:true});
} await page.close(); } await browser.close(); })().catch(e=>{console.error(e);process.exit(1)});
'''
            result = subprocess.run(['node', '-e', script, folder, str(output)], capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
