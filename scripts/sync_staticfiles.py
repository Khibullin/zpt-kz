import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
(root / "staticfiles/js").mkdir(parents=True, exist_ok=True)

js_files = [
    "portal-config.js",
    "request-parts-form.js",
    "request-parts-register.js",
    "request-parts-cabinet.js",
    "service-request-form.js",
    "service-request-register.js",
]

for name in js_files:
    shutil.copy2(root / "static/js" / name, root / "staticfiles/js" / name)

for css_file in (root / "static/css").glob("portal-*.css"):
    shutil.copy2(css_file, root / "staticfiles/css" / css_file.name)

print("staticfiles synced")
