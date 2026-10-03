"""Render a validated explanation spec into one self-contained HTML page.

The page is a generic template: layout, CSS, widgets and controls live in
templates/; all paper-specific content comes from the generated spec.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from latex2mathml.converter import convert as latex_to_mathml

TEMPLATES = Path(__file__).resolve().parent / "templates"
_MATH = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)


def math_html(latex: str, display: bool = False) -> str:
    """LaTeX to native MathML; falls back to escaped code if conversion fails."""
    try:
        out = latex_to_mathml(latex.strip(), display="block" if display else "inline")
        if "<math" in out:
            return out
    except Exception:
        pass
    return f"<code class='tex'>{html.escape(latex)}</code>"


def latex_ok(latex: str) -> bool:
    try:
        return "<math" in latex_to_mathml(latex.strip())
    except Exception:
        return False


def rich(text: Any) -> str:
    """Escape text and render $inline$ / $$display$$ math. Allows **bold**."""
    text = "" if text is None else str(text)
    parts, last = [], 0
    for m in _MATH.finditer(text):
        parts.append(_inline(text[last:m.start()]))
        parts.append(math_html(m.group(1) or m.group(2), display=bool(m.group(1))))
        last = m.end()
    parts.append(_inline(text[last:]))
    return "".join(parts)


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def plain(text: Any) -> str:
    """Text for SVG labels, where MathML cannot be used."""
    s = str(text or "")
    s = re.sub(r"\$+", "", s)
    s = re.sub(r"\\(?:mathrm|text|mathbf|operatorname)\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\sqrt\{([^}]*)\}", r"√\1", s)
    greek = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "theta": "θ",
             "lambda": "λ", "mu": "μ", "sigma": "σ", "tau": "τ", "pi": "π", "omega": "ω", "eta": "η",
             "rho": "ρ", "phi": "φ", "Delta": "Δ", "Sigma": "Σ", "sum": "Σ", "cdot": "·", "times": "×"}
    s = re.sub(r"\\([A-Za-z]+)", lambda m: greek.get(m.group(1), m.group(1)), s)
    s = re.sub(r"_\{([^}]*)\}|_(\w)", lambda m: "_" + (m.group(1) or m.group(2)), s)
    return s.replace("{", "").replace("}", "")


RICH_KEYS = {"label", "title", "name", "note", "caption", "help", "row_header"}
PLAIN_KEYS = {"x_label", "y_label", "value_label"}


def _client_spec(spec: dict) -> dict:
    """The subset of the spec the browser runtime needs, with labels pre-rendered."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in RICH_KEYS and isinstance(v, str):
                    out[k] = rich(v)
                elif k in PLAIN_KEYS and isinstance(v, str):
                    out[k] = plain(v)
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    params = walk(spec["params"])
    for raw, p in zip(spec["params"], params):
        p["plain_label"] = plain(raw.get("label", raw["id"]))
    views = walk(spec["views"])
    for v in views:
        for step in v.get("steps", []) or []:
            if step.get("latex"):
                step["math"] = math_html(step["latex"])
    checks = [{"id": c["id"], "name": rich(c["name"]), **({"params": c["params"]} if c.get("params") else {})}
              for c in spec.get("checks", [])]
    return {
        "digits": spec.get("digits", 3),
        "params": params,
        "views": views,
        "checks": checks,
        "presets": [e.get("preset", {}) for e in spec.get("explorations", [])],
    }


def _fn(code: str) -> str:
    return "(" + code.strip().rstrip(";") + ")"


def _expr_fn(expr: str) -> str:
    body = expr.strip().rstrip(";")
    if re.match(r"(return|var|let|const|if|for)\b", body):
        return "function (p, r) { " + body + "; }"
    return "function (p, r) { return (" + body + "); }"


def code_bundle(spec: dict) -> str:
    """JavaScript that exposes compute, checks and custom drawings to the runtime."""
    checks = ",\n    ".join(
        f"{json.dumps(c['id'])}: {{ test: {_expr_fn(c['test'])}"
        + (f", show: {_expr_fn(c['show'])}" if c.get("show") else "") + " }"
        for c in spec.get("checks", [])
    )
    draws = ",\n    ".join(
        f"{json.dumps(v['id'])}: {_fn(v['code'])}" for v in spec["views"] if v.get("type") == "svg" and v.get("code")
    )
    js = (
        "window.__explainer = (function () {\n  \"use strict\";\n"
        f"  var compute = {_fn(spec['compute'])};\n"
        f"  var checks = {{\n    {checks}\n  }};\n"
        f"  var draw = {{\n    {draws}\n  }};\n"
        "  return { compute: compute, checks: checks, draw: draw };\n})();"
    )
    return js


def _script_safe(s: str) -> str:
    return re.sub(r"</(script)", r"<\\/\1", s, flags=re.I)


def _list(items: list, cls: str = "") -> str:
    return f"<ul class='{cls}'>" + "".join(f"<li>{rich(i)}</li>" for i in items) + "</ul>"


def render_page(spec: dict, case: dict, source_note: str) -> str:
    css = (TEMPLATES / "style.css").read_text(encoding="utf-8")
    runtime = (TEMPLATES / "runtime.js").read_text(encoding="utf-8")
    paper = spec.get("paper", {})
    src = html.escape(case["source_url"], quote=True)
    paper_line = " · ".join(html.escape(str(x)) for x in
                            (paper.get("title"), paper.get("authors"), paper.get("year")) if x)
    where = " · ".join(html.escape(str(x)) for x in (paper.get("section"), paper.get("equation")) if x)

    eqs = "".join(
        f"<figure class='equation'>{math_html(e['latex'], display=True)}"
        + (f"<figcaption>{rich(e.get('caption', ''))}</figcaption>" if e.get("caption") else "") + "</figure>"
        for e in spec.get("equations", [])
    )
    symbols = "".join(
        f"<tr><th scope='row'>{math_html(s['symbol'])}</th><td>{rich(s['meaning'])}</td></tr>"
        for s in spec.get("symbols", [])
    )
    walkthrough = "".join(f"<li>{rich(s)}</li>" for s in spec.get("walkthrough", []))
    explorations = ""
    for i, e in enumerate(spec.get("explorations", [])):
        explorations += (
            f"<article class='explore'><header><span class='num'>{i + 1}</span><h3>{rich(e['title'])}</h3>"
            f"<button class='preset' type='button' data-preset='{i}'>Load this setup</button></header>"
            f"<dl><dt>Change</dt><dd>{rich(e['change'])}</dd><dt>Observe</dt><dd>{rich(e['observe'])}</dd>"
            f"<dt>Why</dt><dd>{rich(e['why'])}</dd></dl></article>"
        )
    mis = spec.get("misconception", {})
    g = spec.get("grounding", {})
    quotes = "".join(f"<blockquote>“{rich(q)}”</blockquote>" for q in g.get("quotes", []))

    body = f"""
<header class="hero">
  <p class="kicker">Interactive explainer · for {html.escape(case['audience'])}</p>
  <h1>{rich(spec.get('title', 'Interactive explanation'))}</h1>
  <p class="paper">Source: <a href="{src}">{paper_line or src}</a>{(' — <strong>' + where + '</strong>') if where else ''}</p>
</header>

<section id="start" aria-labelledby="h-start">
  <h2 id="h-start">1 · Start here</h2>
  <p class="lead">{rich(spec.get('idea', ''))}</p>
  <div class="why"><h3>Why it matters</h3><p>{rich(spec.get('why_it_matters', ''))}</p></div>
  {eqs}
  <h3>Symbols</h3>
  <div class="scroll-x"><table class="symbols"><tbody>{symbols}</tbody></table></div>
  {('<h3>How it works, step by step</h3><ol class="walk">' + walkthrough + '</ol>') if walkthrough else ''}
</section>

<section id="lab" aria-labelledby="h-lab">
  <h2 id="h-lab">2 · Try it: change the inputs</h2>
  <p class="muted small">Every number below is computed live in your browser from the inputs you set.</p>
  <div class="lab">
    <aside class="panel" aria-label="Controls">
      <h3>Inputs</h3>
      <div id="controls"></div>
      <button id="reset" type="button" class="ghost">Reset to defaults</button>
    </aside>
    <div class="stage">
      <div id="lab-error" class="warn" role="status" hidden></div>
      <div id="views"></div>
      <div class="checks"><h3>Live checks</h3><ul id="live-checks"></ul></div>
    </div>
  </div>
</section>

<section id="guided" aria-labelledby="h-guided">
  <h2 id="h-guided">3 · Guided explorations</h2>
  <div class="explorations">{explorations}</div>
</section>

<section id="pitfall" aria-labelledby="h-pitfall">
  <h2 id="h-pitfall">4 · {rich(mis.get('title', 'A common misunderstanding'))}</h2>
  <div class="callout">{rich(mis.get('text', ''))}</div>
</section>

<section id="source" aria-labelledby="h-source">
  <h2 id="h-source">5 · Source grounding</h2>
  <p>Paper: <a href="{src}">{paper_line or src}</a>{(' — ' + where) if where else ''}.</p>
  <div class="grid2">
    <div class="ground paper-side"><h3><span class="badge paper">From the paper</span></h3>{_list(g.get('from_paper', []))}{quotes}</div>
    <div class="ground ours"><h3><span class="badge ours">Our simplifications &amp; examples</span></h3>{_list(g.get('our_simplifications', []))}</div>
  </div>
  <p class="muted small">{html.escape(source_note)} The interactive numbers are a small toy demonstration of the mechanism; they do not reproduce the paper's experimental results.</p>
</section>
"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(plain(spec.get('title', 'Interactive explanation')))}</title>
<style>
{css}
</style>
</head>
<body>
<main>
{body}
</main>
<script type="application/json" id="spec-data">{_script_safe(json.dumps(_client_spec(spec), ensure_ascii=False))}</script>
<script>
{_script_safe(code_bundle(spec))}
</script>
<script>
{runtime}
</script>
</body>
</html>
"""
