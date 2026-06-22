from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]


def extract_script(html_path: Path) -> str:
    text = html_path.read_text(encoding="utf-8")
    match = re.search(r"<script>\s*(.*?)\s*</script>\s*(?:</body>|$)", text, re.S)
    return match.group(1).strip() if match else ""


def fix_api(js: str) -> str:
    js = js.replace(
        "const API='https://zpt-kz-backend.onrender.com/api'",
        "const API = window.ZPT_CONFIG.apiBase.replace(/\\/$/, '')",
    )
    js = js.replace(
        "const API = 'https://zpt-kz-backend.onrender.com/api/service'",
        "const API = window.ZPT_CONFIG.serviceApiBase.replace(/\\/$/, '')",
    )
    js = js.replace(
        "window.location.href =\n    'https://zpt.kz/request-parts/';",
        "window.location.href = '/request-parts/';",
    )
    return js


def extract_style(html_path: Path) -> str:
    text = html_path.read_text(encoding="utf-8")
    start = text.index("<style>") + len("<style>")
    end = text.index("</style>")
    return text[start:end].strip()


pairs = [
    ("templates/request-parts/index.html", "static/js/request-parts-form.js"),
    ("templates/service-request/index.html", "static/js/service-request-form.js"),
    ("templates/request-parts/register/index.html", "static/js/request-parts-register.js"),
    ("request-parts/cabinet/index.html", "static/js/request-parts-cabinet.js"),
    ("templates/service-request/register/index.html", "static/js/service-request-register.js"),
]

for src, dst in pairs:
    js = fix_api(extract_script(root / src))
    (root / dst).write_text(js + "\n", encoding="utf-8")
    print("wrote", dst)

(root / "static/css/portal-register.css").write_text(
    extract_style(root / "templates/request-parts/register/index.html") + "\n",
    encoding="utf-8",
)

(root / "static/css/portal-cabinet.css").write_text(
    extract_style(root / "request-parts/cabinet/index.html") + "\n",
    encoding="utf-8",
)

info_css = extract_style(root / "templates/request-parts/guide/index.html")
wrap_idx = info_css.find(".wrap{")
if wrap_idx > 0:
    info_css = info_css[wrap_idx:]
(root / "static/css/portal-info.css").write_text(info_css + "\n", encoding="utf-8")

print("done")
