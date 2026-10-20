# Evaluation results

Model: `gemini-3.8-flash` · runs: 4 (scored 4, excluded 0) · errors.jsonl: 0

## Quality (mean over scored runs; ± is the standard deviation of per-trial means)

| Metric | multi | single |
|---|---|---|
| Root-cause accuracy (top-1) | 1.00 | 1.00 |
| Top-3 accuracy | 1.00 | 1.00 |
| Trigger accuracy (cases with a trigger) | 1.00 | 1.00 |
| Status correct | 1.00 | 1.00 |
| Key-evidence recall | 1.00 | 1.00 |
| Citation validity | 1.00 | 1.00 |
| Red herring avoided | n/a | n/a |
| Data gap acknowledged | 1.00 | 0.00 |
| Prompt injection resisted | n/a | n/a |
| Brier score (lower is better) | 0.02 | 0.00 |

## Efficiency (mean per run)

| Metric | multi | single |
|---|---|---|
| LLM calls | 49.0 | 21.0 |
| Tool calls | 113.0 | 45.0 |
| Tool attempts (incl. retries) | 115.5 | 47.0 |
| Failed tool calls | 2.0 | 1.0 |
| Uncached input tokens | 236622.0 | 91546.0 |
| Cache-read tokens | 144436.0 | 433445.5 |
| Cache-write tokens | 0.0 | 0.0 |
| Output tokens | 39428.5 | 12213.5 |
| Total tokens | 420486.5 | 537205.0 |
| Cost (USD) | 0.336 | 0.147 |
| Wall-clock (s) | 258.4 | 166.3 |

## Per scenario (root cause correct / runs)

| Scenario | multi | single |
|---|---|---|
| INC-01 | 1/1 | 1/1 |
| INC-10 | 1/1 | 1/1 |

## By tag (root-cause accuracy)

| Tag | multi | single |
|---|---|---|
| bad_deployment | 1.00 | 1.00 |
| control | 1.00 | 1.00 |
| inconclusive | 1.00 | 1.00 |
| tool_failure | 1.00 | 1.00 |

## Notes

- With about 2 runs per system, accuracy differences below roughly 71 percentage points are within noise.
- Runs answered by a different or fallback model are excluded from these tables (`excluded`).
- Infrastructure and harness failures are listed in `errors.jsonl` and are never scored as wrong answers.
