"""Real Chromium layout checks, opt-in in CI; synthetic rendered pages only."""
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import skipUnless
from django.test import TestCase, override_settings
from django.urls import reverse
from core.services.request_connections import participate
from core.tests.test_approved_workflow import ApprovedWorkflowTests


@skipUnless(os.environ.get('ZPT_BROWSER_QA') == '1', 'Chromium QA runs in the browser CI job')
@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'])
class ApprovedBrowserTests(TestCase):
    setUp = ApprovedWorkflowTests.setUp
    buyer = ApprovedWorkflowTests.buyer

    def test_desktop_mobile_layouts(self):
        pages = {}
        for step in ('accepted', 'sellers', 'edit', 'result', 'search', 'close', 'done'):
            pages['buyer-' + step] = self.client.get(self.buyer(step)).content.decode()
        pages['connect'] = self.client.get(reverse('workflow_connect')).content.decode()
        pages['claim'] = self.client.get(reverse('workflow_claim')).content.decode()
        pages['new-shop'] = self.client.get(reverse('workflow_claim') + '?new=1').content.decode()
        self.client.force_login(self.user)
        participate(self.req, self.seller)
        for name, args in [('connection_feed', []), ('connection_detail', [self.req.pk]),
                           ('workflow_seller_contact_page', [self.req.pk]), ('workflow_seller_result', [self.req.pk]),
                           ('connection_quality', []), ('workflow_profile_edit', []), ('workflow_channels', []),
                           ('workflow_telegram_connect', [])]:
            pages[name] = self.client.get(reverse(name, args=args)).content.decode()
        self.client.force_login(self.staff)
        for name, args in [('workflow_admin_list', []), ('workflow_admin_control', [self.req.pk]),
                           ('workflow_admin_reconcile', [self.req.pk]), ('workflow_admin_activity', [self.seller.pk]),
                           ('workflow_admin_buyer_followup', [self.req.pk])]:
            pages[name] = self.client.get(reverse(name, args=args)).content.decode()
        css = Path('core/static/core/connections.css').read_text()
        output = Path('var/reports/workflow-ui').resolve()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            for name, html in pages.items():
                self.assertNotIn('Traceback', html)
                Path(folder, name + '.html').write_text(html.replace('</head>', '<style>' + css + '</style></head>'))
            script = r'''
const {chromium}=require('playwright');
const fs=require('fs'); const path=require('path');
(async()=>{ const browser=await chromium.launch({headless:true});
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
