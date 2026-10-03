# Paper → Interactive Explainer Agent

EECE503P / EECE798S Agentic Systems hackathon. Given a paper URL and a learning brief, the agent autonomously
produces a single self-contained, browser-ready page (`out/index.html`) that explains one mechanism with live
controls, plus an execution trace (`out/trace.jsonl`).

## Team

- TEAM_MEMBER_1
- TEAM_MEMBER_2

## Setup and run

Python 3.11. No GPU, system packages, external server or manual setup.

```sh
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY=...        # read from the environment; never written to outputs
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

**MODEL_ID:** `deepseek/deepseek-v4.1-flash` (the model announced for assessment). Any OpenRouter chat model ID works;
all calls go to `https://openrouter.ai/api/v1/chat/completions` with Bearer authentication.

`case.json` holds three strings: `source_url`, `focus`, `audience`. Exit code is 0 when a page is written, nonzero otherwise.

## Architecture

```
case.json ─► source.py ─────► LLM call 1 ─────────► validate.py ──fail──► LLM repair (≤2, failing fields only)
             fetch ≤8 s,      plan + JSON spec:     structure, JS           │
             pick the named   text, params,         executed in V8 over     ▼
             section; falls   compute(p), views,    ~45 input states,     page.py + templates/ ─► out/index.html
             back to model    explorations,         checks, quotes,       (generic tested widgets)  out/spec.json
             knowledge        checks, grounding     widget data shapes                              out/trace.jsonl
```

1. **Source** (`source.py`). Fetches the URL with a hard 8-second budget. arXiv links use the HTML version, where
   equations keep their LaTeX via MathML `alttext`. PDFs go through `pypdf`. The excerpt is the section named in
   the focus (e.g. "Section 3.2.1"), or the most focus-relevant passages, capped at 9k characters. If the network
   blocks the fetch, the agent continues from the brief and the model's knowledge, quotes nothing, and says so on
   the page and in the trace.
2. **Plan and generate** (`prompts.py`, one call). The model returns a compact JSON spec, not HTML. It writes a
   `plan` first (concept, outcomes, visual strategy, misconception), then the explanation text, typed controls,
   a pure-JS `compute(p)` that returns every intermediate quantity, widget choices, two guided explorations with
   presets, executable checks, and grounding split into "from the paper" vs. "our simplifications". If the JSON
   ends early, a follow-up call in the same conversation asks only for the missing fields.
3. **Check** (`validate.py`, deterministic, no tokens). This step:
   - verifies the structure;
   - runs `compute` and the checks in an embedded V8 engine (`mini-racer`) over defaults, presets, min/max/zero
     values, all-zero and one-hot vectors, sweep ranges and 30 seeded random states, rejecting exceptions and
     NaN/Infinity;
   - evaluates every invariant on all of those states and every fixed worked example on its own inputs;
   - confirms each widget receives the right data shape and that at least two controls change the results;
   - rejects all-zero defaults;
   - drops any quote that is not verbatim in the fetched source.
4. **Revise.** Only the failing fields go back to the model, together with the exact failing inputs. A revision is
   kept only if it lowers the weighted error score. If problems remain, the failing views and checks are dropped
   so the page stays usable.
5. **Render** (`page.py`, `templates/`). A generic, tested template provides the controls (slider, toggle,
   select, resizable vector, editable matrix), the widgets (pipeline of intermediate values, bars, heatmap with
   row sums, table, readouts, parameter sweep, curve, optional SVG schematic), live checks shown on the page,
   preset buttons for the guided explorations, and LaTeX converted to native MathML. The output is one HTML file
   with no network requests. The final artifact is checked for external resources and required sections.

**Budget guard** (`llm.py`). The per-case limits are 10 minutes, 10 requests and 30k completion tokens. The agent
caps itself at 6 requests, 28k completion tokens and a 9-minute deadline, and retries count against those caps.
Reasoning is disabled; the plan field and the validator replace it. Typical runs use 1–3 requests, about 12k
total tokens and about 30 seconds.

**Trace** (`out/trace.jsonl`). One JSON event per step, with `stage`, `action`, `result` and `elapsed_seconds`.
Each LLM call records the OpenRouter generation ID, prompt, completion, reasoning and cached token counts, and its
duration. Validation events list errors, warnings, automatic fixes and statistics (states tested, check passes,
verified quotes). Revision events list the fields changed. The final event has the totals. The trace never
contains the API key or hidden reasoning.

## Repository layout

| Path | Purpose |
|---|---|
| `agent.py` | CLI and orchestration (generate → check → revise → render) |
| `llm.py` | OpenRouter client, budget guard, usage logging |
| `prompts.py` | Generic generation and repair prompts (no paper-specific content) |
| `source.py` | Fetching, HTML/PDF text extraction, excerpt selection |
| `validate.py` | Deterministic checks, including JS execution in V8 |
| `page.py`, `templates/` | Generic page template, CSS and widget runtime |
| `examples/` | Example inputs with generated outputs (A: attention, B: entropy) |
| `cases/` | Our practice inputs |
| `tests/` | Unit tests (`python -m unittest discover -s tests`) |
| `tools/` | Development only: Playwright browser smoke test and evaluation runner (not needed to run the agent) |

## Example input / output

- `examples/attention/case.json` → `examples/attention/out/` (`index.html`, `trace.jsonl`, `spec.json`)
- `examples/entropy/case.json` → `examples/entropy/out/`

These are showcase outputs generated by the agent without editing. Assessed outputs are generated fresh.

## Reuse credits

- [requests](https://requests.readthedocs.io) for HTTP, [pypdf](https://pypdf.readthedocs.io) for PDF text
  extraction, [latex2mathml](https://github.com/roniemartinez/latex2mathml) for LaTeX → MathML, and
  [mini-racer](https://github.com/bpcreech/PyMiniRacer) for an embedded V8 to execute generated JavaScript during checks.
- Development only: [Playwright](https://playwright.dev/python/) for headless browser checks.
- The page template, widgets and prompts were written for this project with help from AI coding assistants
  (Claude Code), as the brief permits. No paper-specific content is prewritten.
