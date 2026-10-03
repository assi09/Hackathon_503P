"""Read a paper from its URL or an optional local PDF during development."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from pypdf import PdfReader


MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_EXCERPT_CHARS = 24_000


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDocument:
    text: str
    format: str
    origin: str


class _PaperHTMLParser(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "svg", "noscript"}
    BREAK = {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "tr"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_depth:
            if tag not in self.VOID:
                self.skip_depth += 1
        elif tag in self.SKIP:
            self.skip_depth = 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth and tag not in self.VOID:
            self.skip_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.skip_depth and tag in self.BREAK:
            self.parts.append("\n")


def _clean(text: str) -> str:
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _extract(data: bytes, content_type: str, origin: str) -> SourceDocument:
    if data.startswith(b"%PDF-") or "application/pdf" in content_type.lower():
        try:
            reader = PdfReader(io.BytesIO(data))
            text = _clean("\n".join(page.extract_text() or "" for page in reader.pages))
        except Exception as exc:
            raise SourceError(f"Cannot extract PDF text: {exc}") from exc
        fmt = "pdf"
    else:
        try:
            decoded = data.decode("utf-8-sig", errors="replace")
            parser = _PaperHTMLParser()
            parser.feed(decoded)
            text = _clean("".join(parser.parts))
        except Exception as exc:
            raise SourceError(f"Cannot extract HTML text: {exc}") from exc
        fmt = "html"
    if len(text) < 100:
        raise SourceError("Source has too little extractable text")
    return SourceDocument(text=text, format=fmt, origin=origin)


def load_source(source_url: str, local_pdf: Path | None = None) -> SourceDocument:
    if local_pdf is not None:
        try:
            data = local_pdf.read_bytes()
        except OSError as exc:
            raise SourceError(f"Cannot read local PDF: {exc}") from exc
        if len(data) > MAX_SOURCE_BYTES:
            raise SourceError("Local PDF exceeds the source size limit")
        if not data.startswith(b"%PDF-"):
            raise SourceError("Local source must be a PDF")
        return _extract(data, "application/pdf", str(local_pdf))

    request = Request(source_url, headers={"User-Agent": "PaperExplainer/0.1"})
    try:
        with urlopen(request, timeout=20) as response:
            data = response.read(MAX_SOURCE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except Exception as exc:
        raise SourceError(f"Cannot fetch source URL: {exc}") from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise SourceError("Remote source exceeds the source size limit")
    return _extract(data, content_type, source_url)


def select_excerpt(document: SourceDocument, focus: str) -> str:
    """Keep the start and the most focus-relevant lines within a prompt budget."""
    text = document.text
    if len(text) <= MAX_EXCERPT_CHARS:
        return text

    terms = {term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", focus)}
    terms -= {"explain", "using", "with", "show", "paper", "section", "change", "values"}
    lines = text.splitlines(keepends=True)
    scored: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lower = line.lower()
        score = sum(1 for term in terms if term in lower)
        if score:
            scored.append((score, index))
    scored.sort(reverse=True)

    selected = text[:4000]
    remaining = MAX_EXCERPT_CHARS - len(selected)
    positions: set[int] = set()
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    for _, index in scored:
        start = max(0, offsets[index] - 900)
        end = min(len(text), offsets[index] + len(lines[index]) + 1800)
        if any(abs(start - prior) < 1800 for prior in positions):
            continue
        chunk = text[start:end]
        if len(chunk) + 2 > remaining:
            continue
        selected += "\n\n" + chunk
        positions.add(start)
        remaining -= len(chunk) + 2
        if remaining < 2000:
            break
    return selected[:MAX_EXCERPT_CHARS]
