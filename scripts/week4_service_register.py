from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
text = (root / "templates/service-request/register/index.html").read_text(encoding="utf-8")
match = re.search(r"<script>\s*(.*?)\s*</script>\s*</body>", text, re.S)
js = match.group(1).strip() if match else ""
(root / "static/js/service-request-register.js").write_text(js + "\n", encoding="utf-8")

css = text[text.index("<style>") + 7 : text.index("</style>")].strip()
# drop header/body rules already in portal-common
for marker in [".wrap{", ".card{"]:
    idx = css.find(marker)
    if idx > 0 and marker == ".wrap{":
        css = css[idx:]
        break
(root / "static/css/portal-service-register.css").write_text(css + "\n", encoding="utf-8")
print("service register assets ok")
