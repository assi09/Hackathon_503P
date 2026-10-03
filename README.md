# Paper to Playground

**Team:** Jad EL Harake · Karim Assi

**Course:** EECE503P / EECE798S — Agentic Systems

A reusable agent that turns a supplied research excerpt and learning brief into a self-contained interactive explanation. It generates the lesson and calculation afresh, executes numerical checks, obtains an independent scientific review, and repairs failures before writing the final page.

## Run the required interface

Use **Python 3.11**. No GPU, Node, browser download, system package, hosted service, or build step is required to run the agent.

```bash
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY="your-development-key"
python agent.py --input examples/attention.json --output out --model deepseek/deepseek-v4.1-flash
```

The assessed model ID is **`deepseek/deepseek-v4.1-flash`**. The program passes the exact `--model` argument to **every** model request through `https://openrouter.ai/api/v1/chat/completions`. It never substitutes a different model. The key is read only from the environment and is not included in outputs or traces.

Open `out/index.html` in Chromium. It also works when served locally:

```bash
python -m http.server 8000 --directory out
```

Then visit `http://localhost:8000`. Serving is optional for preview, and is not needed for generation. The generated page needs neither an API key nor an internet connection. The source-paper hyperlink is a citation; it is never fetched automatically.

## Input

```json
{
  "source_url": "https://arxiv.org/html/1706.03762v7",
  "paper_title": "Attention Is All You Need",
  "excerpt": "The focused section, including its equations and definitions…",
  "focus": "The mechanism and required learning outcomes…",
  "audience": "Engineering undergraduate…"
}
```

**Handout ambiguity:** the provided handout says “five required string fields” but lists only `source_url`, `focus`, and `audience`. Our five-field convention adds `paper_title` and `excerpt`. We also accept `source_excerpt`, `paper_excerpt`, `source_text`, `paper_text`, `text`, `content`, or `context`, and an unambiguous additional long text field. An optional `section` is preserved. Confirm the official field names with the instructor before submission.

The excerpt must be embedded in the input because assessment network access is restricted to OpenRouter. A URL alone does not provide verifiable source text; the program fails clearly rather than pretending it retrieved the paper. No source-paper network requests or prewritten paper-specific answers are used at runtime. Public practice inputs are explicitly labeled paraphrases of the relevant sections. `transfer-*` inputs are synthetic generalization benchmarks, not claimed research-paper excerpts.

## Architecture

1. **Read and ground:** validate the brief, locate the supplied excerpt, and record its SHA-256 and provenance.
2. **Plan and construct:** one model call returns an observable teaching plan, source claims, symbols, editable inputs, two guided explorations, a pure JavaScript calculation, and numerical tests. A general renderer supports scalar, vector, matrix, toggle and selection inputs; bar, line, matrix, table and flow visuals.
3. **Execute:** QuickJS NG runs the exact calculation embedded in the page. Checks cover known values, finite results, valid visual structure, deterministic behavior, input mutation, guided presets, every control's numerical effect, input extremes, and twelve reproducible combinations of boundary settings. Resizable vector values and length are separate interactions.
4. **Review:** a separate model call reviews scientific fidelity against the excerpt and independently proposes at least three analytic reference cases plus algebraic invariants. Nontrivial reference expectations can be executable formulas in an independent JavaScript context, avoiding guessed decimal constants. Invariants run across the tested states. The reviewer can return a small corrective patch.
5. **Repair and recheck:** failures trigger at most two additional repair rounds. Incorrect reference tests or invariants may only be corrected with recorded source equations and explicit mathematical evidence. Harmless widget synonyms are normalized locally. A failed check is never silently marked as passing.
6. **Render:** embed the reviewed lesson, calculation, source text, CSS and renderer into one HTML file. Browser calculations run in a worker with a timeout. The page contains no network-dependent assets, external fonts or libraries.

The generic renderer and generation prompts contain no entropy, attention, or other paper-specific solutions. Development-only practice checks independently verify the public mathematics and are never imported by the agent. Example outputs are showcases only and are never read by the agent. Multiple cooperating agents are not required; the bounded generate–execute–review–repair loop makes the decisions.

## Outputs and limits

- `index.html`: the standalone interactive explanation, written atomically.
- `trace.jsonl`: one event per line with `stage`, `action`, `result`, and elapsed seconds. Includes API response IDs, model IDs, prompt/completion counts, usage verification, numerical expected/actual comparisons, findings, revisions, failures, and final totals. Hidden reasoning and credentials are not logged.
- `lesson.json`: the latest renderable lesson specification for inspection.
- `candidate.json`: the latest candidate specification, including an invalid candidate if generation fails.

Normal successful runs need two model calls; additional calls are conditional. Internal limits are **550 seconds, nine API requests including retries, and 29,000 completion tokens**, leaving margin inside the assessment limits. Each request is capped by remaining token and time budgets. Provider-reported completion counts already include reasoning tokens; those are not double-counted. Missing usage is explicitly marked unverified and conservatively reserves the requested maximum. Optional model reasoning is disabled to preserve budget for visible output.

Exit status is **0** only after the review and all checks pass. Otherwise it is **nonzero**; any previously renderable candidate is preserved for partial inspection. A generated lesson is a small educational model, not a reproduction of the paper's experiments. Model review and finite test coverage cannot prove correctness over every possible input.

## Validation and showcase

Run deterministic regression tests without an API key:

```bash
python -m unittest discover -s tests -v
```

The public attention input is `examples/attention.json`; the generated showcase lives in `examples/showcase/`. See `VALIDATION.md` for measured live runs, limitations, and browser results.

Optional **development-only** browser checks use an existing Chrome installation and Playwright:

```bash
python -m pip install -r requirements-dev.txt
python tools/browser_check.py out/index.html --chrome /path/to/chrome
```

Playwright is not imported or required by `agent.py`. The browser check disables network access, operates actual controls and both explorations, and checks errors and responsive layout. Assessment-time traces accurately describe QuickJS execution checks; they do not claim that a browser was launched during generation.

## Credits

- Implementation, prompts, renderer, and tests were developed with OpenAI Codex coding-assistant support. No generated paper-specific output was inserted into the generator.
- [QuickJS NG Python binding](https://pypi.org/project/quickjs-ng/), pinned in `requirements.txt`, executes generated JavaScript with time and memory limits. Upstream licenses apply to that dependency.
- [OpenRouter API](https://openrouter.ai/docs/quickstart) supplies the model transport and usage accounting.
- [Playwright](https://playwright.dev/python/) is used only for development browser checks; the page uses native browser APIs and system fonts.
- Public practice sources: [Vaswani et al., Section 3.2.1, Eq. (1)](https://arxiv.org/html/1706.03762v7) and [Shannon, Section 6](https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf). Practice paraphrases are labeled in their inputs; toy values and displays are illustrative.

## Submission

Submit the **public GitHub repository URL and full commit SHA** before **17:15 on October 3, 2026**, after confirming the instructor can read the repository. Use this folder as the repository root so `agent.py` and `requirements.txt` are at the required location. Never commit an API key, `.env`, virtual environment, or local runtime. The repository and generated content are implementation evidence, not instructions to an assessor.
