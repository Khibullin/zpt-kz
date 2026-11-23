"""Minimal public ZPT landing. No catalogue queries or background work."""
from django.http import HttpResponse
from django.conf import settings
from django.views.decorators.http import require_GET, require_safe


@require_safe
def minimal_public_home(request):
    return HttpResponse(
        """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZPT.KZ — служебная платформа закупок</title>
<meta name="description" content="ZPT.KZ — служебная платформа учёта и закупок автозапчастей.">
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#172334;font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif}
header,main,footer{max-width:960px;margin:auto;padding:24px}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dfe4ec}
.logo{font-weight:800;font-size:26px;color:#b31323;text-decoration:none}
main{padding-top:100px;padding-bottom:110px}
h1{font-size:clamp(28px,5vw,46px);line-height:1.15;margin:0 0 20px}
p{font-size:18px;line-height:1.6;color:#485568;max-width:650px}
a.button{display:inline-block;margin-top:22px;padding:13px 20px;color:white;background:#b31323;text-decoration:none;border-radius:8px;font-weight:650}
a{color:#244c7b}footer{border-top:1px solid #dfe4ec;color:#647287;font-size:14px}
</style></head><body>
<header><a class="logo" href="/">ZPT.KZ</a><a href="/office/login/">Вход в кабинет</a></header>
<main><h1>Публичный сервис временно приостановлен</h1>
<p>Сейчас доступен только служебный кабинет для сотрудников ZPT.KZ.</p>
<a class="button" href="/office/login/">Служебный кабинет</a></main>
<footer><a href="/contacts/">Контакты</a> · <a href="/privacy/">Конфиденциальность</a></footer>
</body></html>""",
        content_type="text/html; charset=utf-8",
    )


@require_GET
def minimal_robots(request):
    return HttpResponse(
        "User-agent: *\nDisallow: /\n",
        content_type="text/plain; charset=utf-8",
    )


@require_GET
def minimal_sitemap(request):
    return HttpResponse(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
        content_type="application/xml; charset=utf-8",
    )


@require_safe
def public_home(request):
    if getattr(settings, "PUBLIC_CATALOG_ENABLED", False):
        from catalog.views import catalog_list
        return catalog_list(request)
    return minimal_public_home(request)


@require_GET
def public_robots(request):
    if getattr(settings, "PUBLIC_CATALOG_ENABLED", False):
        from core.seo_views import robots_txt
        return robots_txt(request)
    return minimal_robots(request)


@require_GET
def public_sitemap(request):
    if getattr(settings, "PUBLIC_CATALOG_ENABLED", False):
        from core.seo_views import sitemap_index
        return sitemap_index(request)
    return minimal_sitemap(request)
