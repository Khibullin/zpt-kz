from pathlib import Path

root = Path(__file__).resolve().parents[1]
cab = (root / "request-parts/cabinet/index.html").read_text(encoding="utf-8")
start = cab.index("<body>") + len("<body>")
end = cab.index("<script>")
html = cab[start:end].strip()
html = html.replace("https://zpt.kz/request-parts/", "/request-parts/")

template = """{% extends 'base_portal.html' %}
{% load static %}

{% block title %}Кабинет продавца — ZPT.kz{% endblock %}

{% block portal_css %}
<link rel="stylesheet" href="{% static 'css/portal-cabinet.css' %}">
{% endblock %}

{% block portal_header %}{% endblock %}

{% block content %}
""" + html + """
{% endblock %}

{% block portal_js %}
<script src="{% static 'js/request-parts-cabinet.js' %}"></script>
{% endblock %}
"""

(root / "templates/request-parts/cabinet").mkdir(parents=True, exist_ok=True)
(root / "templates/request-parts/cabinet/index.html").write_text(template, encoding="utf-8")
print("cabinet template ok")
