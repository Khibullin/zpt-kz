from django.test import SimpleTestCase

from catalog.applicability import supplier_configuration_note


class SupplierConfigurationNoteTests(SimpleTestCase):
    def test_renders_confirmed_restriction_assembly_and_region(self):
        note = supplier_configuration_note({
            "drive": "FWD",
            "market": "EU",
            "placement": "front",
            "side": "left",
            "shock_type": "gas pressure",
            "restriction": "for vehicles with ABS",
            "assembly": "strut without spring",
            "region": "KZ",
        })

        self.assertEqual(
            note,
            "FWD; Рынок: EU; Установка: спереди; Сторона: слева; "
            "Тип: gas pressure; Ограничение: for vehicles with ABS; "
            "Комплектность: strut without spring; Регион: KZ",
        )
