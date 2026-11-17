"""Preserve the last user-uploaded supplier price and saved findings."""
from types import SimpleNamespace
from django.test import SimpleTestCase
from .views import preserved_best_scope


class PreservedSupplierPriceScopeTests(SimpleTestCase):
    def test_unchanged_zakazauto_and_phaeton_show_saved_findings_by_default(self):
        for name in ('ZakazAuto — Костанай', 'Фаетон'):
            self.assertEqual(preserved_best_scope(SimpleNamespace(name=name)), 'all')

    def test_omega_keeps_saved_quotes_for_repeat_verification(self):
        self.assertEqual(preserved_best_scope(SimpleNamespace(name='Omega')), 'all')

    def test_unknown_supplier_uses_conservative_verified_only(self):
        self.assertEqual(preserved_best_scope(None), 'current')
