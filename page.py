"""Render a structured explanation as a self-contained browser page."""

from __future__ import annotations

import html
from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    name: str
    meaning: str


@dataclass(frozen=True)
class Exploration:
    change: str
    observe: str
    why: str


@dataclass(frozen=True)
class PageSpec:
    title: str
    idea: str
    why_it_matters: str
    symbols: tuple[Symbol, ...]
    visual_html: str
    controls_html: str
    calculation_js: str
    explorations: tuple[Exploration, Exploration]
    limitation: str
    source_url: str
    source_location: str
    source_support: str
    simplification: str
    extra_css: str = ""


def _text(value: str) -> str:
    return html.escape(value, quote=True)


def render_page(spec: PageSpec) -> str:
    if len(spec.explorations) != 2:
        raise ValueError("Exactly two guided explorations are required")
    symbols = "".join(
        f"<div class='symbol'><dt>{_text(item.name)}</dt><dd>{_text(item.meaning)}</dd></div>"
        for item in spec.symbols
    )
    explorations = "".join(
        "<li><strong>Change:</strong> " + _text(item.change)
        + " <strong>Observe:</strong> " + _text(item.observe)
        + " <strong>Why:</strong> " + _text(item.why) + "</li>"
        for item in spec.explorations
    )
    source_url = _text(spec.source_url)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_text(spec.title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; background: #f5f7f8; color: #18232a; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; line-height: 1.5; }}
    main {{ max-width: 1100px; margin: auto; padding: 24px; }}
    h1, h2 {{ line-height: 1.2; }}
    h1 {{ font-size: clamp(1.8rem, 3vw, 2.5rem); margin: 0 0 12px; }}
    h2 {{ font-size: 1.3rem; margin: 0 0 12px; }}
    p {{ max-width: 76ch; }}
    section {{ border-top: 1px solid #ccd5d9; padding: 24px 0; }}
    .workbench {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, 300px); gap: 24px; }}
    .visual, .controls {{ min-width: 0; }}
    .visual {{ background: white; border: 1px solid #ccd5d9; padding: 20px; min-height: 240px; }}
    .controls label {{ display: block; margin-bottom: 16px; }}
    input, select, button {{ font: inherit; }}
    input[type=range] {{ width: 100%; }}
    dl {{ display: grid; gap: 8px; }}
    .symbol {{ display: grid; grid-template-columns: minmax(70px, 120px) 1fr; gap: 16px; }}
    dt {{ font-weight: 700; }} dd {{ margin: 0; }}
    li {{ margin-bottom: 12px; }}
    .source {{ overflow-wrap: anywhere; }}
    @media (max-width: 700px) {{ .workbench {{ grid-template-columns: 1fr; }} main {{ padding: 16px; }} }}
    {spec.extra_css}
  </style>
</head>
<body>
<main>
  <header><h1>{_text(spec.title)}</h1><p>{_text(spec.idea)}</p><p>{_text(spec.why_it_matters)}</p></header>
  <section aria-labelledby="symbols-heading"><h2 id="symbols-heading">Symbols</h2><dl>{symbols}</dl></section>
  <section aria-labelledby="explore-heading"><h2 id="explore-heading">Explore the mechanism</h2>
    <div class="workbench"><div class="visual" id="visual">{spec.visual_html}</div>
    <div class="controls" id="controls">{spec.controls_html}</div></div>
  </section>
  <section aria-labelledby="guided-heading"><h2 id="guided-heading">Try these</h2><ol>{explorations}</ol></section>
  <section aria-labelledby="limits-heading"><h2 id="limits-heading">Scope and source</h2>
    <p><strong>Limitation:</strong> {_text(spec.limitation)}</p>
    <p><strong>Supported by the paper:</strong> {_text(spec.source_support)}</p>
    <p><strong>Simplified here:</strong> {_text(spec.simplification)}</p>
    <p class="source"><a href="{source_url}">{source_url}</a> · {_text(spec.source_location)}</p>
  </section>
</main>
<script>{spec.calculation_js}</script>
</body>
</html>
"""
