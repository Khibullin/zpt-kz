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
        self._product("Воздушный фильтр для Tiggo 7", "TIGGO7-FINDER", self.tiggo7,
                      seller=self.seller, engine_compatibility="1.5T\nSQRE4T15C")
        self._product("Салонный фильтр для Tiggo 7", "TIGGO7-CABIN-FINDER", self.tiggo7,
                      seller=self.seller, engine_compatibility="2.0T")
        self._product("Фильтр для Tiggo 8", "TIGGO8-FINDER", self.tiggo8,
                      seller=self.seller, engine_compatibility="2.0T")
        self._product("Товар другого продавца", "OTHER-SELLER-FINDER", self.tiggo7,
                      seller=None, seller_name="Другой продавец", engine_compatibility="1.5T")

    def _product(
        self, title, article, model, *, seller, seller_name="AG Parts",
        engine_compatibility="", compatibility="",
    ):
        return Product.objects.create(
            title=title, article=article, slug=article.lower(), price=1000,
            condition="new", status="active", brand=self.brand, car_model=model,
            seller_profile=seller, seller_name=seller_name,
            whatsapp_number="+77000000000",
            engine_compatibility=engine_compatibility, compatibility=compatibility,
        )

    def test_brand_selection_shows_products_for_all_its_models(self):
        response = self.client.get(reverse("ag_parts_filter_finder"), {"brand": self.brand.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertContains(response, "Салонный фильтр для Tiggo 7")
        self.assertContains(response, "Фильтр для Tiggo 8")
        self.assertNotContains(response, "Товар другого продавца")

    def test_model_selection_shows_every_product_for_model_without_engine(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo7.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertContains(response, "Салонный фильтр для Tiggo 7")
        self.assertNotContains(response, "Фильтр для Tiggo 8")

    def test_selected_engine_shows_only_exact_ag_parts_fitment(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo7.pk, "engine": "1.5T"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertNotContains(response, "Салонный фильтр для Tiggo 7")
        self.assertNotContains(response, "Фильтр для Tiggo 8")
        self.assertNotContains(response, "Товар другого продавца")

    def test_model_engine_option_respects_explicit_incompatibility(self):
        product = Product.objects.get(article="TIGGO8-FINDER")
        product.engine_compatibility = "2.0T\nSQRE4T15C"
        product.compatibility = "Chery Tiggo 8 2.0T; Не Tiggo 8 SQRE4T15C."
        product.save(update_fields=["engine_compatibility", "compatibility"])
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo8.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("2.0T", response.context["model_engine_options"])
        self.assertNotIn("SQRE4T15C", response.context["model_engine_options"])
        self.assertContains(response, "Фильтр для Tiggo 8")

    def test_invalid_engine_is_ignored_without_hiding_model_results(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo7.pk, "engine": "1.5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertContains(response, "Салонный фильтр для Tiggo 7")
        self.assertContains(response, "Показаны все фильтры для выбранной модели")

    def test_model_must_belong_to_selected_brand(self):
        other_brand = Brand.objects.create(country=self.brand.country, name="Geely")
        other_model = CarModel.objects.create(brand=other_brand, name="Coolray")
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": other_model.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_model"], "")
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertContains(response, "Фильтр для Tiggo 8")

    def test_initial_page_has_three_vehicle_fields_and_no_results(self):
        response = self.client.get(reverse("ag_parts_filter_finder"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ag-finder-brand"')
        self.assertContains(response, 'id="ag-finder-model"')
        self.assertContains(response, 'id="ag-finder-engine"')
        self.assertNotContains(response, "Воздушный фильтр для Tiggo 7")
