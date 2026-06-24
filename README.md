# ZPT.KZ — платформа автозапчастей и автосервисов

Монолитное Django-приложение, объединяющие три бизнес-модуля на одном бэкенде с доменом **https://zpt.kz**. Продакшн развёрнут на **Render** (`zpt-kz-backend`).

## Стек технологий

| Компонент | Версия / сервис |
|-----------|-----------------|
| Python | 3.12.8 |
| Django | 6.0.x (`Django==6.0.5`) |
| База данных | PostgreSQL 18 (продакшн через `DATABASE_URL`), SQLite (локальная разработка) |
| WSGI | Gunicorn 25 |
| Статика | WhiteNoise + `collectstatic` |
| Хостинг | Render Web Service |
| Медиа | Локальное хранилище `/products/` |
| Frontend | Server-side Django templates, CSS (`static/css/`), vanilla JS |

## Архитектура модулей

```
backend/                 — settings, urls, wsgi
├── catalog/             — ZPT Market (витрина запчастей, CRM продавца)
├── core/                — волновой подбор заявок на запчасти + Meta WhatsApp API
├── service_requests/    — гео-подбор СТО и детейлинга
├── templates/           — порталы request-parts, service-request
└── static/              — исходники CSS/JS (источник для collectstatic)
```

### 1. Core — волновой подбор заявок (Request Parts)

- **URL:** `/request-parts/`, API `/api/`
- **Назначение:** покупатель оставляет одну заявку на запчасть; система волнами находит продавцов и уведомляет их через **Meta WhatsApp Cloud API** (шаблонные сообщения).
- **Ключевые сущности:** `Request`, `Seller`, `Match`, `Dispatch`
- **Логика:** фильтрация продавцов по стране / марке / модели / городу, очередь рассылки, статусы матчей.
- **Frontend:** портал `templates/request-parts/` + JS (`static/js/request-parts-*.js`).

### 2. Catalog — ZPT Market (двухколоночная витрина + CRM склада)

- **URL:** `/` (главная), `/market/`, карточки товаров, кабинет продавца `/seller/`
- **Назначение:** публичный каталог автозапчастей с фильтрами (страна, марка, модель, категория) и личный кабинет продавца.
- **Ключевые сущности:** `Product`, `SellerProfile`, `Brand`, `CarModel`, `Category`
- **CRM-функции кабинета:**
  - внутренний поиск по артикулу и названию (`q_dashboard`);
  - фильтр по статусу склада: «В наличии» (`active`) / «Под заказ» (`hidden`);
  - мобильная сетка карточек **2×2** (`.products-container`);
  - вертикальные кнопки «Редактировать» / «Удалить» в узких карточках.
- **CSS:** `market-catalog.css` (главная), `style.css` (кабинет, формы), cache-bust через query-параметры `?v=...`.

### 3. Service Requests — гео-подбор СТО и детейлинга

- **URL:** `/service-request/`, API `/api/service/`, каталог `/catalog/services/`
- **Назначение:** клиент создаёт заявку на услугу; исполнители (СТО / детейлинг) получают матчи по городу, району и типу услуги.
- **Ключевые сущности:** `ServiceSeller`, `ServiceRequest`, `ServiceMatch`, `Service`
- **Frontend:** `templates/service-request/` + REST API для кабинета исполнителя.

## Маршрутизация (основное)

| Путь | Модуль | Описание |
|------|--------|----------|
| `/` | catalog | Главная витрина ZPT Market |
| `/seller/login/` | catalog | Вход продавца |
| `/seller/dashboard/` | catalog | CRM-кабинет продавца |
| `/request-parts/` | core | Заявка на запчасти |
| `/service-request/` | service_requests | Заявка на СТО / детейлинг |
| `/api/` | core | REST API запчастей |
| `/api/service/` | service_requests | REST API сервисов |

## Безопасность (продакшн)

Настройки в `backend/settings.py`:

### CSRF и хосты

```python
ALLOWED_HOSTS = ['zpt.kz', 'www.zpt.kz', '.onrender.com', '127.0.0.1', 'localhost']

CSRF_TRUSTED_ORIGINS = [
    'https://zpt.kz',
    'https://www.zpt.kz',
    'https://zpt-kz-backend.onrender.com',
    'https://zpt-kz.onrender.com',
]
```

Защита от **403 CSRF** при POST-формах (вход продавца, добавление товара) с мобильных браузеров и кастомного домена.

### Secure-куки и сессии

```python
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'None'

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'None'
```

- **«Запомнить меня»** на входе продавца: `request.session.set_expiry(1209600)` (14 дней) или `0` (сессия браузера).
- Все формы содержат `{% csrf_token %}`.

### HTTPS и заголовки

```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000  # при DEBUG=False
```

### Cache-busting статики

Браузеры агрессивно кэшируют CSS (WhiteNoise `max-age=31536000`). В шаблонах используются версионные query-параметры:

- `style.css?v=force_refresh_12` — главная / base
- `style.css?v=dashboard_refresh_12` — кабинет продавца
- `market-catalog.css?v=force_refresh_12` — витрина каталога

### Open Graph (WhatsApp / соцсети)

В `templates/base.html` заданы OG-теги для корректного превью ссылок **zpt.kz** при пересылке в WhatsApp и мессенджерах:

- `og:title`, `og:description`, `og:url`, `og:image` (логотип платформы)

### UI продавца на карточке товара

- Ссылка «Все товары продавца →» отделена от соцсетей (`margin-bottom: 12px`).
- Instagram и Сайт — компактные чипы `.seller-social-chip` (пастельные цвета, без красных ссылок в одну строку).

### XSS

- Django auto-escaping в шаблонах.
- Пользовательский ввод выводится через `{{ variable }}`, не через `|safe` без необходимости.
- CORS ограничен явным списком `CORS_ALLOWED_ORIGINS`.

## Локальная разработка

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

Переменные окружения (продакшн):

| Переменная | Назначение |
|------------|------------|
| `SECRET_KEY` | Django secret (обязательна при `DEBUG=False`) |
| `DATABASE_URL` | PostgreSQL connection string |
| `WHATSAPP_TOKEN` | Meta WhatsApp Cloud API |
| `META_PHONE_NUMBER_ID` | ID номера WhatsApp Business |
| `ALLOWED_HOSTS` | Переопределение списка хостов (опционально) |

## Деплой (Render)

1. Push в `main` → автодеплой `zpt-kz-backend`.
2. `build.sh`: `pip install` → `migrate` → `collectstatic`.
3. Start: `gunicorn backend.wsgi:application`.
4. Custom domain: **zpt.kz** → Render service.

## Структура статики

| Файл | Назначение |
|------|------------|
| `static/css/style.css` | Кабинет продавца, формы, профили |
| `static/css/market-catalog.css` | Главная витрина каталога |
| `static/css/market-product.css` | Карточка товара |
| `static/css/portal-common.css` | Общие стили порталов |
| `staticfiles/` | Сборка `collectstatic` (не в git) |

---

**ZPT.KZ** — единая платформа: заявка на запчасть → WhatsApp-уведомления продавцам → витрина Market → гео-подбор автосервиса.
