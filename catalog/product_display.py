"""Exact public labels; never rewrite seller titles, articles or fitment data."""
import re
import unicodedata

# Reviewed Chinese titles present in the public catalogue on 13 October 2026.
# Translate the named component only; do not infer vehicles or assembly contents.
CHINESE_PRODUCT_TITLES_RU = {
    '前保险杠': 'Передний бампер',
    '后保险杠': 'Задний бампер',
    '空调滤清器': 'Салонный фильтр',
    '机油滤清器': 'Масляный фильтр',
    '火花塞总成': 'Свеча зажигания в сборе',
    '前刹车片': 'Передние тормозные колодки',
    '后刹车片': 'Задние тормозные колодки',
    '拉杆球头(右)': 'Правый наконечник рулевой тяги',
    '拉杆球头(左)': 'Левый наконечник рулевой тяги',
    '后减震器': 'Задний амортизатор',
    '前减震器(右)': 'Передний правый амортизатор',
    '前减震器(左)': 'Передний левый амортизатор',
    '下摆臂胶': 'Сайлентблок нижнего рычага',
    '前下摆臂球头': 'Шаровая опора переднего нижнего рычага',
    '前下摆臂(左)': 'Передний левый нижний рычаг',
    '前轮轴承': 'Подшипник переднего колеса',
    '喷油嘴': 'Топливная форсунка',
}
CJK = re.compile(r'[\u3400-\u9fff]')


def public_product_title(product):
    original = str(getattr(product, 'title', '') or '').strip()
    normalized = unicodedata.normalize('NFKC', original)
    return CHINESE_PRODUCT_TITLES_RU.get(normalized, original)
