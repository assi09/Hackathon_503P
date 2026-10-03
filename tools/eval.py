"""Development-only: run every case in cases/ twice (like grading), browser-check each page, summarize."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from browser_check import check  # noqa: E402

MODEL = "deepseek/deepseek-v4.1-flash"


def one(case: Path, run: int, tag: str) -> dict:
    out = ROOT / "runs" / tag / f"{case.stem}_{run}"
    if out.exists():
        subprocess.run(["rm", "-rf", str(out)])
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "agent.py", "--input", str(case), "--output", str(out), "--model", MODEL],
                          cwd=ROOT, capture_output=True, text=True)
    secs = time.monotonic() - t0
    ev = [json.loads(l) for l in (out / "trace.jsonl").read_text().splitlines()]
    fin = ev[-1]
    row = {"case": case.stem, "run": run, "exit": proc.returncode, "status": fin["result"], "secs": round(secs, 1),
           "tokens": fin.get("total_tokens") or (fin.get("prompt_tokens", 0) + fin.get("completion_tokens", 0)),
           "requests": fin.get("requests"), "repairs": fin.get("repairs"),
           "errors": fin.get("remaining_errors") or fin.get("error")}
    if (out / "index.html").exists():
        b = check(out / "index.html", out / "shots")
        row.update(js_errors=b["errors"], bad_text=b["bad_text"], hscroll=b["horizontal_scroll_mobile"],
                   failing_checks=[c for c in b["checks"] if c.startswith("✗")])
    return row


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "eval"
    cases = sorted((ROOT / "cases").glob(sys.argv[2] if len(sys.argv) > 2 else "*.json"))
    jobs = [(c, r) for c in cases for r in (1, 2)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda j: one(j[0], j[1], tag), jobs))
    for r in rows:
        print(json.dumps(r, ensure_ascii=False)[:700])
    ok = [r for r in rows if r["exit"] == 0]
    print(f"\nexit0 {len(ok)}/{len(rows)}  mean tokens {sum(r['tokens'] or 0 for r in rows)/len(rows):.0f}  "
          f"mean secs {sum(r['secs'] for r in rows)/len(rows):.1f}")
