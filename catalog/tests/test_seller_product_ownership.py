from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile


class SellerProductOwnershipTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user(
            username='agparts-owner',
            password='secret12345',
        )
        self.owner = SellerProfile.objects.create(
            user=self.owner_user,
            name='AG Parts',
            phone='77771360740',
            city='Алматы',
        )
        self.other_user = User.objects.create_user(
            username='other-seller',
            password='secret12345',
        )
        self.other = SellerProfile.objects.create(
            user=self.other_user,
            name='AG Parts',
            phone='77001112233',
            city='Астана',
        )

    def _product(self, *, seller_profile, seller_name, article, title, slug, status='active'):
        return Product.objects.create(
            title=title,
            slug=slug,
            article=article,
            price=1950,
            seller_name=seller_name,
            seller_profile=seller_profile,
            whatsapp_number='77700000000',
            status=status,
            city='Алматы',
        )

    def test_queryset_uses_canonical_profile_and_unbound_legacy_only(self):
        canonical = self._product(
            seller_profile=self.owner,
            seller_name='',
            article='CAN-1',
            title='Canonical empty name',
            slug='canonical-empty-name',
        )
        stale = self._product(
            seller_profile=self.owner,
            seller_name='Old AG Parts Sign',
            article='CAN-2',
            title='Canonical stale name',
            slug='canonical-stale-name',
        )
        legacy = self._product(
            seller_profile=None,
            seller_name='AG Parts',
            article='LEG-1',
            title='Legacy unbound',
            slug='legacy-unbound',
        )
        foreign = self._product(
            seller_profile=self.other,
            seller_name='AG Parts',
            article='FOR-1',
            title='Other seller same name',
            slug='other-seller-same-name',
        )
        owned = Product.objects.owned_by_seller(self.owner)
        self.assertEqual(
            set(owned.values_list('pk', flat=True)),
            {canonical.pk, stale.pk, legacy.pk},
        )
        self.assertNotIn(foreign.pk, owned.values_list('pk', flat=True))

    def test_public_seller_profile_shows_canonical_with_empty_or_stale_name(self):
        canonical = self._product(
            seller_profile=self.owner,
            seller_name='',
            article='PUB-CAN',
            title='Public canonical',
            slug='public-canonical',
        )
        stale = self._product(
            seller_profile=self.owner,
            seller_name='Устаревшее имя',
            article='PUB-STALE',
            title='Public stale',
            slug='public-stale',
        )
        url = reverse('public_seller_profile', kwargs={'slug': self.owner.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, canonical.article)
        self.assertContains(response, stale.article)
        self.assertContains(response, 'На складе: 2 запчастей')

    def test_public_seller_profile_keeps_legacy_unbound_name_match(self):
        legacy = self._product(
            seller_profile=None,
            seller_name='ag parts',
            article='PUB-LEG',
            title='Public legacy',
            slug='public-legacy',
        )
        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': self.owner.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, legacy.article)

    def test_public_seller_profile_excludes_other_seller_profile(self):
        self._product(
            seller_profile=self.other,
            seller_name='AG Parts',
            article='PUB-FOR',
            title='Should not appear',
            slug='should-not-appear',
        )
        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': self.owner.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'PUB-FOR')
        self.assertContains(response, 'На складе: 0 запчастей')

    def test_dashboard_uses_same_ownership_logic(self):
        canonical = self._product(
            seller_profile=self.owner,
            seller_name='',
            article='DASH-CAN',
            title='Dashboard canonical',
            slug='dashboard-canonical',
        )
        legacy = self._product(
            seller_profile=None,
            seller_name='AG Parts',
            article='DASH-LEG',
            title='Dashboard legacy',
            slug='dashboard-legacy',
        )
        foreign = self._product(
            seller_profile=self.other,
            seller_name='AG Parts',
            article='DASH-FOR',
            title='Dashboard foreign',
            slug='dashboard-foreign',
        )
        self.client.login(username='agparts-owner', password='secret12345')
        response = self.client.get(reverse('seller_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, canonical.title)
        self.assertContains(response, legacy.title)
        self.assertNotContains(response, foreign.title)

    def test_seller_profile_products_count_uses_same_logic(self):
        self._product(
            seller_profile=self.owner,
            seller_name='',
            article='CNT-CAN',
            title='Count canonical',
            slug='count-canonical',
        )
        self._product(
            seller_profile=None,
            seller_name='AG Parts',
            article='CNT-LEG',
            title='Count legacy',
            slug='count-legacy',
        )
        self._product(
            seller_profile=self.other,
            seller_name='AG Parts',
            article='CNT-FOR',
            title='Count foreign',
            slug='count-foreign',
        )
        self.client.login(username='agparts-owner', password='secret12345')
        response = self.client.get(reverse('seller_profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['products_count'], 2)
        self.assertContains(response, '2 шт.')

    def test_edit_and_delete_allowed_for_canonical_owner_not_other_seller(self):
        canonical = self._product(
            seller_profile=self.owner,
            seller_name='',
            article='ED-CAN',
            title='Editable canonical',
            slug='editable-canonical',
        )
        self.client.login(username='agparts-owner', password='secret12345')
        edit_url = reverse('edit_product', kwargs={'pk': canonical.pk})
        delete_url = reverse('delete_product', kwargs={'pk': canonical.pk})
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        self.assertEqual(self.client.get(delete_url).status_code, 200)

        self.client.login(username='other-seller', password='secret12345')
        self.assertEqual(self.client.get(edit_url).status_code, 404)
        self.assertEqual(self.client.get(delete_url).status_code, 404)
        self.assertEqual(
            self.client.post(delete_url).status_code,
            404,
        )
        self.assertTrue(Product.objects.filter(pk=canonical.pk).exists())

    def test_product_detail_related_uses_canonical_and_legacy_fallback(self):
        canonical = self._product(
            seller_profile=self.owner,
            seller_name='',
            article='REL-CAN',
            title='Related canonical',
            slug='related-canonical',
        )
        legacy = self._product(
            seller_profile=None,
            seller_name='AG Parts',
            article='REL-LEG',
            title='Related legacy',
            slug='related-legacy',
        )
        foreign = self._product(
            seller_profile=self.other,
            seller_name='AG Parts',
            article='REL-FOR',
            title='Related foreign',
            slug='related-foreign',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': canonical.slug})
        )
        self.assertEqual(response.status_code, 200)
        related_ids = {item.pk for item in response.context['seller_products']}
        self.assertEqual(related_ids, {legacy.pk})
        self.assertNotIn(foreign.pk, related_ids)

        response = self.client.get(
            reverse('product_detail', kwargs={'slug': legacy.slug})
        )
        self.assertEqual(response.status_code, 200)
        related_ids = {item.pk for item in response.context['seller_products']}
        self.assertEqual(related_ids, {canonical.pk})
        self.assertNotIn(foreign.pk, related_ids)
