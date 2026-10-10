from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Brand, CarModel, Country, Product, SellerProfile


class AgPartsFilterFinderTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="ag-parts-finder-test")
        self.seller = SellerProfile.objects.create(
            user=user,
            name="AG Parts",
            slug="ag-parts",
            phone="+77000000000",
        )
        country = Country.objects.create(name="Китай AG Finder")
        self.brand = Brand.objects.create(country=country, name="Chery")
        self.tiggo7 = CarModel.objects.create(brand=self.brand, name="Tiggo 7")
        self.tiggo8 = CarModel.objects.create(brand=self.brand, name="Tiggo 8")
        self._product(
            "Фильтр для Tiggo 7",
            "TIGGO7-FINDER",
            self.tiggo7,
            seller=self.seller,
        )
        self._product(
            "Фильтр для Tiggo 8",
            "TIGGO8-FINDER",
            self.tiggo8,
            seller=self.seller,
        )
        self._product(
            "Товар другого продавца",
            "OTHER-SELLER-FINDER",
            self.tiggo7,
            seller=None,
            seller_name="Другой продавец",
        )

    def _product(self, title, article, model, *, seller, seller_name="AG Parts"):
        return Product.objects.create(
            title=title,
            article=article,
            slug=article.lower(),
            price=1000,
            condition="new",
            status="active",
            brand=self.brand,
            car_model=model,
            seller_profile=seller,
            seller_name=seller_name,
            whatsapp_number="+77000000000",
        )

    def test_only_selected_model_products_from_ag_parts_are_shown(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo7.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Фильтр для Tiggo 7")
        self.assertNotContains(response, "Фильтр для Tiggo 8")
        self.assertNotContains(response, "Товар другого продавца")
        self.assertContains(response, "Показать подходящие товары")

    def test_model_must_belong_to_selected_brand(self):
        other_brand = Brand.objects.create(country=self.brand.country, name="Geely")
        other_model = CarModel.objects.create(brand=other_brand, name="Coolray")

        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": other_model.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Фильтр для Tiggo 7")
        self.assertContains(response, "Выберите модель")

    def test_initial_page_has_two_vehicle_fields_and_no_results(self):
        response = self.client.get(reverse("ag_parts_filter_finder"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ag-finder-brand"')
        self.assertContains(response, 'id="ag-finder-model"')
        self.assertNotContains(response, "Фильтр для Tiggo 7")
