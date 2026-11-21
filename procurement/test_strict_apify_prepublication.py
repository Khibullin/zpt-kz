import unittest
from decimal import Decimal
from procurement.strict_apify_prepublication import select_minimum_seller_quotes


def card(article, price, product_id, query, *, count='1 шт', brand='Superzing', category='Shock absorbers', seller=True):
    return dict(id=product_id, title=f'{brand} задний {article} {count}', brand=brand,
        url=f'https://kaspi.kz/shop/p/{brand.lower()}-zadnii-{article.lower()}-1-sht-{product_id}/',
        price=price, minPrice=price, inStock=True, currency='KZT',
        categoryCode=category, city='Almaty', searchQuery=query,
        offers=[dict(price=price, sellerId='123', sellerName='Seller')] if seller else [],
        sellersCount=1 if seller else 0)


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

    def test_row_brand_must_be_visible_in_kaspi_product_title_and_slug(self):
        row = card('S2S1552', 12784, '179049603', 'STINSS S2S1552',
                   brand='OtherBrand')
        row['brand'] = 'STINSS'  # misleading Apify field: actual card is OtherBrand
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('STINSS', 'S2S1552')]))

    def test_kaspi_title_and_url_brand_disagreement_is_rejected(self):
        row = card('S2S1552', 12784, '179049603', 'STINSS S2S1552',
                   brand='STINSS')
        row['title'] = 'OtherBrand задний S2S1552 1 шт'
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('STINSS', 'S2S1552')]))
        row['title'] = 'STINSS задний S2S1552 1 шт'
        row['url'] = row['url'].replace('stinss-', 'otherbrand-')
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('STINSS', 'S2S1552')]))

    def test_matching_supplier_brand_card_title_and_slug_passes_brand_gate(self):
        row = card('S2S1552', 12784, '179049603', 'STINSS S2S1552',
                   brand='STINSS')
        out = select_minimum_seller_quotes([row], [('STINSS', 'S2S1552')])
        self.assertEqual(out[('stinss', 's2s1552')]['min_price'], Decimal('12784'))

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


    def test_title_with_two_catalogue_skus_does_not_cross_link(self):
        rows=[card('57409',14005,'179042763','Superzing 57409')]
        rows[0]['title']='Superzing 57409 аналог 57486 1 шт'
        out=select_minimum_seller_quotes(rows,[('Superzing','57409'),('Superzing','57486')])
        self.assertFalse(out, 'Ambiguous title must never link one Kaspi listing to both SKUs')

    def test_invalid_seller_price_cannot_be_silently_ignored(self):
        row=card('57409',14005,'179042763','Superzing 57409')
        row['offers'].append({'price':None,'sellerName':'Unknown-price seller','sellerId':'999'})
        self.assertFalse(select_minimum_seller_quotes([row],[('Superzing','57409')]))

    def test_unidentified_lowest_seller_is_not_verified(self):
        row=card('57409',14005,'179042763','Superzing 57409')
        row['offers'][0].pop('sellerId')
        row['offers'][0].pop('sellerName')
        self.assertFalse(select_minimum_seller_quotes([row],[('Superzing','57409')]))

    def test_batches_are_combined_before_choosing_lowest(self):
        first=card('365757',10763,'179042686','Superzing 365757')
        later=card('365757',5320,'122329934','Superzing 365753')
        out=select_minimum_seller_quotes(
            (row for batch in ([first],[later]) for row in batch),
            [('Superzing','365757')])
        self.assertEqual(out[('superzing','365757')]['min_price'],Decimal('5320'))
        self.assertEqual(out[('superzing','365757')]['source_query'],'Superzing 365753')



    def test_brake_pads_and_ball_joints_categories_are_not_dropped(self):
        rows = [card('PAD001', 9000, '123000001', 'Superzing PAD001',
                     category='Brake pads'),
                card('BJ001', 12000, '123000002', 'Superzing BJ001',
                     category='Ball joints')]
        out = select_minimum_seller_quotes(
            rows, [('Superzing', 'PAD001'), ('Superzing', 'BJ001')])
        self.assertEqual(len(out), 2)

    def test_truncated_seller_list_cannot_certify_a_minimum(self):
        row = card('365757', 5320, '122329934', 'Superzing 365753')
        row['sellersCount'] = 2
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '365757')]))

    def test_matching_seller_count_is_accepted(self):
        row = card('365757', 5320, '122329934', 'Superzing 365753')
        row['sellersCount'] = 1
        out = select_minimum_seller_quotes([row], [('Superzing', '365757')])
        self.assertEqual(out[('superzing', '365757')]['min_price'], Decimal('5320'))

    def test_missing_seller_count_cannot_prove_complete_market_list(self):
        row = card('365757', 5320, '122329934', 'Superzing 365753')
        del row['sellersCount']
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '365757')]))

    def test_seller_count_less_than_returned_offers_is_inconsistent(self):
        row = card('365757', 5320, '122329934', 'Superzing 365753')
        row['offers'].append(dict(price=8000, sellerId='222', sellerName='Other'))
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '365757')]))

    def test_equal_minimum_price_cards_are_flagged_for_identity_review(self):
        rows = [card('163827', 12710, '179820627', 'Superzing 163827'),
                card('163827', 12710, '132689987', 'Superzing 163827')]
        quote = select_minimum_seller_quotes(
            rows, [('Superzing', '163827')])[('superzing', '163827')]
        self.assertEqual(quote['equal_minimum_card_ids'], ['132689987', '179820627'])
        self.assertTrue(quote['needs_identity_choice'])
        self.assertFalse(quote['publication_verified'])

    def test_inconsistent_card_minimum_is_not_accepted(self):
        row = card('365757', 5320, '122329934', 'Superzing 365753')
        row['minPrice'] = 4000
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '365757')]))

    def test_broken_cheaper_card_blocks_higher_quote_for_same_sku(self):
        # A valid 10,763 quote does not prove market minimum when a second
        # matching 5,320 card has a truncated list of sellers.
        high = card('365757', 10763, '179042686', 'Superzing 365757')
        low = card('365757', 5320, '122329934', 'Superzing 365753')
        low['sellersCount'] = 2
        self.assertFalse(select_minimum_seller_quotes(
            [high, low], [('Superzing', '365757')]))

    def test_broken_higher_card_also_blocks_full_sku_minimum(self):
        # A seller snapshot contradiction is never silently ignored even
        # when an apparently lower quote has been collected.
        low = card('365757', 5320, '122329934', 'Superzing 365757')
        high = card('365757', 10763, '179042686', 'Superzing 365753')
        high['offers'][0]['price'] = None
        self.assertFalse(select_minimum_seller_quotes(
            [low, high], [('Superzing', '365757')]))

    def test_anomaly_in_one_sku_does_not_block_independent_sku(self):
        valid = card('57486', 15716, '187300001', 'Superzing 57486')
        broken = card('365757', 5320, '122329934', 'Superzing 365757')
        broken.pop('sellersCount')
        out = select_minimum_seller_quotes(
            [broken, valid], [('Superzing', '365757'), ('Superzing', '57486')])
        self.assertNotIn(('superzing', '365757'), out)
        self.assertIn(('superzing', '57486'), out)


    def test_analogue_article_in_title_is_not_a_supplier_sku_match(self):
        # Only 163827 is in targets, but the card being sold is 57409.
        row = card('57409', 14005, '179042763', 'Superzing 163827')
        row['title'] = 'Superzing передний 57409 аналог 163827 1 шт'
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '163827')]))

    def test_article_must_match_entire_slug_token(self):
        row = card('1638279', 12710, '179820627', 'Superzing 163827')
        row['title'] = 'Superzing передний 163827 1 шт'
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '163827')]))

    def test_one_piece_plus_two_piece_kit_cannot_be_accepted(self):
        row = card('57486', 15716, '152114068', 'Superzing 57486')
        row['title'] = 'Superzing передний 57486 1 шт, комплект 2 шт'
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '57486')]))

    def test_multiple_one_piece_mentions_are_ambiguous(self):
        row = card('57486', 15716, '152114068', 'Superzing 57486')
        row['title'] = 'Superzing передний 57486 1 шт + 1 шт'
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '57486')]))

    def test_analogue_article_in_both_title_and_slug_is_not_primary(self):
        # The old rule checked only SKU presence in slug, so this card
        # could be mis-linked to 163827 when 57409 was the sold item.
        row = card('57409', 14005, '179042763', 'Superzing 163827')
        row['title'] = 'Superzing 57409 аналог 163827 1 шт'
        row['url'] = ('https://kaspi.kz/shop/p/'
                      'superzing-57409-analog-163827-1-sht-179042763/')
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '163827')]))

    def test_analogue_spelled_in_latin_is_not_primary(self):
        row = card('57409', 14005, '179042763', 'Superzing 163827')
        row['title'] = 'Superzing 57409 analog 163827 1 шт'
        row['url'] = ('https://kaspi.kz/shop/p/'
                      'superzing-57409-analog-163827-1-sht-179042763/')
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '163827')]))

    def test_replacement_marker_in_slug_does_not_certify_article(self):
        row = card('57409', 14005, '179042763', 'Superzing 163827')
        row['title'] = 'Superzing 57409 163827 1 шт'
        row['url'] = ('https://kaspi.kz/shop/p/'
                      'superzing-57409-replacement-163827-1-sht-179042763/')
        self.assertFalse(select_minimum_seller_quotes(
            [row], [('Superzing', '163827')]))

    def test_unambiguous_single_piece_with_matching_slug_remains_candidate(self):
        row = card('57486', 15716, '152114068', 'Superzing 57486')
        out = select_minimum_seller_quotes([row], [('Superzing', '57486')])
        self.assertEqual(out[('superzing', '57486')]['min_price'], Decimal('15716'))

if __name__ == '__main__':
    unittest.main()
