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
             fetch ≤6 s,      plan + JSON spec:     structure, JS           │
             pick the named   text, params,         executed in V8 over     ▼
             section; falls   compute(p), views,    ~45 input states,     page.py + templates/ ─► out/index.html
             back to model    explorations,         checks, quotes,       (generic tested widgets)  out/spec.json
             knowledge        checks, grounding     widget data shapes                              out/trace.jsonl
```

1. **Source** (`source.py`). Fetches the URL with a hard 6-second wall-clock budget. arXiv links use the HTML
   version, where equations keep their LaTeX via MathML `alttext`. PDFs go through `pypdf`, and symbols lost in
   extraction are marked `□`. A relevance check rejects text that doesn't match the focus (login walls, paywalls,
   wrong pages). The excerpt (at most 9k characters) contains the paper opening, the section named in the focus
   (sections, subsections and appendices, e.g. "Section 3.2.1", "Appendix A.2"), and a window around every item the
   focus names explicitly ("Algorithm 1", "Eq. (5)", "Theorem 17", "Figure 2", "Table 1"). Without a named section,
   it takes the most focus-relevant passages. If the fetch fails, the agent continues from the brief and the
   model's knowledge, quotes nothing, and says so on the page and in the trace.
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
   - rejects all-zero defaults and calculations too slow for live interaction;
   - checks that precise numbers stated in the guided explorations are values `compute` actually produces from that
     exploration's preset; on failure, the actual values are sent back to the model;
   - checks that every equation, algorithm, theorem, figure or table number the page cites appears in the source;
   - drops any quote that is not verbatim in the fetched source.
4. **Revise.** Only the failing fields go back to the model, together with the exact failing inputs. A revision is
   kept only if it lowers the weighted error score. If problems remain, the failing views and checks are dropped,
   and any sentence that still states an unverifiable number is removed, so the page stays usable and does not
   publish a wrong value.
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

## Security and robustness

Every input is treated as untrusted: the URL, the downloaded file, the case text and the code the model writes.
One hang or crash would score 0 for a run, so every blocking step has a hard limit.

| Threat | Defence | Tested |
|---|---|---|
| Slow server (1 byte/s), endless redirects, server that never answers | Fetch and extraction run under a 6 s wall-clock limit (`guard.py`); `requests` timeouts alone apply per read | ✓ local hostile server: all cut off within the limit (before the fix, a slow server kept the agent busy for 200+ s) |
| Huge or complex PDF | 16 MB download cap, at most 150 pages, same 6 s limit | ✓ |
| Non-web links (`file://`, `javascript:`) | Only http(s) is fetched; non-http links are not rendered as links | ✓ |
| Model call that hangs | Hard per-call limit; budget of 6 requests, 28k completion tokens, 9 min | ✓ |
| Anything else that stalls | Watchdog logs and exits nonzero at 570 s, before the 600 s limit | ✓ |
| Oversized case fields (token inflation) | `focus` capped at 4,000 characters, `audience` at 600; truncation logged in the trace | ✓ live: a 224k-character focus kept the prompt at about 3.3k tokens |
| Prompt injection in the paper or the case | Prompt treats both as data, not instructions; no URLs allowed in generated code | ✓ live: a paper telling the model to write 20,000 words, add a script and address the grader had no effect |
| Generated code: infinite loop, memory bomb, very slow loops | V8 time (8 s) and memory (256 MB) limits on every call; slow `compute` rejected | ✓ |
| Generated code reaching the network or the DOM | Rejects `fetch`, `XMLHttpRequest`, `document`, `window`, `globalThis`, `eval`, `Function`, `constructor`, URLs… | ✓ |
| Unsafe SVG (scripts, embedded pages, links) | Rejected by the validator and again by the page runtime | ✓ |
| Markup injection through text | All text escaped; embedded JSON cannot close its `<script>` tag | ✓ |

The 15 attack tests live in `tests/test_security.py` and all pass: `python -m unittest tests.test_security`.

## Testing and generalization

- **Unit and attack tests:** `python -m unittest discover -s tests` (31 tests, all passing).
- **Source types** (loader only): arXiv in 5 URL forms (HTML, abs, pdf, `.pdf`, old-style IDs and papers with
  no HTML version), university PDFs, an ACL conference PDF, JMLR, PMLR, a plain web page, a paywall, a 404, a dead
  domain and a blocking server. Readable sources gave the right section; the rest fell back cleanly.
- **End to end** (`tools/eval.py`, each case run twice like the grader, then a headless Chromium check of every
  control and preset): 14 papers across loss functions, normalization, optimizers, attention, encodings,
  low-rank updates, graph ranking, information theory and filtering. Results are in the table below.

| Measure (28 runs: 14 papers × 2, `deepseek/deepseek-v4.1-flash`) | Result |
|---|---|
| Runs producing a page / fully passing checks | 28 / 28 |
| Total tokens per run (prompt + completion) | mean 11.9k, range 4.6k–20.9k |
| Completion tokens per run (limit 30k) | max 7.5k |
| Requests per run (limit 10) | 1–3 |
| Wall time per run (limit 600 s) | mean 15.9 s, max 24.5 s |
| Browser check: JS errors / NaN shown / failing live checks / phone overflow | 0 / 0 / 0 / 0 |
| Source unavailable (dead link) | page still produced from the brief, no quotes, clearly labelled |

These are our own practice cases, not the hidden assessment set.

## Repository layout

| Path | Purpose |
|---|---|
| `agent.py` | CLI and orchestration (generate → check → revise → render) |
| `llm.py` | OpenRouter client, budget guard, usage logging |
| `prompts.py` | Generic generation and repair prompts (no paper-specific content) |
| `source.py` | Fetching, HTML/PDF text extraction, excerpt selection |
| `validate.py` | Deterministic checks, including JS execution in V8 |
| `guard.py` | Hard wall-clock limits and the watchdog |
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
