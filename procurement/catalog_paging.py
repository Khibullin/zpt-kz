"""Rank the full catalogue in SQL; load full offers only for the visible page."""
from datetime import timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Case, When, Value, Q, F, Count, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, NullIf


def best_candidates_query(query, preferences, today):
    if preferences.commission is None or preferences.expenses is None:
        return query.none()
    profit = ExpressionWrapper(
        F('kaspi_price') * Value((Decimal(100) - preferences.commission) / Decimal(100))
        - F('cost') - Value(max(Decimal(250), preferences.expenses)),
        output_field=DecimalField(max_digits=38, decimal_places=12))
    return query.filter(kaspi_price__isnull=False, cost__gt=0, match_verified=True,
                        checked_on__gte=today - timedelta(days=7), checked_on__lte=today).annotate(
                            _best_profit=profit).filter(_best_profit__gte=max(Decimal(1000), preferences.best_min_profit))


def catalog_page(query, preferences, filters, mode, number, today):
    fresh = today - timedelta(days=preferences.no_sellers_days)
    snapshot = Q(kaspi_sellers__city='750000000', kaspi_sellers__source_url=F('kaspi_url'),
                 kaspi_sellers__checked_on__gte=fresh, kaspi_sellers__checked_on__lte=today) & ~Q(kaspi_url='')
    missing = Q(kaspi_url='', kaspi_card_search__complete=True, kaspi_card_search__city='750000000',
                kaspi_card_search__checked_on__gte=fresh, kaspi_card_search__checked_on__lte=today,
                kaspi_card_search__checked_brand=F('brand'), kaspi_card_search__checked_article=F('article'),
                kaspi_card_search__checked_name=F('name'))
    query = query.annotate(_catalog_status=Case(
        When(missing, then=Value('missing')),
        When(snapshot & ~Q(kaspi_sellers__sellers=[]), then=Value('sellers')),
        When(snapshot & Q(kaspi_sellers__complete=True, kaspi_sellers__sellers=[], match_verified=True), then=Value('empty')),
        default=Value('pending')))
    money = DecimalField(max_digits=38, decimal_places=12)
    null = Value(None, output_field=money)
    if preferences.commission is None:
        difference = null
    else:
        difference = ExpressionWrapper(F('kaspi_price') * Value((Decimal(100) - preferences.commission) / Decimal(100)) - F('cost'), output_field=money)
    query = query.annotate(_catalog_difference=Case(
        When(_catalog_status__in=['empty', 'missing'], then=null), default=difference, output_field=money))
    query = query.annotate(_catalog_roi=ExpressionWrapper(
        F('_catalog_difference') * Value(Decimal(100)) / NullIf(F('cost'), Value(Decimal(0))), output_field=money))
    # Zero and negative purchase costs have no defined return percentage.
    query = query.annotate(_catalog_roi=Case(When(cost__gt=0, then=F('_catalog_roi')), default=null, output_field=money))
    counts = {row['_catalog_status']: row['n'] for row in query.order_by().values('_catalog_status').annotate(n=Count('pk', distinct=True))}
    if filters.get('kaspi_status'):
        query = query.filter(_catalog_status__in=filters['kaspi_status'])
    if filters.get('sort') == 'profit':
        profit = null if preferences.expenses is None else ExpressionWrapper(
            F('_catalog_difference') - Value(max(Decimal(250), preferences.expenses)), output_field=money)
        query = query.annotate(_catalog_profit=profit).order_by(F('_catalog_profit').desc(nulls_last=True), 'pk')
    elif mode == 'reviewed':
        query = query.order_by(F('_catalog_roi').desc(nulls_last=True), 'pk')
    else:
        query = query.order_by(F('_catalog_roi').desc(nulls_last=True), Coalesce('_catalog_difference', Value(Decimal(0))).desc(), 'pk')
    # Sort narrow IDs, not large offer/JSON rows from the joined tables.
    page = Paginator(query.values_list('pk', flat=True), 40).get_page(number)
    ids = list(page.object_list)
    offers = {offer.pk: offer for offer in query.filter(pk__in=ids).order_by()}
    page.object_list = [offers[pk] for pk in ids]
    return page, counts
