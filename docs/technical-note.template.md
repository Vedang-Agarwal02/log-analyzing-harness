# Technical note: multi-agent incident investigator

## Approach

A deterministic coordinator runs six LLM agents (`«MODEL»`) over a shared blackboard. An LLM planner turns the incident into a task graph. Observability and dependency specialists run in parallel with least-privilege tools. A reasoning agent ranks two to five hypotheses, and a critic accepts them, asks for a revision or requests more evidence, for up to three rounds. A reporter writes the final report. Tools mint evidence IDs and every citation is validated, so each claim points at data. Code, not the model, derives the status from confidence, which makes "inconclusive" a normal outcome.

## Key decisions

- **Thin custom harness, not a framework:** explicit limits, retries, tracing and replay, with every behaviour tested offline against a scripted model.
- **Submit tools for structured output:** schema and citation errors return to the model as tool errors it can correct.
- **Seeded synthetic platform:** 12 reproducible incidents with misleading evidence, conflicting signals, tool faults and a prompt injection. Agents cannot see ground truth, and tests enforce it.
- **Fair baseline:** one agent with the same model, tools, faults, budget and report schema, at the highest effort level.

## Results

«RUNS» runs per system («TRIALS» trials of each scenario):

| Metric | Multi-agent | Single agent |
|---|---|---|
| Root-cause accuracy (top-1) | «M_RC» | «S_RC» |
| Root cause in top 3 | «M_TOP3» | «S_TOP3» |
| Citation validity | «M_CIT» | «S_CIT» |
| Key-evidence recall | «M_KEY» | «S_KEY» |
| Tool calls per run | «M_TOOLS» | «S_TOOLS» |
| Tokens per run | «M_TOK» | «S_TOK» |
| Cost per run | «M_COST» | «S_COST» |
| Wall-clock per run | «M_WALL» | «S_WALL» |

«VERDICT» The multi-agent system did better on «M_WINS» and worse on «S_WINS». Full tables are in `evals/results/`.

## Limitations

Synthetic telemetry is cleaner than production data. With «RUNS» runs per system, differences below about «NOISE» points are noise. History search uses keywords. There are no real connectors, and an investigation runs in one process without durable state.

## Next steps

Real backends behind the existing ports (Loki, Prometheus, ArgoCD); durable execution; embedding search over postmortems; confidence calibration against outcomes; more scenarios and seed variants; evaluation on real incidents.
