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
from pdfglyphs import page_text

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
    penalty: float = 0.0


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
        self.errors = 0
        self.tex_script: bool | None = None
        self.annotation = False
        self.math_has_alt = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if "ltx_ERROR" in (a.get("class") or ""):
            self.errors += 1
        if tag == "script" and (a.get("type") or "").startswith("math/tex") and not self.skip_depth:
            self.tex_script = "display" in (a.get("type") or "")
            self.skip_depth = 1
            return
        if self.math_depth and tag == "annotation" and "tex" in (a.get("encoding") or "").lower():
            self.annotation = True
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
            self.math_has_alt = bool(alt)
            self.math_depth = 1
        elif tag in self.SKIP:
            self.skip_depth = 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "annotation":
            self.annotation = False
        if tag == "script" and self.tex_script is not None:
            self.tex_script = None
        if self.skip_depth:
            self.skip_depth -= 1
        elif self.math_depth:
            self.math_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.tex_script is not None and self.skip_depth == 1:  # MathJax source: <script type="math/tex">
            self.parts.append(("\n$$" + data.strip() + "$$\n") if self.tex_script else (" $" + data.strip() + "$ "))
            return
        if self.annotation and not self.math_has_alt:  # MathML without alttext: use its TeX annotation
            self.parts.append(" $" + data.strip() + "$ ")
            return
        if not self.skip_depth and not self.math_depth:
            self.parts.append(data)


def _clean(text: str) -> str:
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _fix_pdf_glyphs(text: str) -> str:
    """Leftover hex glyph names (/#28 -> '(') after font-aware decoding; control characters become □.
    A '/' followed by punctuation is NOT rewritten: in most PDFs it is a real division slash."""
    text = re.sub(r"/#([2-7][0-9A-Fa-f])", lambda m: chr(int(m.group(1), 16)), text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|/#[01][0-9A-Fa-f]", "□", text)


LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}


def _repair_text(text: str) -> str:
    """Generic extraction repairs: ligatures, words hyphenated across lines, UTF-8 read as Latin-1, CID glyphs."""
    if "â€" in text or "Ã" in text:
        try:
            text = text.encode("cp1252", errors="strict").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", text)
    return re.sub(r"\(cid:\d+\)", "⟨?⟩", text)


def _strip_running_lines(pages: list[str]) -> list[str]:
    """Drop running headers/footers (a line repeated on many pages) and bare page numbers."""
    if len(pages) < 4:
        return pages
    counts: dict[str, int] = {}
    for page in pages:
        for line in {l.strip() for l in page.splitlines() if len(l.strip()) >= 4}:
            counts[line] = counts.get(line, 0) + 1
    repeated = {line for line, n in counts.items() if n >= max(3, 0.3 * len(pages))}
    return ["\n".join(l for l in page.splitlines()
                      if l.strip() not in repeated and not re.fullmatch(r"\s*\d{1,4}\s*", l)) for page in pages]


def _fix_letter_spacing(text: str) -> str:
    """Rejoin words a PDF spaced out letter by letter: 'C HANNEL' -> 'CHANNEL', 'T h e s e' -> 'These'."""
    text = re.sub(r"\b([A-Z]) ([A-Z]{2,})\b", r"\1\2", text)
    return re.sub(r"\b(?:[A-Za-z] ){3,}[A-Za-z]\b", lambda m: m.group(0).replace(" ", ""), text)


GARBLED = re.compile(r"□|⟨\?⟩|\ufffd|[\ue000-\uf8ff]|\(cid:\d+\)|/#[0-9A-Fa-f]{2}|â€")


def quality(document: SourceDocument) -> float:
    """0..1 score of how cleanly the text was extracted: unrecognized symbols, mojibake and letter-spaced runs
    lower it; LaTeX-preserving extraction (arXiv HTML) raises it."""
    text = document.text
    per_k = 1000 / max(1, len(text))
    garbled = len(GARBLED.findall(text)) * per_k
    spaced = len(re.findall(r"\b(?:[A-Za-z] ){3,}[A-Za-z]\b", text)) * per_k
    score = 1.0 - min(0.7, 0.35 * garbled) - min(0.2, 0.1 * spaced) - document.penalty
    if re.search(r"\$[^$]+\$", text):
        score += 0.1
    return round(max(0.0, min(1.0, score)), 3)


GOOD_QUALITY = 0.85


def _extract(data: bytes, content_type: str, origin: str) -> SourceDocument:
    if data.startswith(b"%PDF-") or "application/pdf" in content_type.lower():
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                reader.decrypt("")  # many "encrypted" papers only restrict editing, with an empty password
            pages = _strip_running_lines([page_text(page)[0] for page in reader.pages[:MAX_PDF_PAGES]])
            text = _clean(_repair_text(_fix_letter_spacing(_fix_pdf_glyphs("\n".join(pages)))))
        except Exception as exc:
            raise SourceError(f"Cannot extract PDF text: {exc}") from exc
        fmt = "pdf"
    else:
        try:
            parser = _PaperHTMLParser()
            parser.feed(data.decode("utf-8-sig", errors="replace"))
            text = _clean(_repair_text("".join(parser.parts)))
            penalty = min(0.5, 0.05 * parser.errors)  # arXiv HTML conversion failures: prefer the PDF
        except Exception as exc:
            raise SourceError(f"Cannot extract HTML text: {exc}") from exc
        fmt = "html"
    if len(text) < 300:
        raise SourceError("Source has too little extractable text (scanned or image-only document?)")
    return SourceDocument(text=text, format=fmt, origin=origin, penalty=penalty if fmt == "html" else 0.0)


def _candidate_urls(url: str) -> list[str]:
    """arXiv abs/pdf links get an HTML twin first (keeps equations as LaTeX)."""
    p = urlparse(url)
    if p.netloc.endswith("arxiv.org"):
        m = re.match(r"/(abs|pdf|html)/([^?#]+?)(\.pdf)?/?$", p.path)
        if m:
            ident = m.group(2)
            return [f"https://arxiv.org/html/{ident}", f"https://arxiv.org/pdf/{ident}"]
    if p.scheme == "http":
        return [url, "https://" + url[len("http://"):]]
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
    found: list[SourceDocument] = []
    for url in _candidate_urls(source_url):
        cache = _cache_path(url)
        if cache and cache.exists():
            data = cache.read_bytes()
            ctype = "application/pdf" if data.startswith(b"%PDF-") else "text/html"
            doc = _extract(data, ctype, url)
            found.append(doc)
            if quality(doc) >= GOOD_QUALITY:
                return doc
            continue
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
            found.append(doc)
            if quality(doc) >= GOOD_QUALITY:
                return doc
            errors.append(f"{url}: extraction quality {quality(doc):.2f}, trying the next version")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:160]}")
    if found:
        return max(found, key=quality)
    raise SourceError("; ".join(errors) or "fetch time budget exhausted")


_STOP = {"explain", "using", "with", "show", "paper", "section", "change", "values", "learner", "small",
         "that", "this", "their", "them", "each", "from", "into", "should", "make", "check", "guide", "through"}


_SEC_TOKEN = r"(?:[IVX]{1,5}(?![A-Za-z])|[A-Z](?:\.\d+)+|\d+(?:\.\d+)*|[A-Z](?![A-Za-z]))"
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}
_SECTION_REF = re.compile(rf"(?i:sections?|sec\.|§|appendix)\s*({_SEC_TOKEN}(?:\s*(?:,|and|&)\s*{_SEC_TOKEN})*)")
_ITEM_REF = re.compile(r"\b(algorithm|theorem|lemma|definition|proposition|corollary|figure|fig\.|table|"
                       r"equations?|eqs?\.)\s*\(?(\d+(?:\.\d+)?)\)?", re.I)


def _section_numbers(focus: str) -> list[str]:
    nums: list[str] = []
    for m in _SECTION_REF.finditer(focus):
        nums += [n for n in re.findall(_SEC_TOKEN, m.group(1)) if n not in nums]
    arabic = {v: k for k, v in _ROMAN.items()}
    for n in list(nums):  # "Section 3" may be headed "III." and "Section III" may be headed "3"
        if n in _ROMAN and str(_ROMAN[n]) not in nums:
            nums.append(str(_ROMAN[n]))
        elif n.isdigit() and int(n) in arabic and arabic[int(n)] not in nums:
            nums.append(arabic[int(n)])
    return nums


def _heading_pos(text: str, num: str, terms: set[str]) -> int | None:
    """Find a section heading such as '3.2.1 Scaled Dot-Product Attention'. Parsers do not always put headings on
    their own line ('1 IntroductionRecurrent ...'), so a heading may also start mid-line; mentions like
    'see Section 3.2.1' are ignored."""
    pattern = re.compile(r"(?<!\w)(?<!\d\.)(?:#+\s*)?(?:Appendix\s+)?" + re.escape(num) + r"\.?\s+[A-Z][A-Za-z]")
    hits = []
    for h in pattern.finditer(text):
        before = text[max(0, h.start() - 12):h.start()].lower().rstrip()
        if before.endswith(("section", "sections", "sec.", "§", "and", "in", "eq.", "table", "figure", "fig.", "(")):
            continue
        hits.append(h)
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
