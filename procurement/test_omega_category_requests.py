"""Omega staged category requests must preserve the current buying workflow."""
import json
from io import BytesIO
from openpyxl import Workbook
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Activity, Membership, Offer, PriceUpdate, Selection, Supplier
from .omega_selection_requests import (
    REQUEST_ACTION, DEFAULT_CATEGORIES, price_categories, request_queries,
    save_request,
)


class OmegaCategoryRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('omega-categories-admin')
        Membership.objects.create(user=cls.user, role='admin')
        cls.omega = Supplier.objects.create(name='Omega')
        cls.offer = Offer.objects.create(
            supplier=cls.omega, brand='SRR', article='EXISTING',
            name='Existing offer', category='Other',
            cost=Decimal('1500'), stock='6')
        Selection.objects.create(user=cls.user, offer=cls.offer, quantity=2)
        cls.price = PriceUpdate.objects.create(
            supplier=cls.omega, user=cls.user,
            filename='solid-2026.xlsx', sha256='mocksha256', checked_on=date.today(),
            status='ready', row_count=4,
            rows=[
                {'brand': 'SRR', 'article': '085066', 'name': 'Амортизатор 1',
                 'category': 'Амортизатор', 'cost': '2000', 'stock': '20>'},
                {'brand': 'SRR', 'article': '85066', 'name': 'Амортизатор 2',
                 'category': 'Амортизатор', 'cost': '2100', 'stock': '5'},
                {'brand': 'WXQP', 'article': 'B-10', 'name': 'Рычаг',
                 'category': 'Рычаг', 'cost': '3100', 'stock': '3'},
                {'brand': 'SRR', 'article': 'NOT', 'name': 'Нет',
                 'category': 'Амортизатор', 'cost': '5000', 'stock': '0'},
            ])

    def setUp(self):
        self.client.force_login(self.user)

    def test_compact_two_column_picker_shows_previously_selected_first(self):
        page = self.client.get(reverse('procurement:selection_rules'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'omega-category-grid')
        self.assertContains(page, 'grid-template-columns:repeat(2,minmax(0,1fr))')
        self.assertContains(page, 'omega-category-counts')
        self.assertContains(page, 'omega-category-history')
        categories = page.context['omega_categories']
        previously = [bool(category['previous_requests']) for category in categories]
        self.assertEqual(previously, sorted(previously, reverse=True))
        self.assertContains(page, 'name="omega_min_cost"')
        self.assertContains(page, 'name="omega_max_vehicle_age"')

    def test_category_taxonomy_is_lossless_and_keeps_uncertain_items_visible(self):
        from .omega_category_groups import group_categories
        sample = [
            {'name':'Амортизатор', 'previous_requests':[1], 'key':'амортизатор'},
            {'name':'Рычаг', 'previous_requests':[1], 'key':'рычаг'},
            {'name':'Шрус (граната)', 'previous_requests':[1], 'key':'шрус (граната)'},
            {'name':'Рулевая трапеция', 'previous_requests':[], 'key':'рулевая трапеция'},
            {'name':'Пружина', 'previous_requests':[], 'key':'пружина'},
            {'name':'Случайный артикул новой группы', 'previous_requests':[], 'key':'новая'},
        ]
        groups = group_categories(sample)
        self.assertEqual(sum(group['total'] for group in groups), len(sample))
        self.assertEqual(groups[0]['key'], 'suspension')
        suspension = next(group for group in groups if group['key']=='suspension')
        self.assertIn('Амортизаторы, опоры и пружины',
                      [section['name'] for section in suspension['sections']])
        self.assertIn('Рычаги, шаровые и сайлентблоки',
                      [section['name'] for section in suspension['sections']])
        review = next(group for group in groups if group['key']=='review')
        self.assertEqual(review['categories'][0]['name'], 'Случайный артикул новой группы')
        for group in groups:
            for item in group['categories']:
                self.assertIn(item, sample)
            for section in group['sections']:
                for item in section['categories']:
                    self.assertIn(item, sample)

    def test_real_category_view_has_nested_group_controls_without_changing_post(self):
        page = self.client.get(reverse('procurement:selection_rules'))
        self.assertContains(page, 'omega-group-grid')
        self.assertContains(page, 'omega-group-checkbox')
        self.assertContains(page, 'Ходовая и подвеска')
        self.assertContains(page, 'Амортизаторы, опоры и пружины')
        self.assertContains(page, 'Рычаги, шаровые и сайлентблоки')
        self.assertContains(page, 'Выбрать все категории группы')
        self.assertContains(page, 'name="omega_category"', count=2)
        self.assertEqual(sum(group['total'] for group in page.context['omega_category_groups']),
                         len(page.context['omega_categories']))
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action': 'preview_omega_request',
            'price_batch_id': self.price.pk, 'omega_category': ['Амортизатор', 'Рычаг'],
            'omega_min_cost':'2000','omega_max_vehicle_age':'20',
            'omega_stock_submitted':'1','omega_in_stock_only':'on'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['products'], 3)
        self.assertFalse(Activity.objects.filter(action=REQUEST_ACTION).exists())

    def test_categories_list_counts_while_preserving_offers(self):
        summary = {x['name']: x for x in price_categories(self.price)}
        self.assertEqual(summary['Амортизатор']['total'], 3)
        self.assertEqual(summary['Амортизатор']['eligible'], 2)
        self.assertEqual(summary['Рычаг']['eligible'], 1)
        response = self.client.get(reverse('procurement:selection_rules'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Выбор товара из прайса')
        self.assertContains(response, 'История запросов')
        self.assertContains(response, 'solid-2026.xlsx')
        self.assertContains(response, 'value="Амортизатор"')
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.cost, self.offer.stock), (Decimal('1500'), '6'))

    def choose(self, categories, minimum='2000', age='20'):
        payload = {
            'price_batch_id': self.price.pk, 'omega_category': categories,
            'omega_min_cost': minimum, 'omega_max_vehicle_age': age,
            'omega_stock_submitted': '1', 'omega_in_stock_only': 'on',
        }
        response = self.client.post(reverse('procurement:selection_rules'),
                                    {**payload, 'action': 'preview_omega_request'})
        self.assertEqual(response.status_code, 200)
        return payload

    def test_independent_history_saved_and_exported_without_changes_to_purchase(self):
        url = reverse('procurement:selection_rules')
        payload = self.choose(['Амортизатор', 'Рычаг'])
        response = self.client.post(url, {**payload, 'action': 'save_omega_request'})
        self.assertEqual(response.status_code, 302)
        first = Activity.objects.get(action=REQUEST_ACTION)
        self.assertEqual(first.details['title'], 'Запрос №2')
        self.assertEqual(first.details['total'], 3)
        self.assertEqual(first.details['query_count'], 3)
        second = save_request(batch_id=self.price.pk, categories=['Рычаг'],
                              minimum=3000, user=self.user)
        self.assertEqual(second.details['title'], 'Запрос №3')
        self.assertEqual(second.details['total'], 1)
        self.assertEqual(first.details['min_cost'], '2000')
        self.assertEqual(second.details['min_cost'], '3000')
        export = self.client.get(reverse('procurement:selection_apify_json'),
                                 {'request_id': first.pk, 'batch': 1})
        self.assertEqual(export.status_code, 200)
        data = json.loads(export.content)
        self.assertEqual(len(data['searchQueries']), 3)
        self.assertEqual(data['cities'][-1], '391010000')
        mapping = self.client.get(reverse('procurement:selection_apify_json'),
                                  {'request_id': first.pk, 'batch': 1, 'format': 'mapping'})
        self.assertEqual(mapping.status_code, 200)
        self.assertEqual(len(json.loads(mapping.content)['mapping']), 3)
        self.assertEqual(list(Selection.objects.values_list('offer_id', 'quantity')),
                         [(self.offer.pk, 2)])
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.cost, self.offer.stock), (Decimal('1500'), '6'))

    def test_categories_mark_previously_selected_requests_and_legacy_history(self):
        page = self.client.get(reverse('procurement:selection_rules'))
        self.assertContains(page, 'Запрос №1')
        self.assertContains(page, 'Выбрать товары')
        self.assertContains(page, 'id="omega-save-request" hidden disabled')
        self.assertContains(page, '9 файлов подготовлены ранее')
        self.assertContains(page, 'Выбрано в запросе')
        # The default first ten categories are visible as already used, but
        # not automatically checked for the next request.
        self.assertContains(page, 'omega-used-badge')
        self.assertContains(page, 'value="Амортизатор" aria-label="Выбрать Амортизатор"')
        payload = self.choose(['Рычаг'])
        self.client.post(reverse('procurement:selection_rules'),
                         {**payload, 'action': 'save_omega_request'})
        page = self.client.get(reverse('procurement:selection_rules'))
        self.assertContains(page, 'Запрос №2')
        self.assertContains(page, 'id="omega-save-request" hidden disabled')
        self.assertContains(page, 'Повторный выбор разрешён')
        self.assertEqual(list(Selection.objects.values_list('offer_id', 'quantity')),
                         [(self.offer.pk, 2)])

    def test_picker_rules_link_compact_upload_and_live_preview(self):
        page = self.client.get(reverse('procurement:selection_rules'))
        self.assertContains(page, 'Выбор товара из прайса')
        self.assertContains(page, 'Правила отбора')
        self.assertContains(page, 'omega-result-products')
        self.assertContains(page, 'omega_max_vehicle_age')
        self.assertNotContains(page, 'name="full_catalog"')
        help_page = self.client.get(reverse('procurement:omega_selection_rules_help'))
        self.assertContains(help_page, 'Правила отбора товаров из прайса')
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action': 'preview_omega_request', 'price_batch_id': self.price.pk,
            'omega_category': ['Амортизатор', 'Рычаг'],
            'omega_min_cost': '2000', 'omega_max_vehicle_age': '20',
            'omega_stock_submitted': '1', 'omega_in_stock_only': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['products'], 3)
        self.assertEqual(response.json()['queries'], 3)
        self.assertEqual(response.json()['unknown_age'], 3)
        self.assertEqual(list(Selection.objects.values_list('offer_id', 'quantity')),
                         [(self.offer.pk, 2)])

    def test_independent_age_limit_filters_only_explicit_dated_fitment(self):
        from .omega_selection_requests import preview_request
        current = date.today().year
        rows = list(self.price.rows)
        rows.append({'brand':'AGE', 'article':'YEAR-OLD', 'name':'Деталь',
                     'category':'Рычаг', 'cost':'3000', 'stock':'5',
                     'applicability':str(current-26)})
        rows.append({'brand':'AGE', 'article':'YEAR-NEW', 'name':'Деталь',
                     'category':'Рычаг', 'cost':'3000', 'stock':'5',
                     'applicability':str(current-10)})
        self.price.rows = rows
        self.price.save(update_fields=['rows'])
        result = preview_request(batch_id=self.price.pk, categories=['Рычаг'],
                                 minimum=2000, max_vehicle_age=20)
        self.assertEqual(result['products'], 2)
        self.assertEqual(result['unknown_age'], 1)
        result2 = preview_request(batch_id=self.price.pk, categories=['Рычаг'],
                                  minimum=2000, max_vehicle_age=5)
        self.assertEqual(result2['products'], 1)
        self.assertEqual(result2['unknown_age'], 1)

    def test_real_omega_excel_subgroup_is_imported_as_category(self):
        from .importing import load_rows
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Прайс Solid Prof Group'])
        sheet.append(['Дата'])
        sheet.append(['Пр-ль', 'Артикул', 'Подгруппа', 'Наименование',
                      'Торговая марка', 'Цена, за 1шт',
                      'Цена, за 1шт со скидкой', 'Остаток'])
        sheet.append(['VW', '085066', 'Шрус (граната)', 'Шрус', 'SRR',
                      2800, 2200, '20>'])
        file = BytesIO()
        workbook.save(file)
        file.name = 'omega.xlsx'
        file.seek(0)
        rows = load_rows(file)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]['category'], 'Шрус (граната)')
        self.assertEqual(rows[0][1]['cost'], 2200)

    def test_preview_must_precede_save_and_saved_request_links_to_apify(self):
        url = reverse('procurement:selection_rules')
        data = {'action': 'save_omega_request', 'price_batch_id': self.price.pk,
                'omega_category': ['Рычаг'], 'omega_min_cost': '2000',
                'omega_max_vehicle_age': '20', 'omega_stock_submitted': '1',
                'omega_in_stock_only': 'on'}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertFalse(Activity.objects.filter(action=REQUEST_ACTION).exists())
        payload = self.choose(['Рычаг'])
        response = self.client.post(url, {**payload, 'action': 'save_omega_request'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('saved_request=', response.url)
        page = self.client.get(response.url)
        self.assertContains(page, 'Перейти к формированию JSON для Apify')
        self.assertContains(page, '?request_id=1&amp;batch=1')
        self.assertNotContains(page, 'По текущему общему фильтру')
        self.assertContains(page, 'omega-pick-saved')
        self.assertEqual(Activity.objects.filter(action=REQUEST_ACTION).count(), 1)

    def test_changed_criteria_after_preview_cannot_be_saved(self):
        payload = self.choose(['Рычаг'], minimum='2000')
        payload['omega_min_cost'] = '3000'
        self.client.post(reverse('procurement:selection_rules'),
                         {**payload, 'action': 'save_omega_request'})
        self.assertFalse(Activity.objects.filter(action=REQUEST_ACTION).exists())

    def test_exact_duplicate_pricelist_keeps_one_history_record(self):
        import hashlib
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .price_updates import stage_update
        csv = b'brand;article;name;cost;stock\\nSRR;ONE;Item;2000;3\\n'
        self.price.sha256 = hashlib.sha256(csv).hexdigest()
        self.price.save(update_fields=['sha256'])
        uploaded = SimpleUploadedFile('omega.csv', csv)
        previous = stage_update(uploaded, self.omega, self.user, date.today(), False)
        self.assertEqual(previous.pk, self.price.pk)
        self.assertEqual(PriceUpdate.objects.filter(supplier=self.omega).count(), 1)
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action':'upload_price','supplier':self.omega.pk,
            'checked_on':date.today().isoformat(),
            'file':SimpleUploadedFile('omega.csv', csv),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PriceUpdate.objects.filter(supplier=self.omega).count(), 1)

    def test_invalid_selection_does_not_save_anything(self):
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action': 'save_omega_request', 'price_batch_id': self.price.pk,
            'omega_category': ['Случайная категория'], 'omega_min_cost': '2000'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Activity.objects.filter(action=REQUEST_ACTION).exists())
