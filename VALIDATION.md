# Validation evidence

All measurements below are actual OpenRouter runs with `deepseek/deepseek-v4.1-flash`, Python 3.11.17, and provider-reported token usage. They are selected successful development runs across revisions, **not an estimated success rate or a prediction of hidden-assessment scores**. The two showcase runs were freshly generated after the final source-citation fix; the subsequent runtime change only broadens the conditional-control check and has its own regression test.

| Case | API calls | Prompt + completion tokens | Seconds | Autonomous checks |
|---|---:|---:|---:|---:|
| Attention showcase | 2 | 12562 | 45.0 | 30/30 |
| Entropy showcase | 2 | 10729 | 50.6 | 32/32 |
| Bayesian update (synthetic) | 3 | 20505 | 68.7 | 31/31 |
| Markov dynamics (synthetic) | 2 | 11839 | 69.3 | 32/32 |
| Quadratic descent (synthetic) | 2 | 11563 | 24.6 | 31/31 |

## Independent and browser validation

- **16 deterministic regression tests pass** under Python 3.11, including API budgets, usage accounting, bounded JavaScript execution, numerical failures, meaningful and conditional controls, source aliases, HTML escaping, and the full missing-review-patch repair path.
- **24 independent attention comparisons pass**, using Python math separate from the generated JavaScript: equal scores, dominant scores, and mixed signs; scaling both on and off; raw scores, scaled scores, softmax weights, and weighted output.
- **Four independent entropy cases pass:** certainty = 0 bits; four equal outcomes = 2 bits; `[0.5,0.5,0]` = 1 bit; `[0.5,0.25,0.25]` = 1.5 bits.
- Both public showcase pages were exercised in actual Chrome with internet access disabled. Checks operate editable controls, vector length where relevant, both guided experiments, and reset. Layout checks cover 390px and 768px widths, with screenshots at desktop and mobile sizes. Browser tests also verify the required local HTTP serving mode.
- The Bayesian, Markov, and quadratic transfer pages also passed offline browser interaction checks. These inputs are synthetic mechanism descriptions, explicitly labeled as such, and never used as built-in generator answers.

The self-contained pages, original traces, lesson specifications, browser results, and independent mathematics results accompany each public showcase in `examples/showcase/` and `examples/entropy-showcase/`. Additional development evidence is in `validation/`. The agent never reads these showcase or validation artifacts.

## Problems found and corrected during development

The initial reasoning-enabled request spent its output allowance on internal reasoning and produced no page. Optional reasoning is now disabled; accounting still uses provider completion totals. Live transfer runs exposed slider naming mismatches, omitted correction patches, invalid expected decimal constants, malformed invariant shapes, and an interactivity test that incorrectly rejected a conditional normalization toggle. These drove general fixes, with recorded failures and regression tests. A mathematically undefined quantity now has an explicit representation instead of an invented numeric placeholder, and source section labels are preserved from supplied metadata.

Earlier failures remain in the local ignored `runs/` directory for development inspection. Failed runs are not represented as successful measurements. No manually edited output is used in the showcases.

## Limits of this evidence

Model-generated reference tests and scientific review can themselves be wrong. Their equations, observations, corrections, and execution results are inspectable; they are not proofs for every possible input. Successful finite tests on five mechanisms do not establish performance on the instructor's five unseen cases or repeated stochastic generations. API latency and returned content vary.

The official input-schema ambiguity remains unresolved: the handout says five required string fields but names three. Before submission, confirm how the excerpt is supplied. The program intentionally requires source text in the input instead of falsely claiming to have downloaded a paper under the assessment's network restrictions.
