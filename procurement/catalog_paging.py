"""Rank the full catalogue in SQL; load full offers only for the visible page."""
from datetime import timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Case, When, Value, Q, F, Count, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, NullIf
from .models import KaspiSellerSnapshot, KaspiCardSearch
from .omega_policy import POSITIVE_STOCK_PATTERN

# Approved purchasing rules: net gain >= 1000 KZT and ROI >= 25%.
MIN_BEST_ROI = Decimal('25')


def best_candidates_query(query, preferences, today):
    if preferences.commission is None or preferences.expenses is None:
        return query.none()
    profit = ExpressionWrapper(
        F('kaspi_price') * Value((Decimal(100) - preferences.commission) / Decimal(100))
        - F('cost') - Value(max(Decimal(250), preferences.expenses)),
        output_field=DecimalField(max_digits=38, decimal_places=12))
    query = query.exclude(Q(name__iregex='подшипник|bearing') | Q(category__iregex='подшипник|bearing'))
    return query.filter(kaspi_price__isnull=False, cost__gt=0, match_verified=True,
                        supplier_checked_on__gte=today - timedelta(days=7), supplier_checked_on__lte=today,
                        kaspi_price_checked_on__gte=today - timedelta(days=7), kaspi_price_checked_on__lte=today).annotate(
                            _best_profit=profit).filter(
                                _best_profit__gte=max(Decimal(1000), preferences.best_min_profit)).filter(
                                _best_profit__gte=F('cost') * Value(MIN_BEST_ROI / Decimal(100)))




def cumulative_profitable_query(query, preferences, today=None):
    """Supplier-scoped buying rules plus immutable earlier profitable records.

    Omega: in-stock cost >= 2,000 KZT and net ROI >= 50% using the saved
    Kaspi quote. A historical quote is a REVIEW candidate, not authorization
    to purchase. No automatic nationwide claim is inferred from Almaty data.
    Other suppliers retain their existing minimum profit/ROI logic.
    """
    from django.utils import timezone
    today = today or timezone.localdate()
    if preferences.commission is None or preferences.expenses is None:
        return query.none()
    profit = ExpressionWrapper(
        F('kaspi_price') * Value((Decimal('100') - preferences.commission) / Decimal('100'))
        - F('cost') - Value(max(Decimal('250'), preferences.expenses)),
        output_field=DecimalField(max_digits=38, decimal_places=12),
    )
    fresh = Q(
        match_verified=True, kaspi_price__isnull=False, cost__gt=0,
        supplier_checked_on__gte=today-timedelta(days=7),
        supplier_checked_on__lte=today,
        kaspi_price_checked_on__gte=today-timedelta(days=7),
        kaspi_price_checked_on__lte=today,
    )
    legacy_threshold = Q(
        _cumulative_profit__gte=max(Decimal('1000'), preferences.best_min_profit)
    ) & Q(_cumulative_profit__gte=F('cost') * Value(MIN_BEST_ROI / Decimal('100')))
    other_supplier = (
        ~Q(supplier__name='Omega')
        & (fresh & legacy_threshold | Q(profitable_record__isnull=False))
        & ~Q(name__iregex='подшипник|bearing', profitable_record__isnull=True)
        & ~Q(category__iregex='подшипник|bearing', profitable_record__isnull=True)
    )
    # Exactly the same positive-stock notation accepted by stock_status().
    omega_stock = Q(stock__regex=POSITIVE_STOCK_PATTERN)
    omega_qualified = (
        (Q(match_verified=True) |
         Q(characteristics__firsthand_omega_20261117__identity_status__in=['candidate', 'conflict']))
        & Q(cost__gte=Decimal('2000'), kaspi_price__gt=0) & ~Q(kaspi_url='')
        & omega_stock
        & Q(_cumulative_profit__gte=F('cost') * Value(Decimal('0.50')))
    )
    # Imported candidates are financial leads only; they do not set match_verified.\n    # Previously captured profitability remains in the historical record,
    # even if a later supplier update removed stock or lowered the margin.
    omega = Q(supplier__name='Omega') & (
        omega_qualified | Q(profitable_record__isnull=False)
    )
    return query.annotate(_cumulative_profit=profit).filter(other_supplier | omega)



def stale_price_candidates_query(query, preferences, today):
    """Historical profit candidates only, never a confirmed buying recommendation.

    An old or undated Kaspi price is retained for review; prices, stock, demand
    and verification timestamps must not be refreshed merely by rendering.
    """
    if preferences.commission is None or preferences.expenses is None:
        return query.none()
    profit = ExpressionWrapper(
        F('kaspi_price') * Value((Decimal(100) - preferences.commission) / Decimal(100))
        - F('cost') - Value(max(Decimal(250), preferences.expenses)),
        output_field=DecimalField(max_digits=38, decimal_places=12))
    cutoff = today - timedelta(days=7)
    return query.exclude(
        Q(name__iregex='подшипник|bearing') | Q(category__iregex='подшипник|bearing')
    ).filter(
        kaspi_price__isnull=False, cost__gt=0, match_verified=True,
        supplier_checked_on__gte=cutoff, supplier_checked_on__lte=today,
    ).filter(
        Q(kaspi_price_checked_on__lt=cutoff) |
        Q(kaspi_price_checked_on__isnull=True)
    ).annotate(
        _stale_profit=profit,
    ).filter(_stale_profit__gte=max(Decimal(1000), preferences.best_min_profit))


def catalog_page(query, preferences, filters, mode, number, today):
    fresh = today - timedelta(days=preferences.no_sellers_days)
    # Determine status from the small set of fresh observations. Do not join
    # every catalogue row to historical JSON snapshots for grouping/sorting.
    offer_query = query
    query = query.select_related(None)
    snapshots = KaspiSellerSnapshot.objects.filter(
        city='750000000', source_url=F('offer__kaspi_url'),
        checked_on__gte=fresh, checked_on__lte=today).exclude(offer__kaspi_url='')
    missing = KaspiCardSearch.objects.filter(
        offer__kaspi_url='', complete=True, city='750000000',
        checked_on__gte=fresh, checked_on__lte=today,
        checked_brand=F('offer__brand'), checked_article=F('offer__article'),
        checked_name=F('offer__name'))
    query = query.annotate(_catalog_status=Case(
        When(pk__in=missing.order_by().values('offer_id'), then=Value('missing')),
        When(pk__in=snapshots.exclude(sellers=[]).order_by().values('offer_id'), then=Value('sellers')),
        When(pk__in=snapshots.filter(complete=True,sellers=[],offer__match_verified=True).order_by().values('offer_id'), then=Value('empty')),
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
    # Conditional counts need no GROUP BY/sort of the whole price list.
    distinct = query.query.distinct
    status_sets = {
        'missing': missing.order_by().values('offer_id'),
        'sellers': snapshots.exclude(sellers=[]).order_by().values('offer_id'),
        'empty': snapshots.filter(complete=True,sellers=[],offer__match_verified=True).order_by().values('offer_id'),
    }
    summary = query.order_by().aggregate(total=Count('pk',distinct=distinct), **{
        key: Count('pk',filter=Q(pk__in=ids),distinct=distinct) for key,ids in status_sets.items()})
    counts = {key: summary[key] for key in status_sets}
    counts['pending'] = summary['total'] - sum(counts.values())
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
    paginator = Paginator(query.values_list('pk', flat=True), 40)
    # The status aggregation already counted the exact filtered scope. Avoid
    # repeating the same joins just to compute the page count.
    selected_statuses = set(filters.get('kaspi_status') or counts)
    paginator.count = sum(counts.get(status, 0) for status in selected_statuses)
    page = paginator.get_page(number)
    ids = list(page.object_list)
    offers = {offer.pk: offer for offer in offer_query.filter(pk__in=ids).order_by()}
    page.object_list = [offers[pk] for pk in ids]
    return page, counts
