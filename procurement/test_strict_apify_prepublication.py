import unittest
from decimal import Decimal
from procurement.strict_apify_prepublication import select_minimum_seller_quotes


def card(article, price, product_id, query, *, count='1 шт', brand='Superzing', category='Shock absorbers', seller=True):
    return dict(id=product_id, title=f'{brand} задний {article} {count}', brand=brand,
        url=f'https://kaspi.kz/shop/p/{brand.lower()}-zadnii-{article.lower()}-1-sht-{product_id}/',
        price=price, minPrice=price, inStock=True, currency='KZT',
        categoryCode=category, city='Almaty', searchQuery=query,
        offers=[dict(price=price, sellerId='123', sellerName='Seller')] if seller else [])


class StagingQuoteTests(unittest.TestCase):
    def test_cheaper_card_from_different_query_is_selected(self):
        rows=[card('365757', 10763, '179042686','Superzing 365757'),
              card('365757', 5320, '122329934','Superzing 365753')]
        out=select_minimum_seller_quotes(rows,[('Superzing','365757')])
        self.assertEqual(out[('superzing','365757')]['min_price'],Decimal('5320'))
        self.assertEqual(out[('superzing','365757')]['observed_card_ids'],['122329934','179042686'])

    def test_unexpected_other_article_can_be_candidate(self):
        rows=[card('S2S1372',14822,'105187710','STINSS S2S1575',brand='STINSS')]
        out=select_minimum_seller_quotes(rows,[('STINSS','S2S1372')])
        self.assertEqual(out[('stinss','s2s1372')]['product_id'],'105187710')

    def test_matching_kit_quantity_is_required(self):
        rows=[card('365757',1000,'122329934','Superzing 365757',count='2 шт')]
        self.assertFalse(select_minimum_seller_quotes(rows,[('Superzing','365757')]))

    def test_full_article_and_brand_are_exact(self):
        rows=[card('AB-123',1000,'122329934','Superzing AB-12'),
              card('AB-12',950,'123329934','Superzing AB-12',brand='WRONG')]
        self.assertFalse(select_minimum_seller_quotes(rows,[('Superzing','AB-12')]))

    def test_leading_zero_not_collapsed(self):
        rows=[card('00123',3000,'122329934','Superzing 123')]
        self.assertFalse(select_minimum_seller_quotes(rows,[('Superzing','123')]))

    def test_springs_are_allowed(self):
        rows=[card('777',9000,'122329934','Superzing 777',category='Suspension spring')]
        self.assertEqual(select_minimum_seller_quotes(rows,[('Superzing','777')])[('superzing','777')]['min_price'], Decimal('9000'))

    def test_untrusted_non_seller_price_rejected(self):
        rows=[card('365757',1000,'122329934','Superzing 365757',seller=False)]
        self.assertFalse(select_minimum_seller_quotes(rows,[('Superzing','365757')]))

    def test_bad_product_id_url_rejected(self):
        row=card('365757',1000,'122329934','Superzing 365757')
        row['url']='https://kaspi.kz/shop/p/superzing-999999999/'
        self.assertFalse(select_minimum_seller_quotes([row],[('Superzing','365757')]))


if __name__ == '__main__':
    unittest.main()
