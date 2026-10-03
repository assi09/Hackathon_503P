"""Development only: print a rubric-oriented scorecard for one generated output folder.

Usage: python tools/scorecard.py out            (add --no-browser to skip the Chromium test)
Maps the brief's requirements and rubric to evidence found in out/index.html,
out/spec.json and out/trace.jsonl. It is a self-check, not the official grader.
"""

import json
import re
import sys
from pathlib import Path

OK, BAD, WARN = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"
LIMITS = {"seconds": 600, "requests": 10, "completion_tokens": 30_000}


def line(ok, label, detail=""):
    mark = OK if ok is True else BAD if ok is False else WARN
    print(f"  {mark} {label:<44} {detail}")


def main(out: Path, browser: bool) -> None:
    events = [json.loads(l) for l in (out / "trace.jsonl").read_text().splitlines()]
    fin = events[-1]
    calls = [e for e in events if e["action"] == "llm_call"]
    checks = [e for e in events if e["stage"] == "check"]
    source = next((e for e in events if e["stage"] == "source"), {})
    spec = json.loads((out / "spec.json").read_text()) if (out / "spec.json").exists() else {}
    html = (out / "index.html").read_text() if (out / "index.html").exists() else ""

    print(f"\n== RUN  ({out})")
    line(fin["result"] == "ok", "finished", f"result={fin['result']}")
    line(bool(html), "out/index.html written", f"{len(html.encode()) // 1024} KB" if html else "missing")
    secs = fin.get("seconds") or fin.get("elapsed_seconds")
    comp = sum(c["completion_tokens"] for c in calls)
    total = sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls)
    line(secs < LIMITS["seconds"], "time (limit 600 s)", f"{secs:.1f} s   (our typical: ~30 s)")
    line(len(calls) <= LIMITS["requests"], "API requests (limit 10)", f"{len(calls)}")
    line(comp <= LIMITS["completion_tokens"], "completion tokens (limit 30k)", f"{comp}")
    line(True, "TOTAL tokens (scored: fewer is better)", f"{total}   (prompt {total - comp} + completion {comp}; our typical ~12k)")
    line(all(c.get("generation_id") for c in calls), "usage verifiable (generation IDs logged)", f"{len(calls)} calls")

    print("\n== AUTONOMOUS GENERATION & CHECKS (10 pts)")
    line(source.get("result") == "ok" or None, "source text used",
         f"{source.get('format', '')} {source.get('excerpt_chars', '')} chars" if source.get("result") == "ok"
         else "unavailable -> model-knowledge fallback")
    for c in checks:
        st = c.get("stats", {})
        detail = (f"{st.get('states_tested', '-')} input states, {st.get('check_passes', '-')}/{st.get('check_evaluations', '-')} "
                  f"check runs pass, {len(c.get('errors', []))} errors")
        line(c["result"] == "pass" or None, c["action"], detail)
        for e in c.get("errors", [])[:3]:
            print(f"        - {e[:150]}")
    revs = [e for e in events if e["stage"] == "revise" and e["action"] != "llm_call"]
    line(True, "revisions", ", ".join(f"{e['action']} -> {e['result']}" for e in revs) or "none needed")
    line(not fin.get("remaining_errors"), "remaining errors", str(fin.get("remaining_errors") or "none"))

    print("\n== REQUIRED PAGE CONTENT (brief section 2)")
    syms = spec.get("symbols", [])
    line(bool(spec.get("idea")) and bool(spec.get("why_it_matters")) and len(syms) >= 2,
         "starting point: idea, why, symbols", f"{len(syms)} symbols, {len(spec.get('equations', []))} equations")
    views = spec.get("views", [])
    line(len(views) >= 1, "meaningful visual", ", ".join(v.get("type", "?") for v in views))
    params = spec.get("params", [])
    eff = next((c.get("stats", {}).get("effective_controls") for c in reversed(checks) if c.get("stats")), None)
    line(len(params) >= 2 and (eff or 0) >= 2, "at least two meaningful controls",
         f"{len(params)} controls, {eff} change the results: " + ", ".join(f"{p['id']}({p['type']})" for p in params))
    ex = spec.get("explorations", [])
    line(len(ex) == 2 and all(e.get(k) for e in ex for k in ("change", "observe", "why", "preset")),
         "two guided explorations (change/observe/why)", " | ".join(e.get("title", "")[:40] for e in ex))
    line(bool((spec.get("misconception") or {}).get("text")), "limitation / misconception",
         (spec.get("misconception") or {}).get("title", ""))
    g, paper = spec.get("grounding", {}), spec.get("paper", {})
    line(bool(paper.get("title")) and bool(paper.get("section") or paper.get("equation")), "paper + section/equation identified",
         f"{paper.get('title', '')[:40]} | {paper.get('section', '')} {paper.get('equation', '')}")
    line(bool(g.get("from_paper")) and bool(g.get("our_simplifications")), "paper claims vs our simplifications",
         f"{len(g.get('from_paper', []))} vs {len(g.get('our_simplifications', []))}, {len(g.get('quotes', []))} verified quotes")
    ext = re.findall(r"<(?:script|link|img|iframe)\b[^>]*(?:src|href)\s*=\s*['\"]?https?://", html, re.I)
    line(not ext, "self-contained, works offline", "no external resources" if not ext else f"{len(ext)} external refs")
    line("sk-or-" not in html and "sk-or-" not in (out / "trace.jsonl").read_text(), "no API key in outputs")

    if browser:
        print("\n== IN THE BROWSER (working interaction, 15 pts)")
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from browser_check import check
            b = check(out / "index.html", out / "shots")
        except Exception as exc:
            line(None, "browser test skipped", str(exc)[:100])
            return
        line(not b["errors"], "no JavaScript errors", "; ".join(b["errors"])[:120])
        line(b["controls"] >= 2 and all(b["ranges_change_output"]), "controls update the results",
             f"{b['controls']} controls, sliders change output: {b['ranges_change_output']}")
        fails = [c for k in b if k.endswith("checks") for c in b[k] if c.startswith("✗")]
        line(not fails, "live checks pass (defaults + both presets)", "; ".join(fails)[:120] or f"{len(b['checks'])} checks")
        line(not b["bad_text"], "no NaN / [object] on page", str(b["bad_text"] or ""))
        line(not b["horizontal_scroll_mobile"], "readable on a phone", "")
        print(f"     screenshots: {out / 'shots'}/  (full.png, preset1.png, preset2.png, mobile.png)")
    print("\n  Quality points (accuracy 25, clarity 20, visual 15) need a human read: open index.html.\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(Path(args[0] if args else "out"), "--no-browser" not in sys.argv)
