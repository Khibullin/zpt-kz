from datetime import timedelta
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
    result = {'difference': None, 'markup': None, 'profit': None, 'margin': None,
              'signal': 'Данных недостаточно', 'qualified': False, 'reasons': []}
    if offer.kaspi_price is not None and offer.cost > 0:
        result['difference'] = offer.kaspi_price - offer.cost
        result['markup'] = result['difference'] / offer.cost * 100
        if preferences.commission is not None and preferences.expenses is not None:
            result['profit'] = offer.kaspi_price * (100 - preferences.commission) / 100 - offer.cost - preferences.expenses
            if offer.kaspi_price > 0:
                result['margin'] = result['profit'] / offer.kaspi_price * 100
        else:
            result['reasons'].append('Задайте комиссию и расходы: чистая прибыль пока неизвестна.')
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
        price_fresh and in_stock and (sales or reviews) and offer.match_verified
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
