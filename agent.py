"""Paper excerpt -> interactive visual explanation (out/index.html + out/trace.jsonl).

Pipeline: read case -> fetch/select source excerpt (falls back to model knowledge)
-> one LLM call that plans and writes a compact JSON spec -> deterministic checks
(structure, executed math over edge cases, grounding) -> targeted LLM repair of
failing fields only -> render with a tested generic template -> final page checks.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from llm import Budget, BudgetError, LLMError, OpenRouter, parse_json
from page import render_page
from prompts import REPAIR_SYSTEM, SYSTEM, continue_message, repair_message, user_message
from source import SourceError, load_source, select_excerpt
from validate import Report, validate

MAX_REPAIRS = 2
REPAIRABLE = ("params", "compute", "views", "explorations", "checks", "equations", "symbols", "grounding",
              "misconception")
REQUIRED = ("title", "idea", "why_it_matters", "equations", "symbols", "params", "compute", "views", "explorations",
            "misconception", "checks", "grounding")


class CaseError(ValueError):
    pass


def load_case(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaseError(f"Cannot read input JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CaseError("Input must be a JSON object")
    case = {}
    for name in ("source_url", "focus", "audience"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise CaseError(f"{name} must be a nonempty string")
        case[name] = value.strip()
    return case


class Trace:
    """Append-only JSONL log: stage, action, result, elapsed seconds, details. Never logs secrets."""

    def __init__(self, path: Path, started: float) -> None:
        self.path = path
        self.started = started
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def record(self, stage: str, action: str, result: str, **details: Any) -> None:
        event = {"stage": stage, "action": action, "result": result,
                 "elapsed_seconds": round(time.monotonic() - self.started, 3), **details}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def load_dotenv(path: Path = Path(__file__).resolve().parent / ".env") -> None:
    """Development convenience: read OPENROUTER_API_KEY from .env if not already set."""
    if os.environ.get("OPENROUTER_API_KEY") or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip("'\"")


def report_dict(rep: Report) -> dict:
    return {"errors": rep.errors, "warnings": rep.warnings, "auto_fixes": rep.fixes, "stats": rep.stats}


def check_html(html: str) -> list[str]:
    """Final artifact checks: self-contained, required sections present."""
    problems = []
    for m in re.finditer(r"<(script|link|img|iframe|source|video|audio)\b[^>]*\b(src|href)\s*=\s*['\"]?(https?:)?//", html, re.I):
        problems.append(f"external resource: {m.group(0)[:80]}")
    if re.search(r"@import|url\(\s*['\"]?https?:", html):
        problems.append("external CSS resource")
    for sid in ("start", "lab", "guided", "pitfall", "source"):
        if f'id="{sid}"' not in html:
            problems.append(f"missing section #{sid}")
    if html.count("data-preset=") != 2:
        problems.append("expected two guided-exploration presets")
    return problems


def debug_dump(trace: Trace, name: str, text: str) -> None:
    """Development only (EXPLAINER_DEBUG=1): keep raw completions next to the trace."""
    if os.environ.get("EXPLAINER_DEBUG"):
        (trace.path.parent / f"debug_{name}.json").write_text(text, encoding="utf-8")


def generate(client: OpenRouter, trace: Trace, case: dict, excerpt: str | None, source_note: str) -> dict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_message(case, excerpt, source_note)}]
    for attempt in (1, 2):
        text = client.chat("generate", messages, max_tokens=14_000)
        debug_dump(trace, f"generate{attempt}", text)
        try:
            spec = parse_json(text)
            trace.record("plan", "model plan (first field of spec)", "ok", plan=spec.get("plan"))
            trace.record("generate", "parse spec", "ok", fields=sorted(spec.keys()), chars=len(text))
            missing = [k for k in REQUIRED if not spec.get(k)]
            if missing and "compute" in spec and "params" in spec:
                follow = messages + [{"role": "assistant", "content": text},
                                     {"role": "user", "content": continue_message(missing)}]
                try:
                    more = parse_json(client.chat("generate", follow, max_tokens=8_000))
                    spec.update({k: v for k, v in more.items() if k in missing})
                    trace.record("generate", "complete missing fields", "ok", missing=missing,
                                 added=[k for k in missing if more.get(k)])
                except (BudgetError, LLMError, ValueError, json.JSONDecodeError) as exc:
                    trace.record("generate", "complete missing fields", "failed", missing=missing, error=str(exc)[:200])
            return spec
        except (ValueError, json.JSONDecodeError) as exc:
            trace.record("generate", "parse spec", "failed", attempt=attempt, error=str(exc)[:200], chars=len(text))
            messages = messages[:2] + [{"role": "user", "content": "Your previous output was not one valid, complete JSON "
                                        "object. Return it again, shorter, as strict JSON."}]
    raise LLMError("model did not return valid JSON")


def repair(client: OpenRouter, trace: Trace, spec: dict, rep: Report, round_no: int) -> dict:
    messages = [{"role": "system", "content": REPAIR_SYSTEM},
                {"role": "user", "content": repair_message(spec, rep.errors)}]
    text = client.chat("revise", messages, max_tokens=8_000)
    debug_dump(trace, f"repair{round_no}", text)
    patch = parse_json(text)
    changed = [k for k in patch if k in REPAIRABLE]
    fixed = copy.deepcopy(spec)
    for k in changed:
        fixed[k] = patch[k]
    trace.record("revise", f"apply repair round {round_no}", "ok" if changed else "no_change", changed_fields=changed)
    return fixed


def severity(rep: Report) -> int:
    return sum(10 if e.startswith(("missing or invalid field", "JavaScript does not load", "param ")) else 1
               for e in rep.errors)


def auto_views(spec: dict, excerpt: str | None) -> None:
    """Last resort when the model gave no usable views: show every computed quantity generically."""
    try:
        from validate import MiniRacer, HARNESS
        from page import code_bundle
        ctx = MiniRacer()
        ctx.eval(HARNESS % json.dumps({"params": spec["params"]}) + "\n"
                 + code_bundle({**spec, "views": [], "checks": []}).replace("window.__explainer", "var __explainer"))
        r = json.loads(ctx.call("resultOf", {}))
    except Exception:
        return
    scalars = [k for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    vectors = [k for k, v in r.items() if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v)]
    matrices = [k for k, v in r.items() if isinstance(v, list) and v and all(isinstance(x, list) for x in v)]
    views = []
    if scalars:
        views.append({"type": "readout", "title": "Computed quantities", "items": [{"label": k, "key": k} for k in scalars[:8]]})
    views += [{"type": "bars", "title": k, "series": [{"key": k, "label": k}]} for k in vectors[:3]]
    views += [{"type": "heatmap", "title": k, "value": k} for k in matrices[:3]]
    spec["views"] = views


def degrade(spec: dict, rep: Report) -> dict:
    """Last resort without more model calls: drop checks and views that fail, keep the page usable."""
    spec = copy.deepcopy(spec)
    failing_checks = {m.group(1) for e in rep.errors for m in [re.match(r"check '(.+?)' \(", e)] if m}
    if failing_checks:
        spec["checks"] = [c for c in spec.get("checks", []) if c.get("name") not in failing_checks]
    failing_views = {m.group(1) for e in rep.errors for m in [re.match(r"view '(.+?)' \(", e)] if m}
    if failing_views:
        spec["views"] = [v for v in spec["views"] if v.get("title") not in failing_views]
    if isinstance(spec.get("explorations"), list):
        good = [e for e in spec["explorations"] if isinstance(e, dict) and isinstance(e.get("preset"), dict)
                and all(isinstance(e.get(k), str) for k in ("title", "change", "observe", "why"))]
        spec["explorations"] = (good or spec["explorations"])[:2]
    if not isinstance(spec.get("views"), list) or not spec["views"]:
        auto_views(spec, None)
    for k, empty in (("explorations", []), ("grounding", {}), ("symbols", []), ("equations", []),
                     ("misconception", {}), ("checks", [])):
        if not isinstance(spec.get(k), type(empty)):
            spec[k] = empty
    return spec


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    args = parse_args(argv)
    trace = Trace(args.output / "trace.jsonl", started)
    budget = Budget(started=started)
    try:
        case = load_case(args.input)
        trace.record("input", "validate case", "ok", **case)
        load_dotenv()

        # 1. Source: fetch quickly; the assessment network may only allow OpenRouter.
        excerpt, source_note = None, ""
        try:
            doc = load_source(case["source_url"])
            excerpt = select_excerpt(doc, case["focus"])
            source_note = f"Explanation grounded in text extracted from the source ({doc.format})."
            trace.record("source", "fetch and select excerpt", "ok", origin=doc.origin, format=doc.format,
                         document_chars=len(doc.text), excerpt_chars=len(excerpt))
        except SourceError as exc:
            source_note = ("The source could not be downloaded during generation, so this explanation relies on "
                           "the brief and the model's knowledge of the paper; verify details against the original.")
            trace.record("source", "fetch and select excerpt", "unavailable", error=str(exc)[:300],
                         fallback="model knowledge + focus brief")

        # 2. Plan + generate the spec in one call.
        client = OpenRouter(args.model, budget, trace.record)
        spec = generate(client, trace, case, excerpt, source_note)

        # 3. Check, then repair only what failed.
        rep = validate(spec, excerpt)
        trace.record("check", "validate spec (round 0)", "pass" if rep.ok else "fail", **report_dict(rep))
        rounds = 0
        while not rep.ok and rounds < MAX_REPAIRS:
            rounds += 1
            try:
                candidate = repair(client, trace, spec, rep, rounds)
            except (BudgetError, LLMError, ValueError, json.JSONDecodeError) as exc:
                trace.record("revise", f"repair round {rounds}", "failed", error=str(exc)[:300])
                break
            new_rep = validate(candidate, excerpt)
            trace.record("check", f"validate spec (round {rounds})", "pass" if new_rep.ok else "fail", **report_dict(new_rep))
            if severity(new_rep) <= severity(rep):
                spec, rep = candidate, new_rep
            else:
                trace.record("revise", f"repair round {rounds}", "rejected", reason="repair introduced more errors")
        if not rep.ok:
            spec = degrade(spec, rep)
            rep = validate(spec, excerpt)
            trace.record("check", "validate after dropping failing checks/views", "pass" if rep.ok else "fail",
                         **report_dict(rep))
            structural = [e for e in rep.errors if e.startswith(("missing or invalid field 'compute'",
                          "missing or invalid field 'params'", "JavaScript does not load"))]
            if structural:
                raise LLMError("spec still invalid: " + "; ".join(structural[:3]))

        # 4. Render and check the final artifact.
        html = render_page(spec, case, source_note)
        problems = check_html(html)
        trace.record("render", "write index.html and check artifact", "pass" if not problems else "fail",
                     bytes=len(html.encode()), problems=problems)
        (args.output / "index.html").write_text(html, encoding="utf-8")
        (args.output / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        status = "ok" if rep.ok and not problems else "completed_with_failures"
        trace.record("run", "finish", status, requests=budget.requests, prompt_tokens=budget.prompt_tokens,
                     completion_tokens=budget.completion_tokens,
                     total_tokens=budget.prompt_tokens + budget.completion_tokens,
                     repairs=rounds, remaining_errors=rep.errors, seconds=round(time.monotonic() - started, 2))
        return 0
    except Exception as exc:  # any failure must still leave a trace and a nonzero exit
        trace.record("run", "finish", "failed", error=str(exc)[:500], requests=budget.requests,
                     prompt_tokens=budget.prompt_tokens, completion_tokens=budget.completion_tokens)
        print(f"agent.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
