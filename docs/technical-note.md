# Technical note: multi-agent incident investigator

## Approach

A deterministic coordinator runs six LLM agents (`gemini-3.8-flash`) over a shared blackboard. An LLM planner turns the incident into a task graph. Observability and dependency specialists run in parallel with least-privilege tools. A reasoning agent ranks two to five hypotheses, and a critic accepts them, asks for a revision or requests more evidence, for up to three rounds. A reporter writes the final report. Tools mint evidence IDs and every citation is validated, so each claim points at data. Code, not the model, derives the status from confidence, which makes "inconclusive" a normal outcome.

## Key decisions

- **Thin custom harness, not a framework:** explicit limits, retries, tracing and replay, with every behaviour tested offline against a scripted model.
- **Submit tools for structured output:** schema and citation errors return to the model as tool errors it can correct.
- **Seeded synthetic platform:** 12 reproducible incidents with misleading evidence, conflicting signals, tool faults and a prompt injection. Agents cannot see ground truth, and tests enforce it.
- **Fair baseline:** one agent with the same model, tools, faults, budget and report schema, at the highest effort level.

## Results

36 runs per system (3 trials of each scenario):

| Metric | Multi-agent | Single agent |
|---|---|---|
| Root-cause accuracy (top-1) | 83% | 86% |
| Root cause in top 3 | 100% | 94% |
| Citation validity | 100% | 100% |
| Key-evidence recall | 97% | 96% |
| Tool calls per run | 72.4 | 42.0 |
| Tokens per run | 487,073 | 610,175 |
| Cost per run | $0.39 | $0.20 |
| Wall-clock per run | 487 s | 265 s |

The accuracy difference (-3 points) is inside the noise band, so accuracy alone does not separate the systems. The multi-agent system did better on no scenario and worse on INC-12. Full tables are in `evals/results/`.

## Limitations

Synthetic telemetry is cleaner than production data. With 36 runs per system, differences below about 17 points are noise. History search uses keywords. There are no real connectors, and an investigation runs in one process without durable state.

## Next steps

Real backends behind the existing ports (Loki, Prometheus, ArgoCD); durable execution; embedding search over postmortems; confidence calibration against outcomes; more scenarios and seed variants; evaluation on real incidents.
