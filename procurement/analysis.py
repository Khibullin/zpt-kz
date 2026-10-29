from datetime import timedelta
from decimal import Decimal
import re

from django.utils import timezone


def stock_status(stock):
    stock = (stock or '').strip().lower()
    number = re.match(r'^\s*[><≥≤]?\s*(\d+)(?:\s|$)', stock)
    if number:
        return 'in_stock' if int(number.group(1)) > 0 else 'unavailable'
    return 'unavailable' if stock in ('нет', 'нет в наличии', 'отсутствует') else 'unknown'


def evaluate(offer, preferences, today=None):
    today = today or timezone.localdate()
    commission = Decimal(str(preferences.commission)).normalize() if preferences.commission is not None else None
    result = {'gross_difference': None, 'commission_rate': commission,
              'commission_amount': None, 'kaspi_after_commission': None, 'expenses': preferences.expenses,
              'difference': None, 'markup': None, 'profit': None, 'margin': None,
              'signal': 'Данных недостаточно', 'qualified': False, 'reasons': []}
    if offer.kaspi_price is not None:
        price, cost = Decimal(str(offer.kaspi_price)), Decimal(str(offer.cost))
        result['gross_difference'] = price - cost
        if preferences.commission is not None:
            result['commission_amount'] = price * commission / Decimal(100)
            result['kaspi_after_commission'] = price - result['commission_amount']
            result['difference'] = result['kaspi_after_commission'] - cost
            if cost > 0:
                result['markup'] = result['difference'] / cost * Decimal(100)
            if preferences.expenses is not None:
                result['profit'] = result['difference'] - Decimal(str(preferences.expenses))
                if price > 0:
                    result['margin'] = result['profit'] / price * Decimal(100)
            else:
                result['reasons'].append('Задайте прочие расходы: разница после комиссии известна, чистая прибыль пока неизвестна.')
        else:
            result['reasons'].append('Задайте комиссию Kaspi без НДС: разница после комиссии и чистая прибыль пока неизвестны.')
    price_fresh = offer.checked_on is not None and timedelta(0) <= today - offer.checked_on <= timedelta(days=7)
    evidence_fresh = offer.evidence_date is not None and timedelta(0) <= today - offer.evidence_date <= timedelta(days=7)
    sales = evidence_fresh and offer.sales_30 is not None and offer.sales_30 > 0
    reviews = evidence_fresh and offer.recent_reviews is not None and offer.recent_reviews > 0
    result['stock_status'] = stock_status(offer.stock)
    in_stock = result['stock_status'] == 'in_stock'
    result['fresh_reviews'] = bool(reviews)
    result['confirmed_sales'] = bool(sales)
    result['evidence_fresh'] = evidence_fresh
    result['price_fresh'] = price_fresh
    if sales:
        result['signal'] = 'Есть подтверждённые продажи'
    elif reviews:
        result['signal'] = 'Слабый свежий сигнал'
    elif evidence_fresh and offer.review_count == 0:
        result['signal'] = 'Отзывы отсутствуют; продажи неизвестны'
    result['qualified'] = bool(
        price_fresh and in_stock and (sales or reviews)
        and result['profit'] is not None and result['profit'] >= preferences.min_profit
        and result['markup'] is not None and result['markup'] >= preferences.min_markup
    )
    if not offer.match_verified:
        result['reasons'].append('Применимость и комплектность совпадения с Kaspi требуют подтверждения.')
    if not in_stock:
        result['reasons'].append('Не подтверждён положительный остаток у поставщика.')
    if not price_fresh:
        result['reasons'].append('Цена устарела или дата цены неизвестна.')
    if not evidence_fresh:
        result['reasons'].append('Данные спроса устарели или не проверены.')
    if reviews:
        result['reasons'].append(f'Найдено свежих отзывов за 90 дней: {offer.recent_reviews}. Это не количество продаж.')
    if sales:
        result['reasons'].append(f'Продажи за 30 дней по загруженному отчёту: {offer.sales_30}.')
    result['reasons'].append('Рекомендация — кандидат для минимального теста; срок реализации не прогнозируется.')
    return result
