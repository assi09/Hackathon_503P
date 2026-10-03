import html
import json
from pathlib import Path


def render(spec, case, checks):
    data = {**spec, "source_url": case["source_url"], "excerpt": case["excerpt"], "checks": checks}
    # Prevent user/model content from closing the inert JSON script element.
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    return template.replace("__PAGE_TITLE__", html.escape(spec["title"] + " · Paper to Playground")).replace("__LESSON_JSON__", encoded)
