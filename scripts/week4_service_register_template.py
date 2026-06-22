from pathlib import Path

root = Path(__file__).resolve().parents[1]
text = (root / "templates/service-request/register/index.html").read_text(encoding="utf-8")
start = text.index('<div class="wrap">')
end = text.index('<script src="/static/js/dom-safe.js">')
content = text[start:end].strip()

template = """{% extends 'base_portal.html' %}
{% load static %}

{% block title %}Регистрация исполнителя — ZPT.KZ{% endblock %}

{% block portal_css %}
<link rel="stylesheet" href="{% static 'css/portal-common.css' %}">
<link rel="stylesheet" href="{% static 'css/portal-service-register.css' %}">
{% endblock %}

{% block header_nav %}
<a class="nav-home" href="/">Главная</a>
<a class="nav-main" href="/service-request/">Заявка клиента</a>
<a class="nav-seller" href="/service-request/cabinet/">Кабинет</a>
{% endblock %}

{% block content %}
""" + content + """
{% endblock %}

{% block portal_js %}
<script src="{% static 'js/service-request-register.js' %}"></script>
{% endblock %}
"""

(root / "templates/service-request/register/index.html").write_text(template, encoding="utf-8")
print("register template ok")
