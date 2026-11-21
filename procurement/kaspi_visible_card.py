"""Conservative extraction of visible Kaspi product characteristics from HTML.

The data comes only from the exact Kaspi product page. Apify price JSON is
never treated as a source of product description or specifications.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

LABELS = {
    'Код товара': 'product_code',
    'Марка автомобиля': 'vehicle_make',
    'Модель автомобиля': 'vehicle_model',
    'Год выпуска автомобиля': 'vehicle_years',
    'Назначение': 'purpose',
    'Номер OEM': 'oem',
    'Артикул производителя': 'article',
    'Количество в комплекте': 'package',
    'Бренд': 'brand',
    'Производитель': 'brand',
    'Тип': 'type',
    'Сторона установки': 'side',
    'Диаметр': 'diameter',
    'Толщина': 'thickness',
}
LABELS_NORMALIZED = {key.casefold(): value for key, value in LABELS.items()}
STOP = {'Продавцы', 'Отзывы', 'Характеристики', 'Общие характеристики',
        'Доставка', 'Открыть в приложении Kaspi.kz'}


class _VisibleCard(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.hidden = 0
        self.tokens = []
        self.meta = {}
        self.images = []
        self._current = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.stack.append(tag)
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden += 1
        if tag == 'meta':
            key = attrs.get('property') or attrs.get('name')
            if key and attrs.get('content'):
                self.meta[key.lower()] = attrs['content']
        if tag == 'img':
            src = attrs.get('src') or attrs.get('data-src')
            if src:
                self.images.append(src)
        if tag in ('tr', 'li', 'dd', 'dt', 'td', 'p', 'h1', 'h2', 'h3', 'span', 'div'):
            self._flush()

    def handle_endtag(self, tag):
        if tag in ('tr', 'li', 'dd', 'dt', 'td', 'p', 'h1', 'h2', 'h3', 'span', 'div'):
            self._flush()
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden = max(0, self.hidden - 1)
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if not self.hidden:
            self._current += data

    def _flush(self):
        text = re.sub(r'\s+', ' ', self._current).strip()
        if text and len(text) < 1200:
            if not self.tokens or self.tokens[-1] != text:
                self.tokens.append(text)
        self._current = ''


def _parts(tokens):
    found = {}
    keys = set(LABELS_NORMALIZED)
    for i, token in enumerate(tokens):
        normalized = token.strip(' :·.').casefold()
        label, value = '', ''
        if normalized in keys:
            label = normalized
            for future in tokens[i+1:i+4]:
                sample = future.strip(' :·.')
                if sample.casefold() in keys or sample in STOP:
                    break
                if sample and len(sample) <= 350:
                    value = sample
                    break
        else:
            for candidate in keys:
                if normalized.startswith(candidate + ':'):
                    label = candidate
                    value = token.split(':', 1)[1].strip()
                    break
        if label and value and LABELS_NORMALIZED[label] not in found:
            found[LABELS_NORMALIZED[label]] = value
    return found


def read_visible_kaspi(page, url, brand, article, product_id):
    host = (urlsplit(url).hostname or '').lower()
    if host != 'kaspi.kz' and not host.endswith('.kaspi.kz'):
        raise ValueError('Страница не принадлежит Kaspi.kz')
    if not re.search(r'-'+re.escape(str(product_id))+r'/?$', urlsplit(url).path):
        raise ValueError('ID выбранной карточки Kaspi не совпадает с URL')
    parsed = _VisibleCard()
    parsed.feed(page)
    parsed._flush()
    text = ' '.join(parsed.tokens)
    # An exact article and brand must be visible on the product page.
    article_match = re.search(r'(?<![\w])'+re.escape(article)+r'(?![\w])',
                              text, re.I)
    brand_match = re.search(r'(?<![\w])'+re.escape(brand)+r'(?![\w])',
                            text, re.I)
    if not article_match or not brand_match:
        raise ValueError('Kaspi не вернул видимый точный бренд и артикул; динамическая вкладка может быть недоступна')
    found = _parts(parsed.tokens)
    if found.get('article') and found['article'].strip().casefold() != article.strip().casefold():
        raise ValueError('Артикул производителя на Kaspi отличается от исходного')
    title = (parsed.meta.get('og:title') or parsed.meta.get('twitter:title') or '')
    if not title or article.casefold() not in title.casefold():
        title = next((x for x in parsed.tokens if article.casefold() in x.casefold() and
                      brand.casefold() in x.casefold() and len(x) <= 200), '')
    gallery = []
    for src in [parsed.meta.get('og:image', ''), *parsed.images]:
        full = urljoin(url, src)
        target = urlsplit(full)
        if (target.scheme == 'https' and target.hostname and
                (target.hostname == 'resources.cdn-kaspi.kz' or
                 target.hostname.endswith('.cdn-kaspi.kz')) and
                full not in gallery):
            gallery.append(full[:2048])
    specs = {key: value for key, value in found.items()
             if key not in ('article', 'brand', 'oem', 'package',
                            'vehicle_make', 'vehicle_model', 'vehicle_years')}
    fitment = '; '.join(f'{label}: {found[key]}' for key, label in (
        ('vehicle_make','Марка автомобиля'),('vehicle_model','Модель автомобиля'),
        ('vehicle_years','Годы выпуска')) if found.get(key))
    description = parsed.meta.get('description', '')
    # Meta descriptions often contain marketing boilerplate; use only when
    # the exact SKU is explicitly named.
    if article.casefold() not in description.casefold():
        description = ''
    if not found and not description:
        raise ValueError('В HTML Kaspi нет читаемой вкладки «Характеристики»')
    return {
        'name': title[:500], 'brand': found.get('brand', brand),
        'article': found.get('article', article),
        'category': '', 'description': description[:5000],
        'specs': specs, 'fitment': fitment,
        'package': found.get('package', ''), 'oem': found.get('oem', ''),
        'analogs': '', 'images': gallery[:8], 'url': url,
        'product_code': found.get('product_code', str(product_id)),
        'source_section': 'Kaspi.kz — Характеристики', 'extraction': 'visible_html',
    }
