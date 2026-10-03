"""Recover math symbols that PDF text extraction leaves as glyph names (e.g. '/#19', '/,').

TeX-produced PDFs often embed math fonts as Type 3 fonts without Unicode maps. Their character
codes still follow TeX's standard font layouts, so a font can be classified from the codes it uses
(OML: math italic and Greek; OMS: math symbols; otherwise text) and each code decoded from that table.
A token used with conflicting meanings on one page is left marked rather than guessed.
"""

from __future__ import annotations

import re
from typing import Any

OML = {i: c for i, c in enumerate("ΓΔΘΛΞΠΣΥΦΨΩαβγδϵζηθικλμνξπρστυϕχψωεϑϖϱςφ")}
OML.update({0x3A: ".", 0x3B: ",", 0x3C: "<", 0x3D: "/", 0x3E: ">", 0x3F: "⋆", 0x40: "∂", 0x60: "ℓ"})
OMS = dict(enumerate("−·×∗÷⋄±∓⊕⊖⊗⊘⊙◯∘•≍≡⊆⊇≤≥⪯⪰∼≈⊂⊃≪≫≺≻←→↑↓↔↗↘≃⇐⇒⇑⇓⇔↖↙∝′∞∈∋△▽/"))
OMS.update({0x38: "∀", 0x39: "∃", 0x3A: "¬", 0x3B: "∅", 0x3C: "ℜ", 0x3D: "ℑ", 0x3E: "⊤", 0x3F: "⊥", 0x40: "ℵ",
            0x5B: "∪", 0x5C: "∩", 0x5D: "⊎", 0x5E: "∧", 0x5F: "∨", 0x66: "{", 0x67: "}", 0x6A: "|", 0x6B: "‖",
            0x70: "√", 0x72: "∇", 0x73: "∫"})
OMS_SIGNATURE = {0x00, 0x01, 0x02, 0x03, 0x06, 0x14, 0x15, 0x18, 0x19, 0x1C, 0x1D, 0x20, 0x21, 0x31, 0x32, 0x70}
LITERAL = "\x00literal"  # marks a '/x' that is ordinary text, not a glyph name
GLYPH = re.compile(r"/(#[0-9A-Fa-f]{2}|[^\s/#A-Za-z0-9])")


def _differences(font: Any) -> dict[str, int]:
    enc = font.get("/Encoding")
    enc = enc.get_object() if hasattr(enc, "get_object") else enc
    diffs = enc.get("/Differences") if hasattr(enc, "get") else None
    names, code = {}, 0
    for item in diffs or []:
        if isinstance(item, int):
            code = item
        else:
            names[str(item)] = code
            code += 1
    return names


def _table(codes: set[int]) -> dict[int, str] | None:
    if not codes or min(codes) >= 0x20:
        return None  # text font: the code is the character
    oms = len(codes & OMS_SIGNATURE)
    oml = len({c for c in codes if 0x0B <= c <= 0x27} | (codes & {0x3A, 0x3B, 0x3C, 0x3E}))
    return OMS if oms >= oml else OML


def page_text(page: Any) -> tuple[str, int, int]:
    """Text of one page with glyph-name tokens decoded by the font each occurrence came from.
    Returns (text, decoded, unresolved). Occurrences are matched in reading order; if the counts disagree,
    a token falls back to its meaning on the page when that meaning is unique, otherwise it is marked."""
    occurrences: dict[str, list[str | None]] = {}

    def visit(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        if "/" not in (text or ""):
            return
        names = _differences(font) if font else {}
        table = _table(set(names.values())) if names else None
        for m in GLYPH.finditer(text):
            code = names.get("/" + m.group(1))
            if code is None:  # the font defines no such glyph name: this is a real slash, e.g. m/(1-b)
                occurrences.setdefault(m.group(0), []).append(LITERAL)
                continue
            char = table.get(code) if table else (chr(code) if 0x20 <= code < 0x7F else None)
            occurrences.setdefault(m.group(0), []).append(char)

    text = page.extract_text(visitor_text=visit) or ""
    counts = {t: len(re.findall(re.escape(t) + r"(?![0-9A-Fa-f])" if t.startswith("/#") else re.escape(t), text))
              for t in occurrences}
    seen: dict[str, int] = {}
    decoded = unresolved = 0

    def replace(m: re.Match) -> str:
        nonlocal decoded, unresolved
        token = m.group(0)
        options = occurrences.get(token)
        if not options:
            return token
        k = seen.get(token, 0)
        seen[token] = k + 1
        char = options[k] if counts.get(token) == len(options) and k < len(options) else None
        if char is None:
            unique = {o for o in options if o}
            char = next(iter(unique)) if len(unique) == 1 else None
        if char == LITERAL:
            return token
        if char:
            decoded += 1
            return char
        unresolved += 1
        return "⟨?⟩"

    return GLYPH.sub(replace, text), decoded, unresolved
