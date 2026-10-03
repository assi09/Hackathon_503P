"""Fetch the paper (HTML or PDF) quickly and select the excerpt the brief asks about.

During assessment only OpenRouter may be reachable, so every network step has
short timeouts and the caller falls back to model knowledge on failure.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

from guard import DeadlineExceeded, run_with_deadline

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_EXCERPT_CHARS = 9_000
FETCH_BUDGET_SECONDS = 6.0
MAX_PDF_PAGES = 150


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDocument:
    text: str
    format: str
    origin: str


class _PaperHTMLParser(HTMLParser):
    """Text extractor that keeps equations as $LaTeX$ using MathML alttext (arXiv HTML)."""

    SKIP = {"script", "style", "nav", "header", "footer", "svg", "noscript", "button", "form"}
    BREAK = {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
             "table", "figure", "figcaption", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.math_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_depth:
            if tag not in ("br", "img", "hr", "input", "meta", "link"):
                self.skip_depth += 1
            return
        if self.math_depth:
            self.math_depth += 1
            return
        if tag == "math":
            alt = dict(attrs).get("alttext")
            display = dict(attrs).get("display") == "block"
            if alt:
                self.parts.append(("\n$$" + alt + "$$\n") if display else (" $" + alt + "$ "))
            self.math_depth = 1
        elif tag in self.SKIP:
            self.skip_depth = 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
        elif self.math_depth:
            self.math_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and not self.math_depth:
            self.parts.append(data)


def _clean(text: str) -> str:
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _fix_pdf_glyphs(text: str) -> str:
    """Some PDFs expose glyph names such as /#28 or /; instead of the characters."""
    text = re.sub(r"/#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace("/!", "→")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "□", text)  # glyphs lost in extraction
    return re.sub(r"/([=;:,.()<>+\-])", lambda m: {";": ",", ":": "."}.get(m.group(1), m.group(1)), text)


def _extract(data: bytes, content_type: str, origin: str) -> SourceDocument:
    if data.startswith(b"%PDF-") or "application/pdf" in content_type.lower():
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = reader.pages[:MAX_PDF_PAGES]
            text = _clean(_fix_pdf_glyphs("\n".join(page.extract_text() or "" for page in pages)))
        except Exception as exc:
            raise SourceError(f"Cannot extract PDF text: {exc}") from exc
        fmt = "pdf"
    else:
        try:
            parser = _PaperHTMLParser()
            parser.feed(data.decode("utf-8-sig", errors="replace"))
            text = _clean("".join(parser.parts))
        except Exception as exc:
            raise SourceError(f"Cannot extract HTML text: {exc}") from exc
        fmt = "html"
    if len(text) < 300:
        raise SourceError("Source has too little extractable text")
    return SourceDocument(text=text, format=fmt, origin=origin)


def _candidate_urls(url: str) -> list[str]:
    """arXiv abs/pdf links get an HTML twin first (keeps equations as LaTeX)."""
    p = urlparse(url)
    if p.netloc.endswith("arxiv.org"):
        m = re.match(r"/(abs|pdf|html)/([^?#]+?)(\.pdf)?/?$", p.path)
        if m:
            ident = m.group(2)
            return [f"https://arxiv.org/html/{ident}", f"https://arxiv.org/pdf/{ident}"]
    return [url]


def _cache_path(url: str) -> Path | None:
    """Development-only cache, enabled only when PAPER_CACHE_DIR is set."""
    root = os.environ.get("PAPER_CACHE_DIR")
    if not root:
        return None
    return Path(root) / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".bin")


def load_source(source_url: str) -> SourceDocument:
    """Fetch and extract within FETCH_BUDGET_SECONDS of wall-clock time, whatever the server does."""
    try:
        return run_with_deadline(_load_source, FETCH_BUDGET_SECONDS, source_url)
    except DeadlineExceeded as exc:
        raise SourceError(f"source fetch/extraction exceeded {FETCH_BUDGET_SECONDS:.0f}s") from exc


def _load_source(source_url: str) -> SourceDocument:
    deadline = time.monotonic() + FETCH_BUDGET_SECONDS
    errors: list[str] = []
    for url in _candidate_urls(source_url):
        cache = _cache_path(url)
        if cache and cache.exists():
            data = cache.read_bytes()
            ctype = "application/pdf" if data.startswith(b"%PDF-") else "text/html"
            return _extract(data, ctype, url)
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        try:
            if urlparse(url).scheme not in ("http", "https"):
                raise SourceError("only http(s) sources are fetched")
            with requests.get(url, headers={"User-Agent": "Mozilla/5.0 (paper-explainer-agent)"},
                              timeout=(min(3.0, remaining), remaining), stream=True) as resp:
                resp.raise_for_status()
                data = resp.raw.read(MAX_SOURCE_BYTES + 1, decode_content=True)
                ctype = resp.headers.get("Content-Type", "")
            if len(data) > MAX_SOURCE_BYTES:
                raise SourceError("source exceeds size limit")
            doc = _extract(data, ctype, url)
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
            return doc
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:160]}")
    raise SourceError("; ".join(errors) or "fetch time budget exhausted")


_STOP = {"explain", "using", "with", "show", "paper", "section", "change", "values", "learner", "small",
         "that", "this", "their", "them", "each", "from", "into", "should", "make", "check", "guide", "through"}


_SEC_TOKEN = r"(?:[A-Z](?:\.\d+)+|\d+(?:\.\d+)*|[A-Z](?![A-Za-z]))"
_SECTION_REF = re.compile(rf"(?i:sections?|sec\.|§|appendix)\s*({_SEC_TOKEN}(?:\s*(?:,|and|&)\s*{_SEC_TOKEN})*)")
_ITEM_REF = re.compile(r"\b(algorithm|theorem|lemma|definition|proposition|corollary|figure|fig\.|table|"
                       r"equations?|eqs?\.)\s*\(?(\d+(?:\.\d+)?)\)?", re.I)


def _section_numbers(focus: str) -> list[str]:
    nums: list[str] = []
    for m in _SECTION_REF.finditer(focus):
        nums += [n for n in re.findall(_SEC_TOKEN, m.group(1)) if n not in nums]
    return nums


def _heading_pos(text: str, num: str, terms: set[str]) -> int | None:
    pattern = re.compile(rf"^(?:Appendix\s+)?{re.escape(num)}\.?\s+[A-Z][^\n]{{1,90}}$", re.M)
    hits = [h for h in pattern.finditer(text) if len(h.group(0)) < 110]
    if not hits:
        return None
    # A table of contents also lists the heading; prefer the hit followed by focus-relevant prose.
    return max(hits, key=lambda h: sum(text[h.start():h.start() + 3000].lower().count(t) for t in terms)).start()


def _section_start(text: str, focus: str) -> int | None:
    terms = focus_terms(focus)
    for num in _section_numbers(focus):
        pos = _heading_pos(text, num, terms)
        if pos is not None:
            return pos
    return None


def _item_windows(text: str, focus: str) -> list[tuple[int, int]]:
    """Text around every item the focus names: extra sections, equations, algorithms, theorems, figures, tables.
    In PDFs such boxes often sit away from the section heading (e.g. an algorithm printed above it)."""
    windows: list[tuple[int, int]] = []
    terms = focus_terms(focus)
    for num in _section_numbers(focus)[1:]:
        pos = _heading_pos(text, num, terms)
        if pos is not None:
            windows.append((pos, pos + 1800))
    for kind, num in _ITEM_REF.findall(focus):
        k = kind.lower()
        if k.startswith("eq"):  # equation tags such as "(5)" closing a display line
            m = re.search(rf"\(\s*{re.escape(num)}\s*\)\s*$", text, re.M)
            if m:
                windows.append((max(0, m.start() - 700), m.end() + 300))
            continue
        label = "fig(?:ure|\\.)" if k.startswith("fig") else re.escape(k)
        m = (re.search(rf"\b{label}\s*{re.escape(num)}\s*[:.]", text, re.I)  # caption / box title first
             or re.search(rf"\b{label}\s*{re.escape(num)}\b", text, re.I))
        if m:
            windows.append((max(0, m.start() - 150), m.start() + 1500))
    return windows


def focus_terms(focus: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", focus)} - _STOP


def relevance(document: SourceDocument, focus: str) -> float:
    """Share of the focus's key terms found in the document. A login wall, cookie notice or wrong
    page scores low, so the agent does not present unrelated text as the paper."""
    terms = focus_terms(focus)
    if not terms:
        return 1.0
    text = document.text.lower()
    return sum(t in text for t in terms) / len(terms)


def select_excerpt(document: SourceDocument, focus: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    """Paper opening (title) + the section named in the focus + any items it names (equations, algorithms,
    theorems, figures, tables, extra sections); without a named section, the most focus-relevant passages."""
    text = document.text
    if len(text) <= limit:
        return text
    head = text[:700]
    start = _section_start(text, focus)
    items = sorted(_item_windows(text, focus))
    if start is not None:
        body_start = max(0, start - 300)
        body_len = limit - len(head) - 20
        extra: list[tuple[int, int]] = []
        for s_, e_ in items:  # items already inside the section body need no extra room
            if s_ >= body_start and e_ <= body_start + body_len:
                continue
            if sum(e - s for s, e in extra) + (e_ - s_) > 3500 or (extra and s_ < extra[-1][1]):
                continue
            extra.append((s_, e_))
            body_len -= (e_ - s_) + 8
        parts = [(body_start, body_start + max(2000, body_len))] + extra
        return (head + "".join("\n[...]\n" + text[a:b] for a, b in sorted(parts)))[:limit]

    terms = focus_terms(focus)
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    scored = sorted(((sum(t in line.lower() for t in terms), i) for i, line in enumerate(lines)), reverse=True)
    used: list[tuple[int, int]] = []
    budget = limit - len(head)
    for s_, e_ in items[:3]:
        if e_ - s_ + 8 <= budget and not any(s_ < ue and e_ > us for us, ue in used):
            used.append((s_, e_))
            budget -= e_ - s_ + 8
    for score, i in scored:
        if score == 0 or budget < 800:
            break
        s_, e_ = max(0, offsets[i] - 600), min(len(text), offsets[i] + 1600)
        if any(s_ < ue and e_ > us for us, ue in used) or e_ - s_ + 8 > budget:
            continue
        used.append((s_, e_))
        budget -= e_ - s_ + 8
    return (head + "".join("\n[...]\n" + text[s_:e_] for s_, e_ in sorted(used)))[:limit]
