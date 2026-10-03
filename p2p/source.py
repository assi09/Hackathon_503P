"""Read assessment input without requiring network access to the source paper."""
import hashlib
import json
import re
from urllib.parse import urlparse

EXCERPT_KEYS = ("excerpt", "source_excerpt", "paper_excerpt", "source_text", "paper_text", "text", "content", "context")


def load_case(path):
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("case.json must be a JSON object")
    for key in ("source_url", "focus", "audience"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"case.json needs a nonempty string field: {key}")
    if urlparse(raw["source_url"]).scheme not in {"http", "https"}:
        raise ValueError("source_url must be an HTTP(S) URL")
    excerpt, field = "", ""
    for key in EXCERPT_KEYS:
        if isinstance(raw.get(key), str) and raw[key].strip():
            excerpt, field = raw[key].strip(), key
            break
    if not excerpt:
        candidates = [(k, v) for k, v in raw.items() if k not in {"source_url", "focus", "audience", "title", "paper_title", "section"}
                      and isinstance(v, str) and len(v.strip()) > 180]
        if len(candidates) == 1:
            field, excerpt = candidates[0]
    if not excerpt:
        raise ValueError("No paper excerpt supplied. Assessment allows only OpenRouter network access. "
                         "Include excerpt/source_excerpt in case.json; ask the instructor to clarify the omitted fields.")
    if len(excerpt) > 100000:
        raise ValueError("Excerpt exceeds 100,000 characters; supply the focused section requested by the brief")
    section = raw.get("section", raw.get("source_section", ""))
    if not section:
        # Only recognize an explicit heading or a labeled excerpt attribution,
        # not an arbitrary cross-reference buried inside the paper text.
        match = re.search(r"(?:^|paraphrased from |excerpt from )((?:section)\s+\d+(?:\.\d+)*)(?:,?\s*(Equation\s*\(\d+\)))?", excerpt[:300], re.I)
        if match:
            section = ", ".join(x for x in match.groups() if x)
    case = {"source_url": raw["source_url"], "focus": raw["focus"], "audience": raw["audience"],
            "title": raw.get("paper_title", raw.get("title", "")),
            "section": section, "excerpt": excerpt}
    evidence = {"source_url": case["source_url"], "excerpt_field": field,
                "excerpt_characters": len(excerpt), "sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                "provenance": "supplied input; no source-paper network fetch", "explicit_locator": section}
    return case, evidence
