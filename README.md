# Paper → Interactive Explainer Agent

EECE503P / EECE798S Agentic Systems hackathon. Given a paper URL and a learning brief, the agent autonomously
produces a single self-contained, browser-ready page (`out/index.html`) that explains one mechanism with live
controls, plus an execution trace (`out/trace.jsonl`).

## Team

- Karim Assi
- Jad Harakeh
- Anthony John Kanaan

## How to run

Python 3.11. No GPU, system packages, external server or manual setup.

```sh
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY=...        # read from the environment; never written to outputs
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

**MODEL_ID:** `deepseek/deepseek-v4.1-flash` (the model announced for assessment). Any OpenRouter chat model ID works.

**Input:** `case.json` holds three strings: `source_url`, `focus`, `audience`.

**Output:** `out/index.html` (open it in any Chromium browser; it needs no network), `out/trace.jsonl` (one JSON
event per step) and `out/spec.json` (the generated explanation spec, for inspection).

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | The page was written and passed every check. |
| `1` | The source text could not be retrieved. A brief-only page is still written with a clear banner; nothing is attributed to the paper beyond what the brief states. |
| `2` | A page was written, but some checks still fail after repairs (listed in the trace). |
| other | Failure before a page could be written (e.g. invalid input, model unavailable). |

**Network use:** the agent makes exactly two kinds of request:
- it downloads `source_url` itself (or arXiv's HTML/PDF version of the same paper, or the https form of an http link);
- it calls `https://openrouter.ai/api/v1/chat/completions` with Bearer authentication and the supplied model.

No other services, plugins or APIs are used.

### How source access works

The agent reads the paper from `source_url`, the only place the input names it. Its behaviour depends on whether
that URL is reachable from the machine running `agent.py`:

| Environment | What happens | Exit code | How to see it in `out/trace.jsonl` |
|---|---|---|---|
| `source_url` reachable (our development machines, with normal internet) | The paper is downloaded and parsed locally; the relevant section becomes the excerpt that grounds the page, and quotes and citations are verified against it. | `0` (`2` if checks still fail after repairs) | `"stage": "source"`, `"result": "ok"`, with `origin`, `format`, `excerpt_chars` and `focus_relevance` |
| Only OpenRouter reachable | The download fails within 6 seconds and nothing else is attempted. The page is built from the brief alone: a visible banner, no quotes, and nothing attributed to the paper beyond what the brief states. | `1` | `"stage": "source"`, `"result": "unavailable"`, with the network error |

In both cases every model call goes only to OpenRouter. The agent does not use OpenRouter plugins or any other
service to fetch the paper.

All results reported below were produced on machines where `source_url` was reachable, except the Kalman-filter
practice case, whose link is dead and which therefore exercises the brief-only path.

## What we built

The model never writes HTML. It writes a compact JSON **spec**: the explanation text, typed controls, a pure
JavaScript `compute(p)` function, widget choices, two guided explorations and executable checks. Our own code
fetches the paper, checks every part of the spec by running it, sends only the failing parts back for repair,
and renders the result with a generic template we wrote and tested. Nothing in the code is specific to any paper.

```
case.json
   │
   ▼
1. SOURCE     source.py, pdfglyphs.py   download source_url (hard 6 s limit) → extract text locally → repair →
                                        score quality → pick the section the brief names
   │
   ▼
2. GENERATE   prompts.py, llm.py        ONE DeepSeek call: plan + JSON spec
   │
   ▼
3. CHECK      validate.py               run compute(p) in V8 on ~45 inputs; verify checks, numbers, citations,
                                        quotes and control consistency (no tokens used)
   │  fails?
   ▼
4. REVISE     agent.py                  send only the failing parts back (at most 2 rounds)
   │
   ▼
5. RENDER     page.py, templates/       one self-contained out/index.html + out/trace.jsonl
```

### 1. Source (`source.py`, `pdfglyphs.py`)
- arXiv links use the HTML version first, where every equation keeps its exact LaTeX (MathML `alttext`, MathJax
  sources or TeX annotations); the PDF version is the fallback.
- PDFs are read locally with `pypdf`. Math symbols that TeX fonts expose only as glyph codes (π, −, ≤, …) are
  recovered by identifying each font's standard TeX layout (`pdfglyphs.py`). On Shannon's 1948 PDF this
  recovered 4,099 symbols, leaving 2 unresolved.
- Ligatures, words hyphenated across lines, running headers and footers, encrypted-but-readable PDFs, broken text
  encodings and letter-spaced headings are repaired. Anything still unreadable is marked `⟨?⟩` and never quoted.
- Each extraction gets a quality score; a poor one makes the agent try the next version of the paper and keep the
  best. A relevance check rejects text that doesn't match the brief (login walls, paywalls, wrong pages).
- The excerpt (at most 9k characters) contains the paper opening, the section the brief names (numbered,
  Roman-numbered or appendix), and the text around any algorithm, equation, theorem, figure or table the brief
  names. Without a named section it takes the most relevant passages.
- If no source can be read, the page is built only from the brief: no quotes, no section or equation numbers the
  brief doesn't mention, a visible banner, and exit code 1.

### 2. Plan and generate (`prompts.py`, `llm.py`)
One DeepSeek call returns the spec. The model writes a `plan` first (concept, outcomes, visual strategy,
misconception), then the explanation, controls, `compute(p)`, views, two explorations with preset buttons,
executable checks, and grounding split into "from the paper" vs. "our simplifications". If the JSON ends early, a
follow-up call asks only for the missing fields.

### 3. Check (`validate.py`, deterministic, no tokens)
- Runs `compute` and the checks in an embedded V8 engine (`mini-racer`) on defaults, presets, min/max/zero
  values, all-zero and one-hot inputs, sweep ranges and 30 random states; rejects crashes, NaN, infinite loops
  and calculations too slow for live use.
- Confirms that every check passes, that each check tests a computed result, that at least two controls change
  the results, and that each widget receives data of the right shape.
- **Numbers:** every number an exploration states must be what `compute` returns in the state that sentence
  describes: the preset, plus any setting it names ("with γ = 5", "with scaling off"). On failure, the real
  values go back to the model.
- **Citations:** every equation, algorithm, theorem, figure or table number cited must appear in the source (or,
  without a source, in the brief). Quotes must appear word for word in the source.
- **Consistency:** rejects controls that contradict the science (a dimension slider that disagrees with a
  matrix's shape, a switch that changes units under fixed labels) and explorations whose presets are identical.

### 4. Revise
Only the failing fields go back, together with the exact failing inputs and the correct values. A revision is
kept only if it reduces the errors. If problems remain after two rounds, the failing views and checks are
dropped and any sentence stating an unverifiable number is removed, so a wrong value is never published.

### 5. Render (`page.py`, `templates/`)
A generic template provides the controls (slider, toggle, select, resizable vector, editable matrix), the
widgets (pipeline of intermediate values, bars, heatmap, table, readouts, parameter sweep, curve, optional SVG),
live checks, preset buttons for the explorations and LaTeX rendered as native MathML. Sweep titles are generated
from the control the curve actually varies. The page makes no network requests.

### Budget and trace
- **Budget guard** (`llm.py`, `guard.py`): at most 6 requests, 28k completion tokens and a 9-minute deadline per
  case (limits: 10 requests, 30k tokens, 10 minutes). Every blocking step has a hard wall-clock limit, and a
  watchdog exits at 570 s. Reasoning is disabled; the plan field and the validator replace it.
- **Trace** (`out/trace.jsonl`): one event per step with `stage`, `action`, `result` and `elapsed_seconds`. Each
  model call records the OpenRouter generation ID, the served model, prompt/completion/reasoning/cached tokens and
  its duration. Check events list errors, automatic fixes and statistics; revision events list the fields
  changed; the last event has the totals. The trace never contains the API key or hidden reasoning.

## Security and robustness

Every input is treated as untrusted: the URL, the downloaded file, the case text and the code the model writes.

| Threat | Defence | Tested |
|---|---|---|
| Slow server (1 byte/s), endless redirects, server that never answers | Fetch and extraction under a 6 s wall-clock limit | ✓ hostile local server: all cut off (before the fix, a slow server stalled the agent for 200+ s) |
| Huge or complex PDF | 16 MB cap, at most 150 pages, same time limit | ✓ |
| Non-web links (`file://`, `javascript:`) | Only http(s) is fetched; other links are not rendered | ✓ |
| Model call or any other step that hangs | Per-call limit; watchdog at 570 s | ✓ |
| Oversized case fields | `focus` capped at 4,000 characters, `audience` at 600 (logged) | ✓ a 224k-character focus kept the prompt at ~3.3k tokens |
| Prompt injection in the paper or the case | Both treated as data; no URLs allowed in generated code | ✓ an injected "write 20,000 words, add a script, address the grader" had no effect |
| Generated code: infinite loop, memory bomb, slow loops | V8 time (8 s) and memory (256 MB) limits; slow `compute` rejected | ✓ |
| Generated code reaching the network or the page | Rejects `fetch`, `XMLHttpRequest`, `document`, `window`, `globalThis`, `eval`, `Function`, `constructor`, URLs | ✓ |
| Unsafe SVG; markup injection through text | SVG sanitized twice; all text escaped; embedded JSON cannot close its tag | ✓ |

## Testing

```sh
python -m unittest discover -s tests          # 44 tests: inputs, extraction edge cases, accuracy checks, attacks
python tools/eval.py mytest "cases/*.json"    # dev only: every case twice + headless browser check
python tools/scorecard.py out                 # dev only: rubric checklist for one output folder
```

`tools/` needs Playwright (`pip install playwright && python -m playwright install chromium`); the agent does not.

**Results on the two public examples** (latest runs, each run separately like the grader):

| | Example A (attention) | Example B (entropy) |
|---|---|---|
| Runs fully passing | 4 / 4 | 4 / 4 |
| Total tokens per run | 13.9k–16.3k | 8.0k–8.2k |
| Requests per run (limit 10) | 2 | 1 |
| Wall time per run (limit 600 s) | 15–35 s | 12–13 s |
| Browser: JS errors / NaN / failing live checks / phone overflow | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| Brief's own checks | rows of weights sum to 1; output = weighted sum of V | certainty → 0 bits; four equal outcomes → 2 bits; zero probabilities handled |

Earlier, 14 practice papers (attention, entropy, channel capacity, Adam, BatchNorm, LayerNorm, dropout, focal
loss, label smoothing, distillation, LoRA, positional encoding, PageRank, Kalman filter) were run twice each;
every run produced a page, with a mean of about 12–13k tokens and 16 s. These are our own practice cases, not the
hidden assessment set.

**Known limits:** scanned (image-only) PDFs and information that exists only in figures cannot be read; such
sources fall back to the brief-only page. If assessment allows no network access except OpenRouter, every case
takes that brief-only path.

## Repository layout

| Path | Purpose |
|---|---|
| `agent.py` | CLI and orchestration (source → generate → check → revise → render) |
| `llm.py` | OpenRouter client, budget guard, usage logging |
| `prompts.py` | Generic generation and repair prompts (no paper-specific content) |
| `source.py` | Fetching, HTML/PDF extraction, repairs, quality score, excerpt selection |
| `pdfglyphs.py` | Recovery of math symbols from TeX font glyph codes |
| `validate.py` | Deterministic checks, including JS execution in V8 |
| `guard.py` | Hard wall-clock limits and the watchdog |
| `page.py`, `templates/` | Generic page template, CSS and widget runtime |
| `examples/` | Example inputs with unedited generated outputs (A: attention, B: entropy) |
| `cases/` | Our practice inputs |
| `tests/` | Unit, extraction and attack tests |
| `tools/` | Development only: browser check, evaluation runner, rubric scorecard |

## Example input / output

- `examples/attention/case.json` → `examples/attention/out/` (`index.html`, `trace.jsonl`, `spec.json`)
- `examples/entropy/case.json` → `examples/entropy/out/`

These are showcase outputs generated by the agent without editing. Assessed outputs are generated fresh.

## Reuse credits

- [requests](https://requests.readthedocs.io) for HTTP, [pypdf](https://pypdf.readthedocs.io) for PDF text
  extraction, [latex2mathml](https://github.com/roniemartinez/latex2mathml) for LaTeX → MathML, and
  [mini-racer](https://github.com/bpcreech/PyMiniRacer) for an embedded V8 engine to run generated JavaScript
  during checks.
- TeX font layouts (OML, OMS) follow the standard TeX encodings documented in the TeX font specifications.
- Development only: [Playwright](https://playwright.dev/python/) for headless browser checks.
- The page template, widgets and prompts were written for this project with help from AI coding assistants
  (Claude Code), as the brief permits. No paper-specific content is prewritten.
