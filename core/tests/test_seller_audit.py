from datetime import date
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.template import Context, Template
from django.test import TestCase
from core.models import Seller, Request, Country
from core.services.seller_audit import presentation, validate_sources
from core.services.seller_matching import assess
from core.services.manual_seller_dispatch import validate_manual_recipient
from control_panel.manual_processing import recipient_rows


class SellerAuditTests(TestCase):
    def setUp(self):
        self.req = Request.objects.create(source='home_short', country='Китай',
            brand='Changan', model='CS35', category='Электрика', city='Алматы',
            transport_type='car', search_scope='city', description='Свечи')
        self.seller = Seller.objects.create(name='Проверенный магазин', whatsapp='77010000001',
            city='Алматы', transport_type='car', brand='Changan', category='Электрика',
            all_models=True, sells_new_parts=True, audit_status='linked',
            audit_checked_at=date(2026, 10, 29), audit_public_summary='Телефон совпадает с сайтом.',
            audit_note='Внутренняя заметка: уточнить реквизиты', audit_kaspi='candidate',
            audit_address='Рынок, бутик 1', audit_sources=[
                {'label':'Официальный сайт','url':'https://example.com/','confirmed':True,'public':True},
                {'label':'Кандидат Kaspi','kind':'kaspi','url':'https://kaspi.kz/shop/m/123/products/', 'confirmed':False,'public':False}])

    def render(self, public=True):
        tag = 'seller_audit_public' if public else 'seller_audit_internal'
        return Template('{% load seller_audit %}{% '+tag+' seller %}').render(Context({'seller':self.seller}))

    def test_buyer_sees_only_confirmed_facts(self):
        html = self.render()
        self.assertIn('Телефон совпадает', html)
        self.assertIn('https://example.com/', html)
        self.assertIn('29.10.2026', html)
        self.assertNotIn('Внутренняя заметка', html)
        self.assertNotIn('kaspi.kz', html)
        self.assertIn('наличие', html.lower())
        internal = self.render(False)
        self.assertIn('Внутренняя заметка', internal)
        self.assertIn('kaspi.kz', internal)

    def test_unresolved_conflict_is_private_and_checked_again_on_send(self):
        self.seller.audit_status = 'conflict'
        self.seller.save()
        self.assertEqual(self.render().strip(), '')
        self.assertEqual(assess(self.seller, self.req).status, 'review')
        with self.assertRaises(ValidationError):
            validate_manual_recipient(self.req, self.seller)
        row = recipient_rows(self.req, QueryDict())[0]
        self.assertEqual(row['match_status'], 'review')
        self.assertTrue(row['reason'])
        self.assertIn('Обнаружены расхождения', self.render(False))

    def test_filters_do_not_grant_consent_or_override_specialization(self):
        china = Country.objects.create(name='Китай')
        self.seller.selected_countries.add(china)
        params = QueryDict('country=Китай&audit_status=linked&kaspi=candidate&activity=unknown')
        self.assertEqual([r['seller'].pk for r in recipient_rows(self.req, params)], [self.seller.pk])
        self.assertFalse(recipient_rows(self.req, QueryDict('audit_status=conflict')))
        self.assertFalse(recipient_rows(self.req, QueryDict('country=Япония')))
        self.seller.brand='Toyota'; self.seller.save()
        self.assertFalse(recipient_rows(self.req, params))
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertFalse(self.seller.contact_access_verified)

    def test_unsafe_and_candidate_links_never_leak_even_with_bad_stored_flags(self):
        self.seller.audit_sources = [
            {'label':'bad','url':'javascript:alert(1)','confirmed':True,'public':True},
            {'label':'name-only','url':'https://kaspi.kz/shop/m/123/products/','confirmed':True,'public':True}]
        self.assertFalse(presentation(self.seller)['sources'])
        with self.assertRaises(ValidationError):
            validate_sources([{'label':'bad','url':'https://example.com','public':True,'confirmed':False}])
        with self.assertRaises(ValidationError):
            validate_sources([{'label':'bad','url':'javascript:alert(1)'}])

    def test_freshness_and_kaspi_require_evidence(self):
        self.seller.audit_activity='recent'
        self.seller.audit_kaspi='confirmed'
        with self.assertRaises(ValidationError) as caught:
            self.seller.clean()
        self.assertIn('audit_signal_date', caught.exception.message_dict)
        self.assertIn('audit_kaspi', caught.exception.message_dict)

    def test_import_identity_guards_atomicity_and_routing_unchanged(self):
        from core.services.seller_audit import import_audits
        good = dict(pk=self.seller.pk, name=self.seller.name, phone=self.seller.whatsapp,
                    city=self.seller.city, expected_audit_status='linked',
                    values={'audit_note':'Проверено повторно'})
        bad = dict(good, phone='77019999999')
        with self.assertRaises(ValidationError):
            import_audits([good, bad])
        self.seller.refresh_from_db()
        self.assertIn('Внутренняя', self.seller.audit_note)
        with self.assertRaises(ValidationError):
            import_audits([dict(good, values={'receive_requests': True})])
        self.assertEqual(import_audits([good])[0].pk, self.seller.pk)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.audit_note, 'Проверено повторно')
        self.assertFalse(self.seller.receive_requests)
        self.assertFalse(self.seller.contact_access_verified)
        self.assertEqual(self.seller.city, 'Алматы')

    def test_admin_and_buyer_pages_show_different_audit_details(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        from django.test import override_settings
        from django.utils import timezone
        self.req.contact_consent_at = timezone.now()
        self.req.save()
        with override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver']):
            user = User.objects.create_superuser('audit-admin', 'audit@example.com', 'test-password')
            self.client.force_login(user)
            response = self.client.get(reverse('control_panel:parts_request_sellers', args=[self.req.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Внутренняя заметка')
            self.assertContains(response, 'Кандидат Kaspi')
            self.assertContains(response, 'Страна специализации')
            admin_html = response.content.decode()
            self.client.logout()
            response = self.client.get(reverse('workflow_buyer_step', args=[self.req.pk, self.req.access_token, 'sellers']))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Телефон совпадает с сайтом.')
            self.assertNotContains(response, 'Внутренняя заметка')
            self.assertNotContains(response, 'kaspi.kz')
            self._browser_check({'admin': admin_html, 'buyer': response.content.decode()})

    def _browser_check(self, pages):
        import os, re, subprocess, tempfile
        from pathlib import Path
        from django.contrib.staticfiles import finders
        if os.environ.get('ZPT_AUDIT_BROWSER_QA') != '1':
            return
        with tempfile.TemporaryDirectory() as folder:
            for name, html in pages.items():
                def inline(match):
                    url = match.group(1).split('?')[0]
                    asset = finders.find(url.removeprefix('/static/')) if url.startswith('/static/') else None
                    return '<style>' + Path(asset).read_text() + '</style>' if asset else ''
                html = re.sub(r'<link[^>]*href="([^\"]+\.css(?:\?[^\"]*)?)"[^>]*>', inline, html)
                Path(folder, name+'.html').write_text(html)
            script = r'''
const {chromium}=require('playwright');const fs=require('fs');const path=require('path');
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox'],executablePath:process.env.ZPT_BROWSER_EXECUTABLE||undefined});
for(const width of [390,1440]){const page=await browser.newPage({viewport:{width,height:1000}});
await page.route('**/*',r=>r.abort());
for(const file of fs.readdirSync(process.argv[1])){await page.setContent(fs.readFileSync(path.join(process.argv[1],file),'utf8'));
await page.locator('details').evaluateAll(nodes=>nodes.forEach(n=>n.open=true));
await page.screenshot({path:'/tmp/zpt-audit-'+file+'-'+width+'.png',fullPage:true});
if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Overflow '+file+' '+width);}
await page.close();}await browser.close();})().catch(e=>{console.error(e);process.exit(1)});'''
            subprocess.run(['node','-e',script,folder],check=True,timeout=60)

    def test_private_import_requires_permission_and_csrf_and_hides_payload_after_save(self):
        import json
        from django.contrib.auth.models import User
        from django.contrib.admin.models import LogEntry
        from django.test import Client, override_settings
        from django.urls import reverse
        url = reverse('control_panel:seller_audit_import')
        with override_settings(ALLOWED_HOSTS=['testserver']):
            staff = User.objects.create_user('restricted-auditor', password='test-password', is_staff=True)
            self.client.force_login(staff)
            self.assertEqual(self.client.get(url).status_code, 403)
            owner = User.objects.create_superuser('owner-auditor', 'owner@example.com', 'test-password')
            self.client.force_login(owner)
            self.assertEqual(self.client.get(url).status_code, 200)
            data = [dict(pk=self.seller.pk, name=self.seller.name, phone=self.seller.whatsapp,
                    city=self.seller.city, expected_audit_status='linked',
                    values={'audit_note':'Приватное обновление'})]
            csrf = Client(enforce_csrf_checks=True); csrf.force_login(owner)
            self.assertEqual(csrf.post(url, {'payload':json.dumps(data)}).status_code, 403)
            response = self.client.post(url, {'payload':json.dumps(data)}, follow=True)
            self.assertContains(response, 'Проверка сохранена: 1 продавцов')
            self.assertNotContains(response, 'Приватное обновление')
            self.seller.refresh_from_db()
            self.assertEqual(self.seller.audit_note, 'Приватное обновление')
            self.assertTrue(LogEntry.objects.filter(object_id=str(self.seller.pk), user=owner).exists())
