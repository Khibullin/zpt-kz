from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from .analysis import evaluate
from .importing import import_offers, import_demand
from .models import Membership, Supplier, Offer, Preferences, Selection, Activity


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('buyer', password='test-password-123')
        cls.other = get_user_model().objects.create_user('other', password='test-password-123')
        cls.sales = get_user_model().objects.create_user('manager', password='test-password-123')
        cls.admin = get_user_model().objects.create_user('workspace-admin', password='test-password-123')
        cls.outsider = get_user_model().objects.create_user('outsider', password='test-password-123')
        for user, role in [(cls.user, 'user'), (cls.other, 'user'), (cls.sales, 'sales'), (cls.admin, 'admin')]:
            Membership.objects.create(user=user, role=role)
        cls.supplier = Supplier.objects.create(name='Omega')
        cls.offer = Offer.objects.create(supplier=cls.supplier, article='00123', brand='EEP', name='Деталь', cost=1000,
            kaspi_price=3000, checked_on=timezone.localdate(), evidence_date=timezone.localdate(),
            recent_reviews=1, stock='>20 шт.', match_verified=True, kaspi_url='https://kaspi.kz/shop/p/test-123/')

    def url(self, name, **kwargs):
        return reverse('procurement:' + name, kwargs=kwargs or None)

    def login(self, user=None):
        self.client.force_login(user or self.user)

    def test_anonymous_redirects_and_login_page_renders(self):
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 302)
        self.assertContains(self.client.get(self.url('login')), 'Вход в кабинет закупок')

    def test_regular_site_account_is_not_a_workspace_member(self):
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 403)

    def test_inactive_member_denied(self):
        Membership.objects.filter(user=self.user).update(active=False)
        self.login()
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 403)

    def test_dashboard_detail_and_forms_render(self):
        self.login()
        self.assertContains(self.client.get(self.url('dashboard')), '00123')
        self.assertContains(self.client.get(self.url('detail', pk=self.offer.pk)), 'Сравнение поставщиков')
        self.assertEqual(self.client.get(self.url('settings')).status_code, 200)
        self.assertEqual(self.client.get(self.url('basket')).status_code, 200)

    def test_user_cannot_upload_verify_or_edit_drafts(self):
        self.login()
        for name, kwargs in [('upload', {}), ('users', {}), ('draft', {'pk': self.offer.pk}), ('verify', {'pk': self.offer.pk})]:
            self.assertEqual(self.client.post(self.url(name, **kwargs), {}).status_code, 403)

    def test_manager_can_edit_draft_but_cannot_grant_roles(self):
        self.login(self.sales)
        self.assertEqual(self.client.get(self.url('draft', pk=self.offer.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url('users')).status_code, 403)
        self.assertEqual(self.client.get(self.url('upload')).status_code, 200)

    def test_administrator_role_assignment_and_audit(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url('users')).status_code, 200)
        self.client.post(self.url('users'), {'user_id': self.outsider.pk, 'role': 'sales'})
        self.assertEqual(Membership.objects.get(user=self.outsider).role, 'sales')
        self.outsider.refresh_from_db()
        self.assertFalse(self.outsider.is_staff)
        self.assertTrue(Activity.objects.filter(action='role_changed', user=self.admin).exists())
        self.assertEqual(self.client.post(self.url('users'), {'user_id': self.admin.pk, 'role': 'user'}).status_code, 400)

    def test_basket_is_private_and_post_only(self):
        self.login()
        self.assertEqual(self.client.get(self.url('select', pk=self.offer.pk)).status_code, 405)
        self.client.post(self.url('select', pk=self.offer.pk), {'quantity': 2})
        selected = Selection.objects.get(user=self.user)
        self.login(self.other)
        self.assertNotContains(self.client.get(self.url('basket')), '00123')
        self.client.post(self.url('remove', pk=selected.pk))
        self.assertTrue(Selection.objects.filter(pk=selected.pk).exists())
        self.assertNotContains(self.client.get(self.url('export')), '00123')

    def test_invalid_quantity_rejected(self):
        self.login()
        for value in ['0', '-1', '1.5', 'abc', '100001']:
            self.assertEqual(self.client.post(self.url('select', pk=self.offer.pk), {'quantity': value}).status_code, 400)

    def test_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url('select', pk=self.offer.pk), {'quantity': 1}).status_code, 403)

    def test_unknown_expenses_are_not_zero(self):
        preferences = Preferences(user=self.user)
        result = evaluate(self.offer, preferences)
        self.assertEqual(result['difference'], 2000)
        self.assertIsNone(result['profit'])
        self.assertFalse(result['qualified'])

    def test_financial_calculation_and_qualification(self):
        preferences = Preferences(user=self.user, commission=Decimal(10), expenses=Decimal(100), min_profit=Decimal(1000))
        result = evaluate(self.offer, preferences)
        self.assertEqual(result['profit'], Decimal(1600))
        self.assertEqual(result['markup'], Decimal(200))
        self.assertTrue(result['qualified'])
        self.assertEqual(result['signal'], 'Слабый свежий сигнал')

    def test_stale_and_future_evidence_excluded(self):
        preferences = Preferences(user=self.user, commission=0, expenses=0)
        for date in [timezone.localdate()-timedelta(days=8), timezone.localdate()+timedelta(days=1)]:
            self.offer.evidence_date = date
            self.assertFalse(evaluate(self.offer, preferences)['qualified'])

    def test_no_sales_claim_from_reviews(self):
        self.offer.review_count = 20
        self.offer.recent_reviews = None
        result = evaluate(self.offer, Preferences(user=self.user, commission=0, expenses=0))
        self.assertFalse(result['qualified'])
        self.assertEqual(result['signal'], 'Данных недостаточно')

    def test_unverified_match_excluded(self):
        self.offer.match_verified = False
        self.assertFalse(evaluate(self.offer, Preferences(user=self.user, commission=0, expenses=0))['qualified'])

    def test_zero_kaspi_and_cost_do_not_divide_by_zero(self):
        self.offer.kaspi_price = 0
        self.assertIsNone(evaluate(self.offer, Preferences(user=self.user, commission=0, expenses=0))['margin'])
        self.offer.cost = 0
        self.assertIsNone(evaluate(self.offer, Preferences(user=self.user))['markup'])

    def csv(self, content):
        return SimpleUploadedFile('prices.csv', content.encode('utf-8-sig'))

    def test_csv_import_leading_zeros_and_unknown_prices(self):
        count = import_offers(self.csv('article;brand;name;cost;kaspi_price\n00001;TEST;Деталь;1200;Не найдено\n'), 'New supplier', timezone.localdate(), self.sales)
        self.assertEqual(count, 1)
        offer = Offer.objects.get(article='00001')
        self.assertIsNone(offer.kaspi_price)
        self.assertFalse(offer.match_verified)
        self.assertIsNone(offer.sales_30)

    def test_invalid_row_rolls_back_entire_import(self):
        with self.assertRaises(ValueError):
            import_offers(self.csv('article,brand,name,cost\nA,TEST,Good,100\nB,TEST,Bad,-1\n'), 'Atomic', timezone.localdate(), self.sales)
        self.assertFalse(Supplier.objects.filter(name='Atomic').exists())

    def test_duplicate_rows_are_not_silently_overwritten(self):
        with self.assertRaises(ValueError):
            import_offers(self.csv('article,brand,name,cost\nA,TEST,Good,100\nA,TEST,Again,200\n'), 'Duplicate', timezone.localdate(), self.sales)

    def test_new_card_invalidates_old_match_and_demand(self):
        import_offers(self.csv('article,brand,name,cost,kaspi_url\n00123,EEP,Деталь,1000,https://kaspi.kz/shop/p/other-123/\n'), 'Omega', timezone.localdate(), self.sales)
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.match_verified)
        self.assertIsNone(self.offer.recent_reviews)

    def test_formula_injection_is_escaped_in_export(self):
        self.offer.name = '=HYPERLINK("x")'
        self.offer.save()
        Selection.objects.create(user=self.user, offer=self.offer)
        self.login()
        response = self.client.get(self.url('export'))
        self.assertIn("'=HYPERLINK", response.content.decode('utf-8-sig'))

    def test_unknown_or_zero_stock_not_recommended(self):
        for stock in ['', '0 шт.', 'Нет данных']:
            self.offer.stock = stock
            self.assertFalse(evaluate(self.offer, Preferences(user=self.user, commission=0, expenses=0))['qualified'])

    def test_demand_import_requires_explicit_sales_window(self):
        with self.assertRaises(ValueError):
            import_demand(self.csv('article,brand,sales_30\n00123,EEP,10\n'), 'Omega', timezone.localdate(), self.sales)
        self.offer.refresh_from_db()
        self.assertIsNone(self.offer.sales_30)

    def test_demand_import_zero_vs_unknown_and_resets_old_values(self):
        date = timezone.localdate()
        start = (date - timedelta(days=29)).isoformat()
        import_demand(self.csv(f'article,brand,sales_30,period_start,period_end\n00123,EEP,0,{start},{date.isoformat()}\n'), 'Omega', date, self.sales)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.sales_30, 0)
        self.assertIsNone(self.offer.recent_reviews)
        self.assertTrue(self.offer.evidence_source)
        import_demand(self.csv('article,brand,review_count,recent_reviews\n00123,EEP,4,1\n'), 'Omega', date, self.sales)
        self.offer.refresh_from_db()
        self.assertIsNone(self.offer.sales_30)
        self.assertEqual(self.offer.recent_reviews, 1)

    def test_xlsx_import_original_omega_headers(self):
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Оптовой каталог'
        sheet.append(['Omega price list'])
        for _ in range(6):
            sheet.append([])
        sheet.append(['№', 'Артикул', 'Бренд', 'Наименование', 'Категория', 'Оптовая цена, ₸', 'Остаток на сайте', 'Мин. заказ, шт.'])
        sheet.append([1, '00099', 'TEST', 'Деталь', 'Двигатель', 450, '>10 шт.', 1])
        file = BytesIO()
        workbook.save(file)
        upload = SimpleUploadedFile('omega.xlsx', file.getvalue())
        self.assertEqual(import_offers(upload, 'Excel supplier', timezone.localdate(), self.sales), 1)
        self.assertEqual(Offer.objects.get(article='00099').cost, 450)

    def test_business_admin_can_revoke_workspace_access_only(self):
        self.login(self.admin)
        self.client.post(self.url('users'), {'user_id': self.user.pk, 'role': 'disabled'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(Membership.objects.get(user=self.user).active)

    def test_best_mode_recalculates_after_expense_change(self):
        self.login()
        Preferences.objects.create(user=self.user, commission=10, expenses=100)
        self.assertContains(self.client.get(self.url('dashboard')+'?mode=best'), '00123')
        self.client.post(self.url('settings'), {'commission': 10, 'expenses': 2000, 'min_profit': 1000, 'min_markup': 30})
        self.assertNotContains(self.client.get(self.url('dashboard')+'?mode=best'), '00123')

    def test_photo_import_rejects_script_url(self):
        with self.assertRaises(ValueError):
            import_offers(self.csv('article,brand,name,cost,photo_url\nA,TEST,Good,100,javascript:alert(1)\n'), 'Photo', timezone.localdate(), self.sales)
        self.assertFalse(Supplier.objects.filter(name='Photo').exists())
