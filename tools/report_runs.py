"""Summarize actual traces without making claims about unseen evaluation cases."""
import json
import sys
from pathlib import Path

print('| Run | Success | Calls | Total API tokens | Seconds | Checks |')
print('|---|---:|---:|---:|---:|---:|')
for arg in sys.argv[1:]:
    path=Path(arg)
    events=[json.loads(line) for line in (path/'trace.jsonl').read_text().splitlines()]
    last=events[-1];r=last['result']
    print(f"| {path.name} | {r.get('success',False)} | {r.get('requests','?')} | "
          f"{r.get('total_tokens',r.get('prompt_tokens',0)+r.get('completion_tokens',0))} | "
          f"{last['elapsed_seconds']:.1f} | {r.get('checks_passed','?')}/{r.get('checks_total','?')} |")
