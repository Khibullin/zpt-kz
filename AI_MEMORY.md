# AI_MEMORY — ZPT.KZ

> Памятка для AI-ассистентов и разработчиков. Обновлено: июль 2026.
> Репозиторий: `Khibullin/zpt-kz`. Продакшн: **https://zpt.kz** (Render: `zpt-kz-backend`).

---

## 1. Что это за проект

**ZPT.KZ** — монолитное Django 6-приложение (Python 3.12), объединяющее:

| Модуль | App | Назначение |
|--------|-----|------------|
| **Request Parts** | `core` | Заявки покупателей на запчасти, волновая рассылка продавцам через Meta WhatsApp |
| **ZPT Market** | `catalog` | Витрина запчастей, CRM продавца |
| **Service Requests** | `service_requests` | Заявки на СТО / детейлинг |
| **Orders** | `orders` | Корзина, ручное оформление заказа, email администратору (без mock Kaspi и без WhatsApp для заказов) |

Фронтенд: Django templates + vanilla JS + CSS в `static/`.  
API: `/api/` (core), `/api/service/` (service_requests).

---

## 2. Стек и инфраструктура

| Компонент | Детали |
|-----------|--------|
| Django | 6.0.x |
| БД prod | PostgreSQL (`DATABASE_URL`) |
| БД local | SQLite (`db.sqlite3`, в `.gitignore`) |
| WSGI | Gunicorn |
| Статика | WhiteNoise + `collectstatic` → `staticfiles/` (не в git) |
| Медиа | `MEDIA_ROOT = products/`, URL `/products/` |
| Деплой | Render Web Service, push в `main` → автодеплой |
| Build | `build.sh`: install → migrate → `import_car_catalog_kz` → collectstatic |
| Worker | `zpt-kz-dispatch-worker` — волны рассылки (`dispatch_request_waves`) |
| Custom domain | zpt.kz |

**Env (важное):**

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`
- `DATABASE_URL`
- `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID` / `META_PHONE_NUMBER_ID`
- `WHATSAPP_TEMPLATE_NAME` — продавцам (default в коде: `mp_request_v1`; **prod Render:** `zpt_request_notification`)
- `WHATSAPP_BUYER_TEMPLATE_NAME` — покупателю (default: `zpt_buyer_request_receipt`; **prod Render:** `zpt_buyer_request_receipt`)
- `WHATSAPP_TEMPLATE_LANG` — locale шаблона Meta (default: `ru`)
- `PUBLIC_BASE_URL` — default: `https://zpt.kz`

---

## 3. Ключевые URL (публичные)

| URL | Route name | Описание |
|-----|------------|----------|
| `/` | `home` | Главная ZPT Market |
| `/request-parts/` | — | Форма заявки на запчасти |
| `/my-request/<id>/<uuid>/` | `view_request_status_public` | **Защищённая** страница заявки покупателя |
| `/my-request/<id>/` | `view_request_status_legacy_public` | Legacy → **404** (защита от перебора ID) |
| `/my-requests/<uuid>/` | `view_buyer_request_history_public` | История заявок покупателя |
| `/parts-sellers/` | `parts_sellers_catalog_public` | Каталог продавцов запчастей |
| `/parts-seller/<id>/` | `parts_seller_detail_public` | Профиль продавца |
| `/business/` | `business_gateway` | Вход для автобизнеса |
| `/prodavat/` | `seller_landing` | Лендинг регистрации продавца |
| `/seller/dashboard/` | `seller_dashboard` | CRM продавца Market |
| `/service-request/` | — | Заявка на СТО |
| `/api/create-request/` | `create_request` | POST создание заявки (multipart) |

**Не придумывать URL.** Использовать только реальные `name` из `backend/urls.py` и `core/urls.py`.

---

## 4. Core — заявки и рассылка

### Модели (`core/models.py`)

- `Request` — заявка покупателя (+ `access_token` UUID)
- `RequestPhoto` — фото заявки (WebP через Pillow)
- `Seller` — продавец запчастей (очередь рассылки)
- `Match` — факт отправки заявки продавцу
- `RequestDispatch` — очередь волн (`queued`, `sent`, `paused`, `failed`)
- `BuyerPortalAccess` — токен истории заявок по нормализованному телефону
- `WhatsAppMessageLog` — логи WhatsApp

### Поток создания заявки

1. `POST /api/create-request/` → `create_request()` в `core/views.py`
2. `_find_matching_sellers()` — подбор продавцов
3. `_build_dispatch_queue()` — очередь волн
4. `_send_buyer_whatsapp_notification_async()` — уведомление покупателю (фоновый thread)
5. Немедленная отправка первой волны через `_send_dispatch()`

### Волны рассылки

- Команда: `python manage.py dispatch_request_waves`
- Файл: `core/management/commands/dispatch_request_waves.py`
- **Только для продавцов.** Не использовать для buyer-уведомлений.
- **Не менять без явной необходимости.**

### WhatsApp — Meta (аккаунт **Market Parts**)

### WhatsApp — продавцы

- `send_whatsapp_template()` — Meta Cloud API через `urllib.request`
- Шаблон на prod (Render `zpt-kz-backend`): **`zpt_request_notification`** (`WHATSAPP_TEMPLATE_NAME`)
- Default в коде: `mp_request_v1` — на prod переопределяется ENV
- Параметры: `_seller_template_body_params()` — 7 body params
- Рассылка: `_build_dispatch_queue()` + `dispatch_request_waves.py` — **только продавцы**
- **Не изменялся** при интеграции buyer receipt (commit `2fc055d`)

### WhatsApp — покупатель (✅ prod, заявка №349, июль 2026)

- Аккаунт Meta: **Market Parts**
- Шаблон: **`zpt_buyer_request_receipt`** (`WHATSAPP_BUYER_TEMPLATE_NAME`)
- Язык шаблона: **`ru`** (`WHATSAPP_TEMPLATE_LANG`)
- Render ENV (`zpt-kz-backend`): `WHATSAPP_BUYER_TEMPLATE_NAME=zpt_buyer_request_receipt`
- Триггер: `create_request()` → `_send_buyer_whatsapp_notification_async()` (фоновый thread)
- Функции: `_buyer_template_body_params()`, `_buyer_template_button_components()` в `core/views.py`
- **Production-проверка:** заявка **№349** — все 5 body-параметров и обе URL-кнопки работают корректно
- Seller-рассылка и `dispatch_request_waves.py` **не затрагивались**

**Body-параметры (5):**

1. ID заявки (`req.id`)
2. Автомобиль (`brand + model`)
3. Категория (`req.category`)
4. Город (`req.city`)
5. Количество продавцов (`sellers_count`)

**Dynamic URL-кнопки в payload:**

| Кнопка | index | suffix | Итоговый URL |
|--------|-------|--------|--------------|
| «Открыть заявку» | 0 | `{request_id}/{buyer_access_uuid}/` | `/my-request/<request_id>/<buyer_access_uuid>/` |
| «Мои заявки» | 1 | `{buyer_access_uuid}/` | `/my-requests/<buyer_access_uuid>/` |

Helpers (`core/buyer_portal.py`):

- `buyer_request_whatsapp_url_suffix(req)` — suffix для кнопки заявки
- `buyer_history_whatsapp_url_suffix(req)` — suffix для истории (через `ensure_buyer_portal_access`)
- `request_page_url(req, with_utm=True)` — полный URL страницы заявки
- `buyer_history_url(req, with_utm=True)` — полный URL истории
- `home_page_url(with_utm=True)`, `new_request_url(...)`, `repeat_request_url(req, with_utm=True)`

UTM: `utm_source=whatsapp&utm_medium=transactional&utm_campaign=buyer_request_created`

**⚠️ СТРОГО: не менять Meta-шаблон покупателя и `_buyer_template_body_params()` / URL-кнопки без отдельного согласования пользователя.**

Подготовленные URL-хелперы — `core/buyer_portal.py` (см. выше).

---

## 5. Buyer Portal (коммит `beee15a`)

### Защищённый доступ

- Каждая заявка: `Request.access_token` (UUID, уникальный)
- История: `BuyerPortalAccess` — один UUID на нормализованный телефон
- Неправильный токен → HTTP 404
- Телефон **не** передаётся в URL
- Персональные страницы: `<meta name="robots" content="noindex,nofollow">`

### Страница заявки (`templates/request_status.html`)

- Данные заявки + фото (GLightbox)
- Список продавцов из **`RequestDispatch`** (не из временного matched)
- Статусы для покупателя:
  - `sent` → «Заявка отправлена продавцу»
  - `queued` / `paused` → «Ожидает отправки»
  - `failed` / Match.error → «Ошибка отправки»
- Кнопки: WhatsApp продавцу, профиль, повторить заявку, новая заявка, история, каталог, главная

### История (`templates/buyer_request_history.html`)

- Все заявки одного телефона, сортировка: новые первые
- Карточки с кнопками «Открыть заявку» и «Повторить заявку»

### Повтор заявки

- URL: `/request-parts/?transport=...&brand=...&model=...` (query params)
- Prefill: `static/js/request-parts-form-v5.js` → `applyUrlParams()`
- **Не** автo-submit, **не** копировать фото

### Миграция

- `core/migrations/0014_request_access_token_buyerportalaccess.py`

### Тесты

- `core/tests/test_buyer_portal.py` — 12 тестов
- Запуск: `py manage.py test core`

---

## 6b. Orders — ручное оформление (июль 2026)

- **Правило корзины:** один продавец = одна корзина = один заказ (`Product.whatsapp_number` нормализуется)
- **Checkout:** кнопка «Оформить заказ», статус `Order.STATUS_NEW`, без Kaspi mock
- **Email:** `orders/email_notifications.py` → `send_order_admin_email()` на `ORDER_ADMIN_EMAIL`
- **Страница успеха:** `/orders/<id>/<uuid>/success/` — защищена `access_token`, `noindex`
- **Admin:** ручная смена статусов `new → confirmed → awaiting_payment → paid` или `cancelled`
- **Не использовать:** Meta WhatsApp для заказов, `KaspiPayClient` в checkout, публичный mock payment URL
- **Тесты:** `py manage.py test orders`

---

## 7. Catalog — ZPT Market

- Главная, фильтры, карточки товаров
- Кабинет продавца: `/seller/dashboard/`
- Публичный профиль продавца: route `public_seller_profile` (slug)
- CSS: `market-catalog.css`, `market-product.css`, `style.css`, `design-system.css`
- Cache-bust: `?v=...` в шаблонах (см. ниже)
- Справочник марок/моделей: `core/vehicle_catalog.py`
- Синхронизация: `python manage.py import_car_catalog_kz`

### Отображение логотипа продавца (commit `6006861`, июль 2026)

**Финальное правило — «логотип или ничего»:**

| Условие | Поведение |
|---------|-----------|
| `seller.logo` есть | Показать `<img class="seller-logo seller-logo--{size}">` |
| `seller.logo` нет | Ничего не показывать: без инициалов, без ZPT-заглушки, без пустого div/колонки |

**Удалено (не восстанавливать без явного запроса):**

- `catalog/seller_initials.py` — helper инициалов (GG, AP и т.д.)
- filter `seller_initials` в templatetags
- CSS-классы: `.seller-avatar--initials`, `.product-seller-avatar--initials`, `.card-seller-icon-placeholder`
- fallback с логотипом ZPT.KZ вместо отсутствующего логотипа продавца

**Актуальная реализация:**

- Inclusion tag: `{% seller_avatar seller size="lg|sm" link=... wrapper_class=... %}` в `catalog/templatetags/seller_extras.py`
- Шаблон: `catalog/templates/catalog/includes/seller_avatar.html` — выводит только img при наличии `seller.logo`
- Trust card на странице товара (`product_detail.html`): inline `{% if seller.logo %}<img class="seller-logo seller-logo--trust">{% endif %}`
- Места использования: карточка каталога (`catalog_list.html`), страница товара, публичный профиль (`public_seller_profile.html`)

**CSS-классы логотипа (компактно, `object-fit: contain`):**

| Класс | Размер | Где |
|-------|--------|-----|
| `.seller-logo--trust` | 64×64 (56 mobile) | Trust card на странице товара |
| `.seller-logo--lg` | 72×72 (64 mobile) | Профиль, блок «Продавец» на detail |
| `.seller-logo--sm` | 20×20 | Строка продавца в карточке каталога |

**Cache-bust (актуальные версии):**

- `style.css?v=seller_logo_v1` — `base.html`, `public_seller_profile.html`
- `market-product.css?v=seller_logo_v1` — `product_detail.html`
- `market-catalog.css?v=market_v118` — `catalog_list.html`

**Тесты:** `catalog/tests/test_seller_avatar.py` — логотип показывается только при `seller.logo`; без логотипа нет img, инициалов и пустых контейнеров. Запуск: `py manage.py test catalog` (нужен `DEBUG=true` или `SECRET_KEY` локально).

---

## 8. Service Requests

- API: `/api/service/`
- WhatsApp шаблон исполнителям: `mp_request_v1` (отдельный flow)
- Миграция `0004_create_service_whatsapp_log_table` — noop (модель уже в `0003`)

---

## 9. Frontend / JS

| Файл | Назначение |
|------|------------|
| `static/js/request-parts-form-v5.js` | Актуальная форма заявки (FormData → `/api/create-request/`) |
| `static/js/dom-safe.js` | XSS-safe DOM helpers |
| `static/js/portal-config.js` | API base из `data-api-base` |

**Правило:** при изменении JS инкрементировать версию в шаблоне (`v5` → `v6`), т.к. Render агрессивно кэширует статику.

Форма заявки: `templates/request-parts/index.html`  
Базовый layout порталов: `templates/base_portal.html`

---

## 10. Безопасность

- CSRF на всех POST-формах
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SameSite=None` (prod)
- `SECURE_PROXY_SSL_HEADER` для Render/HTTPS
- Не коммитить: `.env`, `db.sqlite3`, секреты
- Не логировать `WHATSAPP_ACCESS_TOKEN`
- Buyer portal: UUID-токены, legacy numeric route → 404

---

## 11. Git и деплой — правила для AI

### Commit

- **Только по явной просьбе пользователя** («да», «commit», «закоммить»)
- Перед commit: `git status`, `git diff`, `git log`
- **Не** включать: `db.sqlite3`, `.env`, случайные правки вне задачи
- PowerShell: использовать `;`, не `&&`
- Push в `main` — только после проверок и явного разрешения

### Деплой

1. Push `main` → Render autodeploy
2. На сервере автоматически: migrate + collectstatic (через `build.sh`)
3. После деплоя с миграциями — убедиться, что `0014` применена

### Проверки перед push

```bash
py manage.py check
py manage.py makemigrations --check
py manage.py migrate
py manage.py test core
```

---

## 12. Строгие запреты для AI

- ❌ FastAPI, Pydantic, asyncio-фреймворки (если не просят явно)
- ❌ Менять Meta WhatsApp-шаблон покупателя без согласования
- ❌ Менять `dispatch_request_waves.py` без необходимости
- ❌ Создавать фиктивные URL / SEO-страницы
- ❌ Второй buyer sender / signals для повторной отправки WhatsApp
- ❌ Commit/push без запроса
- ❌ Force push в main
- ❌ Обновлять git config

---

## 13. Структура файлов (шпаргалка)

```
backend/           settings.py, urls.py, wsgi.py, pwa_views.py
core/
  views.py         create_request, WhatsApp, seller API, buyer portal views
  buyer_portal.py  URL helpers, seller list, history queryset
  models.py        Request, Seller, RequestDispatch, BuyerPortalAccess, ...
  urls.py          /api/* routes
  tests/           test_buyer_portal.py
catalog/           Market, seller CRM
  templatetags/seller_extras.py   inclusion tag seller_avatar (только logo)
  templates/catalog/includes/seller_avatar.html
  tests/test_seller_avatar.py
service_requests/  СТО / детейлинг
orders/            корзина, checkout
templates/         request-parts/, service-request/, request_status.html, ...
static/            css/, js/ (источник)
staticfiles/       collectstatic output (gitignored)
products/          медиафайлы (gitignored)
```

---

## 14. Недавние изменения (хронология)

| Commit | Суть |
|--------|------|
| `2fc055d` | Buyer WhatsApp `zpt_buyer_request_receipt`: 5 body params + 2 URL-кнопки; prod OK на заявке №349 |
| `6006861` | Удалены fallback-инициалы продавца; правило «логотип или ничего»; удалён `seller_initials.py`; CSS `.seller-logo--*` |
| `6da6645` | AI_MEMORY.md — контекст проекта для AI-сессий |
| `beee15a` | Buyer portal: UUID-ссылки, история заявок, список продавцов на странице заявки |
| `c173abb` | Buyer WhatsApp `zpt_buyer_request_created` при создании заявки |
| `d896cb1` | Mobile header: иконка business gateway |
| `2a1f140`… | Business gateway `/business/` — responsive UI |

---

## 15. Следующий шаг (не реализовано)

Buyer WhatsApp receipt (`zpt_buyer_request_receipt`, 5 params + 2 URL-кнопки) — **реализовано и проверено на prod** (commit `2fc055d`, заявка №349). Дальнейшие изменения Meta-шаблона покупателя — только по согласованию.

Историческая заметка (до `2fc055d`): планировался 4-й body-параметр со ссылкой на страницу заявки — вместо этого одобрены dynamic URL-кнопки в шаблоне Meta.

```python
# Устаревший пример — НЕ ПРИМЕНЯТЬ (заменён на URL-кнопки в zpt_buyer_request_receipt)
return [
    _wa_template_param(sellers_count),
    _wa_template_param(req.city),
    _wa_template_param(car_info),
    _wa_template_param(request_page_url(req, with_utm=True)),
]
```

---

## 16. Локальная разработка

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py manage.py migrate
py manage.py runserver
```

Сайт: http://127.0.0.1:8000/

---

*Этот файл создан для передачи контекста между AI-сессиями. При значимых изменениях архитектуры — обновлять вместе с кодом.*
