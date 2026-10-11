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
            "Воздушный фильтр для Tiggo 7",
            "TIGGO7-FINDER",
            self.tiggo7,
            seller=self.seller,
            engine_compatibility="1.5T\nSQRE4T15C",
        )
        self._product(
            "Салонный фильтр для Tiggo 7",
            "TIGGO7-CABIN-FINDER",
            self.tiggo7,
            seller=self.seller,
            engine_compatibility="2.0T",
        )
        self._product(
            "Фильтр для Tiggo 8",
            "TIGGO8-FINDER",
            self.tiggo8,
            seller=self.seller,
            engine_compatibility="2.0T",
        )
        self._product(
            "Товар другого продавца",
            "OTHER-SELLER-FINDER",
            self.tiggo7,
            seller=None,
            seller_name="Другой продавец",
            engine_compatibility="1.5T",
        )

    def _product(
        self,
        title,
        article,
        model,
        *,
        seller,
        seller_name="AG Parts",
        engine_compatibility="",
        compatibility="",
    ):
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
            engine_compatibility=engine_compatibility,
            compatibility=compatibility,
        )

    def test_brand_selection_shows_products_for_all_its_models(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk},
        )
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
            {
                "brand": self.brand.pk,
                "model": self.tiggo7.pk,
                "engine": "1.5T",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertNotContains(response, "Салонный фильтр для Tiggo 7")
        self.assertNotContains(response, "Фильтр для Tiggo 8")
        self.assertNotContains(response, "Товар другого продавца")

    def test_multi_model_item_maps_engines_from_each_model_clause(self):
        product = self._product(
            "Фильтр для Tiggo 7 и Tiggo 8",
            "MULTI-MODEL-FINDER",
            self.tiggo7,
            seller=self.seller,
            engine_compatibility="T15A\nT8B",
            compatibility="Chery Tiggo 7 1.5 T15A; Chery Tiggo 8 1.5 T8B.",
        )
        product.selected_models.add(self.tiggo8)

        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo8.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("T8B", response.context["model_engine_options"])
        self.assertNotIn("T15A", response.context["model_engine_options"])

        engine_response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {
                "brand": self.brand.pk,
                "model": self.tiggo8.pk,
                "engine": "T8B",
            },
        )
        self.assertContains(engine_response, "Фильтр для Tiggo 7 и Tiggo 8")
        self.assertNotContains(engine_response, "Фильтр для Tiggo 8")

    def test_explicit_model_engine_exclusion_is_not_offered_or_shown(self):
        product = Product.objects.get(article="TIGGO8-FINDER")
        product.engine_compatibility = "2.0T\nSQRE4T15B\nSQRE4T15C"
        product.compatibility = (
            "Chery Tiggo 8 2.0T SQRE4T15B/C; "
            "Не Tiggo 8 SQRE4T15C."
        )
        product.save(update_fields=["engine_compatibility", "compatibility"])

        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": self.brand.pk, "model": self.tiggo8.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("SQRE4T15B", response.context["model_engine_options"])
        self.assertNotIn("SQRE4T15C", response.context["model_engine_options"])

        excluded_response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {
                "brand": self.brand.pk,
                "model": self.tiggo8.pk,
                "engine": "SQRE4T15C",
            },
        )
        self.assertEqual(excluded_response.status_code, 200)
        self.assertEqual(excluded_response.context["selected_engine"], "")
        self.assertContains(excluded_response, "Фильтр для Tiggo 8")

    def test_positive_engine_fitment_survives_following_negative_sentence(self):
        haval = Brand.objects.create(country=self.brand.country, name="Haval")
        jolion = CarModel.objects.create(brand=haval, name="Jolion")
        self._product(
            "Фильтр Haval Jolion",
            "JOLION-SENTENCE-FINDER",
            jolion,
            seller=self.seller,
            engine_compatibility="GW4G15K\nGW4B15D",
            compatibility=(
                "Haval Jolion 1.5 GW4G15K / GW4B15D (с 04.2021). "
                "OEM 1109104XGW02A. Не смешивать с 1109101XGW01A."
            ),
        )

        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": haval.pk, "model": jolion.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.context["model_engine_options"]),
            {"GW4G15K", "GW4B15D"},
        )

    def test_year_selection_filters_products_and_engine_options(self):
        haval = Brand.objects.create(country=self.brand.country, name="Haval")
        jolion = CarModel.objects.create(brand=haval, name="Jolion")
        base = self._product(
            "Фильтр Haval Jolion для раннего года",
            "JOLION-EARLY-YEAR-FINDER",
            jolion,
            seller=self.seller,
            engine_compatibility="GW4G15K\nGW4B15D",
            compatibility=(
                "Haval Jolion 1.5 GW4G15K (с 04.2021); "
                "Haval Jolion 1.5 GW4B15D (с 2023)."
            ),
        )
        base.brand = haval
        base.save(update_fields=["brand"])
        late = self._product(
            "Фильтр Haval Jolion для позднего года",
            "JOLION-LATE-YEAR-FINDER",
            jolion,
            seller=self.seller,
            engine_compatibility="GW4B15D",
            compatibility="Haval Jolion 1.5 GW4B15D (с 2024).",
        )
        late.brand = haval
        late.save(update_fields=["brand"])

        model_response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": haval.pk, "model": jolion.pk},
        )
        self.assertEqual(model_response.status_code, 200)
        self.assertIn(2022, model_response.context["model_year_options"])
        self.assertIn(2024, model_response.context["model_year_options"])
        self.assertEqual(
            set(model_response.context["model_engine_options"]),
            {"GW4G15K", "GW4B15D"},
        )

        early_year_response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"brand": haval.pk, "model": jolion.pk, "year": "2022"},
        )
        self.assertEqual(early_year_response.status_code, 200)
        self.assertContains(early_year_response, "Фильтр Haval Jolion для раннего года")
        self.assertNotContains(early_year_response, "Фильтр Haval Jolion для позднего года")
        self.assertEqual(
            early_year_response.context["model_engine_options"],
            ["GW4G15K"],
        )

        late_engine_response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {
                "brand": haval.pk,
                "model": jolion.pk,
                "year": "2024",
                "engine": "GW4B15D",
            },
        )
        self.assertEqual(late_engine_response.status_code, 200)
        self.assertEqual(late_engine_response.context["selected_engine"], "GW4B15D")
        self.assertContains(late_engine_response, "Фильтр Haval Jolion для позднего года")

    def test_invalid_engine_is_ignored_without_hiding_model_results(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {
                "brand": self.brand.pk,
                "model": self.tiggo7.pk,
                "engine": "1.5",
            },
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

    def test_article_search_matches_exact_active_ag_parts_article_case_insensitively(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"article": "tiggo7-finder"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["article_searched"])
        self.assertEqual(response.context["article_query"], "tiggo7-finder")
        self.assertEqual(
            [product.article for product in response.context["products"]],
            ["TIGGO7-FINDER"],
        )
        matched_product = response.context["products"][0]
        self.assertTrue(hasattr(matched_product, "has_public_wholesale"))
        self.assertTrue(hasattr(matched_product, "public_stock"))
        self.assertContains(response, "Воздушный фильтр для Tiggo 7")
        self.assertNotContains(response, "Салонный фильтр для Tiggo 7")
        self.assertNotContains(response, "Фильтр для Tiggo 8")

    def test_article_search_does_not_return_other_sellers_or_fuzzy_matches(self):
        response = self.client.get(
            reverse("ag_parts_filter_finder"),
            {"article": "OTHER-SELLER-FINDER"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["products"], [])
        self.assertContains(response, "Товар по этому артикулу не найден")
        self.assertNotContains(response, "Товар другого продавца")

    def test_initial_page_has_article_and_vehicle_search_and_no_results(self):
        response = self.client.get(reverse("ag_parts_filter_finder"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ag-finder-article"')
        self.assertContains(response, 'name="article"')
        self.assertContains(response, 'id="ag-finder-brand"')
        self.assertContains(response, 'id="ag-finder-model"')
        self.assertContains(response, 'id="ag-finder-year"')
        self.assertContains(response, 'id="ag-finder-engine"')
        self.assertNotContains(response, "Воздушный фильтр для Tiggo 7")
