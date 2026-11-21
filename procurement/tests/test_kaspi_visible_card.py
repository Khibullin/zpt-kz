from django.test import SimpleTestCase
from procurement.kaspi_visible_card import read_visible_kaspi


class KaspiVisibleCharacteristicsTests(SimpleTestCase):
    def test_visible_characteristics_from_exact_product_page(self):
        html = """
        <html><head><meta property="og:title"
        content="Диски EEP передние 15E130002"></head><body>
        <h1>Диски EEP передние 15E130002</h1>
        <section><h2>Характеристики</h2>
        <div><span>Код товара</span><span>151099201</span></div>
        <div><span>Марка автомобиля</span><span>Ford, Mazda</span></div>
        <div><span>Модель автомобиля</span><span>Tribute</span></div>
        <div><span>Год выпуска автомобиля</span><span>2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007</span></div>
        <div><span>Назначение</span><span>передние</span></div>
        <div><span>Номер OEM</span><span>E100-33-25XA</span></div>
        <div><span>Артикул производителя</span><span>15E130002</span></div>
        <div><span>Количество в комплекте</span><span>1</span></div>
        </section></body></html>
        """
        result = read_visible_kaspi(
            html, "https://kaspi.kz/shop/p/diski-eep-perednie-15e130002-151099201/",
            "EEP", "15E130002", "151099201")
        self.assertEqual(result["oem"], "E100-33-25XA")
        self.assertEqual(result["article"], "15E130002")
        self.assertEqual(result["package"], "1")
        self.assertIn("Ford, Mazda", result["fitment"])
        self.assertIn("Tribute", result["fitment"])
        self.assertIn("2007", result["fitment"])
        self.assertEqual(result["specs"]["purpose"], "передние")

    def test_rejects_unrelated_product_identity(self):
        with self.assertRaises(ValueError):
            read_visible_kaspi("<h1>EEP 15E130001</h1>",
                               "https://kaspi.kz/shop/p/eep-151099201/",
                               "EEP", "15E130002", "151099201")

    def test_rejects_wrong_kaspi_id(self):
        with self.assertRaises(ValueError):
            read_visible_kaspi("<h1>EEP 15E130002</h1>",
                               "https://kaspi.kz/shop/p/eep-151099201/",
                               "EEP", "15E130002", "151099202")
