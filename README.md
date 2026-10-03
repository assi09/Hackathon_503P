# Research Paper Visual Explainer

Early foundation for the EECE503P / EECE798S hackathon generator.

## Contract

Requires Python 3.11. The required command shape is:

```sh
python -m pip install -r requirements.txt
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

The three required input fields are `source_url`, `focus`, and `audience`. The CLI validates them and writes `out/trace.jsonl`. It intentionally exits nonzero until model generation is implemented.

## Source loading

`source.py` fetches `source_url` and extracts HTML or PDF text, then selects a focused excerpt for the model. For local development, the same reader accepts a downloaded PDF path; we will document the exact path if an example PDF is bundled. A local example cannot supply unseen grading papers. The brief says assessment network access is limited to OpenRouter, so URL fetching during assessment needs instructor confirmation or another provided source mechanism.

`page.py` renders a structured explanation into a single HTML document with embedded CSS and JavaScript. The renderer is ready to receive content from the generator; it does not contain paper-specific answers.

Run the foundation tests with `python -m unittest discover -s tests -v`.

## Submission checklist

- Connect OpenRouter using the supplied `--model`; read `OPENROUTER_API_KEY` from the environment.
- Confirm how the assessor supplies unseen paper text if external URL fetching is blocked.
- Generate `out/index.html`, check its calculations and interactions, and record API usage and revisions in `out/trace.jsonl`.
- Add team members, architecture, reuse credits, and a freshly generated example input/output pair.
