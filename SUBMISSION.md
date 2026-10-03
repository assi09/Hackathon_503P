# Submission handoff

Team: **Jad EL Harake and Karim Assi**. Deadline supplied by the team: **17:15, October 3, 2026**.

## Resolve before freezing

1. Confirm the instructor's exact `case.json` field names. The handout says five strings but names three. The agent needs the supplied excerpt because assessment network access is restricted to OpenRouter. Aliases are supported; see README.
2. Publish this directory as its own **public** GitHub repository. Do not publish the unrelated EECE 435 lab repository as this submission.
3. Verify the repository's root contains `agent.py`, `requirements.txt`, `README.md`, `p2p/`, and the example input/output pair.
4. Submit the repository URL **and full 40-character commit SHA** before the cutoff. The frozen commit is what will be assessed; do not rely on later commits.

## Exact assessment command

```bash
python -m pip install -r requirements.txt
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

The instructor sets `OPENROUTER_API_KEY`. Use an authorized development key for local tests. No key belongs in the repository or generated page.

## Verify a fresh case

Run a new input into a fresh directory, confirm exit code 0, then open `index.html` in Chromium. Change each input, try both guided explorations, and inspect `trace.jsonl`. A trace's `finish/summary` reports actual token usage and check results. A page saved after failure is a candidate, not a verified success.

Public showcase pages:

- `examples/showcase/index.html` — attention, paired with `examples/attention.json`.
- `examples/entropy-showcase/index.html` — entropy, paired with `examples/entropy.json`.

Do not manually fix output pages for an assessment. Fix the reusable generator and regenerate. Development-only synthetic transfer cases are clearly labeled and are not claims about the instructor's hidden cases.

## Describe the project accurately

The agent selects the concept from the supplied excerpt, makes an observable teaching plan, generates a typed lesson and pure numerical function, executes it in bounded QuickJS, obtains an independent review and reference checks, repairs failures, and embeds the result into an offline HTML renderer. The same numerical function runs in the browser. The implementation prioritizes source fidelity, intermediate values, causal controls, and guided experiments.

No presentation or hosted site is required by the handout. No ranking claim is justified before the hidden assessment.
