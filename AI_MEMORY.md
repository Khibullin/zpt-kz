# AI_MEMORY — ZPT.KZ

> Памятка для AI-ассистентов и разработчиков. Обновлено: июнь 2026.  
> Репозиторий: `Khibullin/zpt-kz`. Продакшн: **https://zpt.kz** (Render: `zpt-kz-backend`).

---

## 1. Что это за проект

**ZPT.KZ** — монолитное Django 6-приложение (Python 3.12), объединяющее:

| Модуль | App | Назначение |
|--------|-----|------------|
| **Request Parts** | `core` | Заявки покупателей на запчасти, волновая рассылка продавцам через Meta WhatsApp |
| **ZPT Market** | `catalog` | Витрина запчастей, CRM продавца |
| **Service Requests** | `service_requests` | Заявки на СТО / детейлинг |
| **Orders** | `orders` | Корзина, checkout (Kaspi — mock) |

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
- `WHATSAPP_TEMPLATE_NAME` — продавцам (default: `mp_request_v1`)
- `WHATSAPP_BUYER_TEMPLATE_NAME` — покупателю (default: `zpt_buyer_request_created`)
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

### WhatsApp — продавцы

- `send_whatsapp_template()` — Meta Cloud API через `urllib.request`
- Шаблон: `WHATSAPP_TEMPLATE_NAME` (`mp_request_v1`)
- Параметры: `_seller_template_body_params()` — 7 body params

### WhatsApp — покупатель

- Шаблон: `zpt_buyer_request_created` (`WHATSAPP_BUYER_TEMPLATE_NAME`)
- Функция: `_buyer_template_body_params(req, sellers_count)` — **3 body params**:
  1. `sellers_count`
  2. `city`
  3. `brand + model`

**⚠️ СТРОГО: не менять Meta-шаблон и `_buyer_template_body_params()` без отдельного согласования пользователя:**
- название шаблона, язык, категорию, количество компонентов, текст
- не добавлять HEADER, BUTTON и другие компоненты
- не отправлять новые ссылки в WhatsApp до согласования

Подготовленные URL-хелперы (для будущего WhatsApp) — `core/buyer_portal.py`:
- `request_page_url(req, with_utm=True)`
- `buyer_history_url(req, with_utm=True)`
- `home_page_url(with_utm=True)`
- `new_request_url(with_utm=True)`
- `repeat_request_url(req, with_utm=True)`

UTM: `utm_source=whatsapp&utm_medium=transactional&utm_campaign=buyer_request_created`

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

## 6. Catalog — ZPT Market

- Главная, фильтры, карточки товаров
- Кабинет продавца: `/seller/dashboard/`
- CSS: `market-catalog.css`, `style.css`, `design-system.css`
- Cache-bust: `?v=...` в шаблонах
- Справочник марок/моделей: `core/vehicle_catalog.py`
- Синхронизация: `python manage.py import_car_catalog_kz`

---

## 7. Service Requests

- API: `/api/service/`
- WhatsApp шаблон исполнителям: `mp_request_v1` (отдельный flow)
- Миграция `0004_create_service_whatsapp_log_table` — noop (модель уже в `0003`)

---

## 8. Frontend / JS

| Файл | Назначение |
|------|------------|
| `static/js/request-parts-form-v5.js` | Актуальная форма заявки (FormData → `/api/create-request/`) |
| `static/js/dom-safe.js` | XSS-safe DOM helpers |
| `static/js/portal-config.js` | API base из `data-api-base` |

**Правило:** при изменении JS инкрементировать версию в шаблоне (`v5` → `v6`), т.к. Render агрессивно кэширует статику.

Форма заявки: `templates/request-parts/index.html`  
Базовый layout порталов: `templates/base_portal.html`

---

## 9. Безопасность

- CSRF на всех POST-формах
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SameSite=None` (prod)
- `SECURE_PROXY_SSL_HEADER` для Render/HTTPS
- Не коммитить: `.env`, `db.sqlite3`, секреты
- Не логировать `WHATSAPP_ACCESS_TOKEN`
- Buyer portal: UUID-токены, legacy numeric route → 404

---

## 10. Git и деплой — правила для AI

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

## 11. Строгие запреты для AI

- ❌ FastAPI, Pydantic, asyncio-фреймворки (если не просят явно)
- ❌ Менять Meta WhatsApp-шаблон покупателя без согласования
- ❌ Менять `dispatch_request_waves.py` без необходимости
- ❌ Создавать фиктивные URL / SEO-страницы
- ❌ Второй buyer sender / signals для повторной отправки WhatsApp
- ❌ Commit/push без запроса
- ❌ Force push в main
- ❌ Обновлять git config

---

## 12. Структура файлов (шпаргалка)

```
backend/           settings.py, urls.py, wsgi.py, pwa_views.py
core/
  views.py         create_request, WhatsApp, seller API, buyer portal views
  buyer_portal.py  URL helpers, seller list, history queryset
  models.py        Request, Seller, RequestDispatch, BuyerPortalAccess, ...
  urls.py          /api/* routes
  tests/           test_buyer_portal.py
catalog/           Market, seller CRM
service_requests/  СТО / детейлинг
orders/            корзина, checkout
templates/         request-parts/, service-request/, request_status.html, ...
static/            css/, js/ (источник)
staticfiles/       collectstatic output (gitignored)
products/          медиафайлы (gitignored)
```

---

## 13. Недавние изменения (хронология)

| Commit | Суть |
|--------|------|
| `beee15a` | Buyer portal: UUID-ссылки, история заявок, список продавцов на странице заявки |
| `c173abb` | Buyer WhatsApp `zpt_buyer_request_created` при создании заявки |
| `d896cb1` | Mobile header: иконка business gateway |
| `2a1f140`… | Business gateway `/business/` — responsive UI |

---

## 14. Следующий шаг (не реализовано)

Когда Meta согласует **4-й body-параметр** или URL-кнопку в шаблоне `zpt_buyer_request_created`:

```python
# Пример будущего изменения _buyer_template_body_params() — НЕ ПРИМЕНЯТЬ БЕЗ СОГЛАСОВАНИЯ
return [
    _wa_template_param(sellers_count),
    _wa_template_param(req.city),
    _wa_template_param(car_info),
    _wa_template_param(request_page_url(req, with_utm=True)),
]
```

---

## 15. Локальная разработка

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
