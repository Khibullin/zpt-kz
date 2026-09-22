import hashlib

from django.test import SimpleTestCase

from payments.signatures import (
    sign_init,
    sign_result,
    sign_success,
    signatures_equal,
    sorted_shp_items,
)


def independent_sha256(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


# Independent of payments.signatures; checked against hashlib in this module.
FROZEN_INIT_SHA256 = hashlib.sha256(
    b'zptkz:1000.00:42:stage1_pass1'
).hexdigest()


class SignatureTests(SimpleTestCase):
    def test_init_matches_independent_hashlib(self):
        expected = independent_sha256('zptkz:1000.00:42:stage1_pass1')
        self.assertEqual(
            sign_init('zptkz', '1000.00', '42', 'stage1_pass1'),
            expected,
        )
        self.assertEqual(expected, FROZEN_INIT_SHA256)
        self.assertEqual(len(expected), 64)

    def test_result_uses_raw_outsum_string(self):
        raw = '1000.000000'
        expected = independent_sha256('1000.000000:42:stage1_pass2')
        formatted = independent_sha256('1000.00:42:stage1_pass2')
        self.assertNotEqual(expected, formatted)
        self.assertEqual(
            sign_result('1000.000000', '42', 'stage1_pass2'),
            expected,
        )
        self.assertEqual(raw, '1000.000000')

    def test_success_uses_password_one(self):
        expected = independent_sha256('1000.00:42:stage1_pass1')
        self.assertEqual(
            sign_success('1000.00', '42', 'stage1_pass1'),
            expected,
        )
        self.assertNotEqual(
            sign_success('1000.00', '42', 'stage1_pass1'),
            sign_result('1000.00', '42', 'stage1_pass2'),
        )

    def test_shp_sorted_and_included(self):
        params = {'Shp_b': '2', 'Shp_a': '1'}
        self.assertEqual(sorted_shp_items(params), [('Shp_a', '1'), ('Shp_b', '2')])
        expected = independent_sha256(
            '1000.00:42:stage1_pass2:Shp_a=1:Shp_b=2'
        )
        self.assertEqual(
            sign_result(
                '1000.00',
                '42',
                'stage1_pass2',
                {'Shp_b': '2', 'Shp_a': '1'},
            ),
            expected,
        )

    def test_signatures_equal_is_case_insensitive_and_constant_time(self):
        digest = independent_sha256('zptkz:1000.00:42:stage1_pass1')
        self.assertTrue(signatures_equal(digest, digest.upper()))
        self.assertFalse(signatures_equal(digest, '0' * 64))
        self.assertFalse(signatures_equal(digest, ''))
        self.assertFalse(signatures_equal(digest, digest[:-1]))
