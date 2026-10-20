# Multi-Agent Production Incident Investigator
## Strategy, Architecture and Decision Record

| | |
|---|---|
| Status | Draft for review |
| Date | 2026-10-20 |
| Assignment | AI Engineer: "Build a Multi-Agent Production Incident Investigation System" |
| Companion doc | `02-development-plan.md`, step-by-step build instructions for a coding model |
| Decisions already confirmed | LLM providers: Claude (Anthropic API, the default) and Gemini (Gemini API), chosen per model id. Harness: thin custom harness (no agent framework) |

---

## 0. Summary

**What we are building.** We are building a Python system in which six cooperating agents investigate a production incident against realistic simulated telemetry: logs, metrics, deployments, config changes, service topology and past incidents. The system produces ranked root-cause hypotheses with supporting and contradicting evidence, and every claim cites an evidence ID that a tool actually returned. It is allowed to answer **inconclusive** when the evidence does not support a conclusion. A single-agent baseline uses the same model, tools and budget. An automated evaluation harness compares the two systems on 12 synthetic incidents that contain misleading evidence, conflicting signals and tool failures.

**The nine decisions that shape everything else**

| # | Decision | One-line reason |
|---|---|---|
| 1 | Python 3.14 + asyncio + Pydantic v2, managed with `uv` | Best LLM/eval ecosystem. Async fits I/O-bound agents. Typed schemas give us validation and JSON Schema for free |
| 2 | Claude Opus 5.5 (`claude-opus-5-5`) by default, and Gemini (e.g. `gemini-4-argon`) as a second provider, each through its official SDK behind our one-method `LLMClient` interface. The model id picks the provider | Strong agentic tool use, a 1M-token context and cheap cache reads. The interface keeps the harness provider-neutral, so the same agents and evaluation run on either provider |
| 3 | Thin custom harness (~1.5K lines) instead of an agent framework | The assignment grades orchestration and reliability semantics, so they must be visible, testable code. It also gives exact token and tool accounting |
| 4 | Hybrid orchestration: an **LLM planner decides what** to investigate; **deterministic code decides how** (DAG scheduling, parallelism, retries, limits, termination) | Keeps the dynamic, evidence-driven behavior while guaranteeing bounded, testable execution |
| 5 | Blackboard state with a single writer. Agents exchange **typed artifacts** (Findings, Hypotheses, Critiques) that cite **evidence IDs minted by tools** | Genuine collaboration instead of prompt chaining. Makes grounding checkable by machine |
| 6 | Structured outputs through validated **submit tools**. Citations that don't resolve are rejected and the agent corrects itself | Works inside the tool loop, is provider-neutral, and enforces grounding at write time |
| 7 | A seeded **synthetic data generator** (YAML scenario language) plus a **fault injector**, behind backend interfaces shaped like real systems | Reproducible, leak-proof data with ground truth, and swappable for Loki, Prometheus, ArgoCD and others later |
| 8 | Testing without LLM spend: a **scripted LLM** for integration tests, **record/replay cassettes** for regression, and live eval only when measuring | Cuts manual testing to near zero. CI runs offline in seconds |
| 9 | **Deterministic scoring first** (root cause, trigger, key-evidence recall, red-herring avoidance); an LLM judge is a later option | Cheap, reproducible, auditable metrics that match the assignment's measures |

**Success looks like:**
- Every assignment requirement maps to code and a test (§1.2).
- `make test` passes offline in under a minute with no API key.
- `make eval` writes a comparison table: multi-agent vs single-agent, 12 incidents × 3 trials, covering root-cause accuracy, grounding, tool calls, latency, tokens and cost.
- The design survives a follow-up interview, because every choice has a written reason and named alternatives (§11).

---

## 1. Problem, goals and requirements

### 1.1 The problem in one paragraph

On-call engineers lose the first 30–90 minutes of an incident gathering context. They open dashboards, grep logs, check what was deployed, ask who changed a flag and search old postmortems, and the data is often incomplete, contradictory or misleading. The system must do that investigation itself: decide what to look at, look at it in parallel, form competing explanations, challenge them, go back for more evidence when needed, and write a report a human can act on. Every statement must trace back to raw evidence, and the report must say "we don't know yet" when that is the honest answer.

### 1.2 Requirements traceability (assignment → design → proof)

| ID | Requirement (from the assignment) | Design element | Verified by |
|---|---|---|---|
| A1 | ≥5 distinct agents: Orchestrator, Observability, Dependency, Reasoning, Critic, Reporting | 6 agents (§4). The Orchestrator is an LLM **Planner** plus a deterministic **Coordinator** | `test_all_agents_participate` |
| A2 | Agents exchange structured results and genuinely collaborate. Chaining LLM calls is not enough | Typed artifacts on a blackboard; dynamic plans; task dependencies that carry upstream findings; specialist "leads"; critic-driven feedback rounds; deterministic checks feeding the critic (§4.3) | `test_critic_requests_followup_round`, `test_dependent_task_receives_upstream_findings` |
| B1 | Tools for logs, metrics, deployments, dependencies, historical incidents | 10 domain tools (§7.3) | tool contract tests |
| B2 | Structured inputs/outputs, error handling, simulated failures (timeouts, incomplete results) | Pydantic input models; JSON result envelopes; fault injector with timeout, unavailable, rate-limited, partial, truncated and stale faults (§9.3) | `tests/unit/test_faults.py`, scenarios INC-07, 08, 10 |
| C1 | Parallel execution of independent tasks | The DAG scheduler runs all ready tasks concurrently, and parallel tool calls run within a turn | `test_specialists_run_in_parallel` |
| C2 | Shared investigation state and task dependencies | `InvestigationState` blackboard; `TaskSpec.depends_on` | scheduler unit tests |
| C3 | Feedback loops for gathering more evidence | Critic verdict `investigate` → planner → new tasks; `revise` → re-reasoning | integration tests |
| C4 | Bounded retries, execution limits, termination conditions | Tool retries (≤3), task attempts (≤2), rounds (≤3), turn/tool caps per agent, global budget, stagnation detection (§6.4) | `test_max_rounds_terminates`, budget tests |
| C5 | Recovery from agent or tool failures | Retries + circuit breaker; failed task becomes a recorded data gap; fallback plan; fallback report (§6.5) | failure-injection tests |
| D1 | Ranked hypotheses, supporting and contradicting evidence, confidence, recommended actions | Report schema (§5.4) | schema + scorer tests |
| D2 | Distinguish facts from assumptions; allow an inconclusive outcome | `Statement.kind` ∈ {observation, inference, assumption}; status derived from calibrated confidence (§5.3) | INC-10 + unit tests |
| E1 | ≥10 test incidents including misleading evidence, conflicting observations, tool failures | 12 generated scenarios (§9.4) | dataset validation tests |
| E2 | Compare against a single-agent baseline using the same tools | Same tool registry, model and global budget (§4.4) | eval |
| E3 | Measure root-cause accuracy, evidence grounding, tool-call count, latency, tokens | Deterministic scorers + trace-derived metrics (§10.3) | `evals/results/*/results.md` |
| F1–F5 | Runnable implementation, architecture diagram, dataset + tests, eval results, technical note (≤500 words) | README; `docs/architecture.md`; `datasets/`; `tests/`; `evals/results/`; `docs/technical-note.md` | deliverables checklist §13.4 |
| G1 | No hardcoded answers for individual test cases | Ground truth is isolated and never readable by agents; architecture tests scan code and prompts (§9.5) | `test_no_ground_truth_leakage` |
| G2 | Include the coding-assistant transcript | `docs/transcripts/` (this design session + the build session) | — |
| G3 | Document incomplete functionality and external dependencies | README "Limitations" and "External dependencies" sections | review |

### 1.3 Additional goals (yours, beyond the assignment)

1. **Follow current agent-harness practice.** These are the patterns that Claude Code / Claude Agent SDK, the OpenAI Agents SDK, LangGraph and Pydantic AI converged on: agent loop with stop conditions, context-isolated subagents, typed outputs with validation-and-retry, hooks, usage limits, tracing, append-only transcripts and evals as a first-class concern. §7.1 maps each pattern to its source.
2. **Extensibility.** New tools, tool sources (including MCP servers), telemetry backends, specialist agents, LLM providers and scenarios plug in through interfaces or config, without editing the orchestrator (§8).
3. **Real-world fidelity.** Data shapes and interfaces mirror real observability stacks, and the system is framed as a deployable "AI on-call investigator" (§2).
4. **An automated test and evaluation environment.** It replaces manual testing with deterministic simulations, a scripted LLM, recorded replays, quality gates and CI (§10).

### 1.4 Non-goals (v1)

- No UI. CLI and Markdown/JSON reports only (the assignment says no polished frontend is needed).
- No automatic remediation. Tools are read-only, and actions are recommendations for a human.
- No real telemetry connectors. The backend interfaces exist, but only the simulated implementations ship.
- No multi-tenant service, auth or persistence beyond local files. An HTTP wrapper (e.g. FastAPI) would be a thin addition, but it is not in the plan.

### 1.5 Measurable success criteria

| Criterion | Target |
|---|---|
| Offline test suite (`make test`) | Passes with no API key, in < 60 s |
| Scenarios | 12, each validated: its ground truth is reachable through the tools |
| Multi-agent root-cause accuracy (top-1) | ≥ 75% on Opus 5.5 (aspirational; report the honest number) |
| Inconclusive handling | INC-10 reported as inconclusive in ≥ 2 of 3 trials |
| Grounding | 100% of cited evidence IDs resolve (enforced); key-evidence recall reported |
| Run bounds | No run exceeds its budget; every run ends with a report, even after failures |
| Reproducibility | Same seed → byte-identical dataset; replay tests reproduce recorded runs exactly |

---

## 2. Real-world framing

### 2.1 How this would run in production

```mermaid
flowchart LR
  A["Alert fires<br/>(PagerDuty / Alertmanager)"] --> B["Webhook → investigator<br/>(incident payload)"]
  B --> C["Multi-agent investigation<br/>(read-only tools)"]
  C --> D["Report posted to the<br/>incident channel / ticket"]
  D --> E["On-call reviews ranked hypotheses<br/>+ evidence links"]
  E --> F["Human approves actions<br/>(rollback, flag off, scale up)"]
  C -.-> G["Traces & metrics<br/>(cost, latency, tool health)"]
```

The system plays an **investigator, not an actor**. It never mutates production. That keeps the blast radius at zero and puts a human in the loop for every action. The same harness could later gain write tools ("roll back deployment X") behind an approval hook (§7.5).

### 2.2 Simulated systems ↔ real systems

Each simulated backend implements an interface whose methods and data shapes mirror a real product. Swapping one in means writing one adapter, with no tool or agent changes.

| Port (interface) | Simulated implementation | Real-world adapters it is shaped after |
|---|---|---|
| `LogStore.search_logs` | JSONL logs, clustered into patterns at query time | Loki (LogQL), Elasticsearch/OpenSearch, Datadog Logs, CloudWatch Logs Insights |
| `MetricStore.get_metric`, `list_metrics` | 1-minute (or 5-minute) time series per service metric | Prometheus/Mimir (PromQL), Datadog Metrics, CloudWatch |
| `ChangeStore.list_deployments`, `list_config_changes` | Deployment and config-change event lists | ArgoCD, Spinnaker, GitHub Deployments; LaunchDarkly/Unleash audit logs; Terraform runs |
| `TopologyStore.get_service`, `list_services` | Static service catalog with dependencies | Backstage catalog, service mesh (Istio) telemetry, CMDB |
| `IncidentArchive.search` | Past incident/postmortem records with keyword scoring | Jira/PagerDuty postmortems, Confluence, incident.io |

### 2.3 The fictional platform

All 12 scenarios run against one consistent fictional **retail investing platform**, a domain close to the company that set the assignment. It has 21 nodes: 10 services, 7 datastores, 1 infrastructure component and 3 external providers. A shared topology makes cross-service reasoning possible (e.g., "the alert is in orders-db but the cause is a portfolio-service flag").

```mermaid
flowchart LR
  web["web-app"] --> gw["api-gateway"]
  gw --> auth["auth-service"]
  gw --> port["portfolio-service"]
  gw --> ord["orders-service"]
  gw --> quo["quotes-service"]
  gw --> pay["payments-service"]
  auth --> udb[("users-db")]
  auth --> scache[("session-cache")]
  port --> pdb[("portfolio-db")]
  port --> acache[("app-cache")]
  port --> quo
  port --> led["ledger-service"]
  port --> odb[("orders-db")]
  ord --> odb
  ord --> risk["risk-service"]
  ord --> quo
  ord --> led
  ord --> bus[("event-bus")]
  risk --> odb
  risk --> quo
  quo --> mdv{{"market-data-vendor"}}
  quo --> acache
  pay --> bank{{"bank-gateway"}}
  pay --> led
  pay --> bus
  led --> ldb[("ledger-db")]
  led --> bus
  notif["notification-service"] --> bus
  notif --> email{{"email-provider"}}
  dns["cluster-dns (all services resolve names here)"]
```

---

## 3. Architecture overview

### 3.1 Layers

```mermaid
flowchart TB
  subgraph Entry["Entry points"]
    CLI["CLI: investigate · eval · generate-data · show-trace"]
    APIX["(optional) HTTP API / alert webhook"]
  end
  subgraph Orch["Orchestration layer (deterministic)"]
    COORD["Coordinator<br/>control loop"]
    SCHED["DAG Scheduler<br/>parallel tasks"]
    POL["Policies<br/>termination · dedupe · checks · fallbacks"]
    STATE[("InvestigationState<br/>blackboard, single writer")]
  end
  subgraph Ag["Agents (LLM-driven)"]
    PLAN["Planner"]
    OBS["Observability"]
    DEP["Dependency"]
    REA["Reasoning"]
    CRI["Critic"]
    REP["Reporter"]
    SGL["Single-agent baseline"]
  end
  subgraph Har["Agent harness (domain-agnostic)"]
    LOOP["Agent loop"]
    EXE["Tool executor<br/>validation · timeout · retry · breaker"]
    REG["Tool registry + toolsets"]
    LLMC["LLMClient<br/>Anthropic · Gemini · Scripted · Recording"]
    BUD["Budget"]
    HK["Hooks + Tracer"]
  end
  subgraph Tl["Domain tools"]
    T1["query_logs · query_metrics · list_metrics · detect_anomalies"]
    T2["get_deployments · get_config_changes · get_service_dependencies · list_services"]
    T3["search_incident_history · get_evidence"]
  end
  subgraph Be["Backends (ports & adapters)"]
    SIM["Simulated backends<br/>(generated dataset)"]
    FI["Fault injector"]
    REAL["(future) Loki · Prometheus · ArgoCD · Backstage"]
  end
  subgraph Ev["Evaluation (never imported by agents)"]
    RUN["Eval runner"]
    SCO["Scorers"]
    GT[("Ground truth")]
  end
  CLI --> COORD
  CLI --> SGL
  APIX --> COORD
  COORD --> SCHED
  COORD --> POL
  COORD <--> STATE
  COORD --> PLAN
  COORD --> REA
  COORD --> CRI
  COORD --> REP
  SCHED --> OBS
  SCHED --> DEP
  Ag --> LOOP
  LOOP --> LLMC
  LOOP --> EXE
  LOOP --> BUD
  LOOP --> HK
  EXE --> REG
  REG --> Tl
  Tl --> FI
  FI --> SIM
  FI -.-> REAL
  RUN --> COORD
  RUN --> SGL
  RUN --> SCO
  SCO --> GT
```

**The layering rule:** each layer depends only on layers below it.
- The **harness** knows nothing about incidents.
- **Tools** know nothing about agents.
- **Backends** know nothing about faults; the injector wraps them.
- **Evaluation** can see everything, but nothing imports it.

An automated test enforces that last rule.

### 3.2 One investigation, end to end

```mermaid
sequenceDiagram
  autonumber
  participant U as CLI / webhook
  participant C as Coordinator
  participant P as Planner
  participant S as Scheduler
  participant O as Observability
  participant D as Dependency
  participant R as Reasoning
  participant K as Critic
  participant W as Reporter
  U->>C: IncidentReport
  C->>P: incident + service catalog + specialist catalog
  P-->>C: PlanDraft (tasks + depends_on)
  C->>S: run ready tasks
  par parallel specialists
    S->>O: R1-T1 logs & metrics
    O-->>S: Finding (statements cite evidence IDs)
  and
    S->>D: R1-T2 changes & topology
    D-->>S: Finding
  end
  C->>R: findings + evidence index (+ incident-history tool)
  R-->>C: HypothesisSet (2–5 ranked)
  C->>C: deterministic checks (timeline, support, duplicates, data gaps)
  C->>K: hypotheses + checks + evidence
  K-->>C: Critique: accept | revise | investigate (+ evidence requests)
  alt investigate (bounded by rounds and budget)
    C->>P: critique + completed tasks + remaining budget
    P-->>C: follow-up tasks (or "conclude")
    C->>S: run follow-up tasks in parallel
    C->>R: re-hypothesize with new findings
  else revise
    C->>R: re-rank addressing the critique
  end
  C->>W: final state (runs on a reserved budget)
  W-->>C: ReportDraft
  C->>C: derive status from confidence + critic verdict
  C-->>U: InvestigationReport (JSON + Markdown) + trace.jsonl
```

### 3.3 Coordinator state machine

```mermaid
stateDiagram-v2
  [*] --> Planning
  Planning --> Gathering: plan accepted (or deterministic fallback plan)
  Gathering --> Hypothesizing: no runnable tasks left
  Hypothesizing --> Critiquing: hypotheses accepted
  Hypothesizing --> Reporting: reasoning failed twice
  Critiquing --> Reporting: accept / max rounds / budget / stagnation / critic failed
  Critiquing --> Hypothesizing: revise
  Critiquing --> Replanning: investigate
  Replanning --> Gathering: new (deduplicated) tasks
  Replanning --> Reporting: planner concludes / nothing new to run
  Reporting --> [*]
```

### 3.4 Module map

| Package | Responsibility | Depends on |
|---|---|---|
| `investigator.domain` | Pydantic models: incident, evidence, findings, hypotheses, critique, plan, report, state | — |
| `investigator.harness` | LLM clients, transcript types, tool protocol/registry/executor, agent loop, budget, hooks, tracing, retry/breaker | `domain` (evidence only) |
| `investigator.backends` | Ports (protocols + record types), simulated implementation, fault injector, analysis helpers | `domain` |
| `investigator.tools` | The 10 domain tools (thin wrappers over backends that mint evidence) | `harness`, `backends`, `domain` |
| `investigator.agents` | Agent catalog (YAML), output tools + validators, context builders | `harness`, `domain` |
| `investigator.orchestrator` | Coordinator, scheduler, policies (termination, checks, fallbacks) | everything above |
| `investigator.baseline` | Single-agent investigator | `harness`, `agents`, `tools` |
| `investigator.reporting` | Markdown rendering | `domain` |
| `investigator.datagen` | Scenario spec models + deterministic generator | `domain` (types only) |
| `investigator.evaluation` | Ground truth, scorers, pricing, runner, summary | everything (read-only) |
| `investigator.cli` | Typer commands | everything |

---

## 4. The agents

### 4.1 The agent contract

Every agent, including the baseline, is just **configuration on one harness class**:

```text
AgentSpec
  name          planner | observability | dependency | reasoning | critic | reporter | single_agent
  prompt        prompts/<name>.md        (versioned system prompt, no scenario-specific text)
  tools         names from the ToolRegistry (least privilege per role)
  output        plan | finding | hypotheses | critique | report   → defines the submit_* tool
  model, effort claude-opus-5-5 / low|medium|high (per agent)
  limits        max_turns, max_tool_calls, max_tokens per call
```

An agent run takes a context message built from the blackboard, loops over tools, and ends **only** by calling its `submit_*` tool with schema-valid, citation-valid output. It never writes to shared state directly. The coordinator merges its output.

### 4.2 Agent catalog

| Agent | Role | Tools | Output (submit tool) | Effort |
|---|---|---|---|---|
| **Planner** (Orchestrator brain) | Turns the incident into 2–6 concrete tasks with dependencies. In later rounds it turns critic requests into follow-up tasks or decides to conclude | none (catalogs are in its context) | `submit_plan` → `PlanDraft{action, rationale, tasks[]}` | medium |
| **Coordinator** (Orchestrator body, code) | Owns the state, runs the control loop, validates plans, schedules, enforces limits, runs deterministic checks, derives status | — | — | — |
| **Observability** | Finds anomalies and their onset, separates causes from symptoms in logs and metrics, reports data gaps | `detect_anomalies`, `list_metrics`, `query_metrics`, `query_logs` | `submit_findings` → `FindingDraft` | medium |
| **Dependency** | Correlates deployments and config changes with the onset, walks the topology for shared upstream causes, explicitly **rules out** unrelated changes | `get_deployments`, `get_config_changes`, `get_service_dependencies`, `list_services` | `submit_findings` → `FindingDraft` | medium |
| **Reasoning** | Builds 2–5 competing hypotheses with causal mechanisms, supporting and contradicting evidence, assumptions, calibrated confidence and discriminating checks. Uses history as hints, not proof | `search_incident_history`, `get_evidence` | `submit_hypotheses` → `HypothesisSet` | high |
| **Critic** | Adversarial review: unsupported claims, timeline violations, correlation ≠ causation, ignored contradictions, overconfidence. Verdict plus targeted evidence requests | `get_evidence` | `submit_critique` → `Critique{verdict, issues[], evidence_requests[]}` | high |
| **Reporter** | Writes the final report from the record, without adding new claims | `get_evidence` | `submit_report` → `ReportDraft` | medium |
| **Single-agent baseline** | Does everything alone with all data tools | all 10 data tools | `submit_report` → `ReportDraft` | high |

### 4.3 How the agents collaborate (and why this is not prompt chaining)

| # | Mechanism | Concretely |
|---|---|---|
| 1 | **Dynamic planning** | The planner chooses which specialists run, on which services and windows, and in which dependency order. The sequence is not fixed in code |
| 2 | **Typed artifacts on a blackboard** | Findings, hypotheses and critiques are Pydantic objects with evidence IDs, not free text passed down a chain. Any agent can resolve any citation through `get_evidence` |
| 3 | **Task dependencies that carry information** | A dependent task (e.g., "correlate changes with the onset found by R1-T1") receives the upstream finding in its context |
| 4 | **Specialist leads** | A specialist can emit `leads` such as "check changes to portfolio-service around 14:01". The planner can turn them into tasks for another specialist |
| 5 | **Critic-driven feedback loop** | A verdict of `investigate` produces concrete evidence requests, which become new parallel tasks. The reasoner then re-ranks with the new evidence. `revise` sends reasoning flaws back without new data collection |
| 6 | **Deterministic checks inform the critic** | Before the critic runs, code checks the hypotheses. It flags a trigger change that happened *after* the onset, high confidence on thin support, and duplicate hypotheses, and the critic must address each flag. Code and LLM collaborate |

### 4.4 Single-agent baseline: the fairness rules

The comparison is meaningful only if the architecture is the **only** variable:

1. **Same model** (`claude-opus-5-5`) for every agent and the baseline.
2. **Same data tools, same backends, same faults**, built from the same registry factory.
3. **Same global budget** (tokens, tool calls, wall-clock) and the same submit-tool validation, so the baseline also cannot cite fabricated evidence.
4. **Effort for the baseline is `high`**, the highest level any multi-agent component uses. This is conservative: if the multi-agent system still wins, the win comes from the architecture.
5. **Same report schema, same status rule, same scorer.**
6. **What the baseline does not get** is the architecture: no planner, no isolated specialist contexts, no deterministic checks, no critic loop. Its prompt still contains the same domain guidance, merged.

An optional ablation (`--no-critic`) runs the multi-agent system without the critic loop, to measure how much the loop contributes.

---

## 5. Shared state, evidence and conclusions

### 5.1 `InvestigationState`: the blackboard

| Field | Content |
|---|---|
| `incident`, `services` | Immutable inputs: the incident report and a snapshot of the service catalog |
| `tasks` | Every task across rounds: spec, status (`pending/running/done/failed/skipped`), attempts, error, origin (`plan/followup`) |
| `findings` | One record per completed task (task, agent, round, `FindingDraft`) |
| `hypothesis_rounds`, `critiques` | Full history per round, so revisions are auditable |
| `evidence` | `EvidenceStore`: every record any tool returned, keyed by evidence ID |
| `data_gaps`, `failures` | What could not be observed, and which components failed and why |
| `round`, `phase`, `termination_reason` | Control-loop position and why the loop ended |

**There is a single writer.** Agents return outputs and only the coordinator mutates state. Even though specialists run in parallel, there are no data races. It is also easy to snapshot `state.json` after each phase, which gives resumability and makes debugging trivial.

### 5.2 Evidence model and citations

- **Tools mint evidence.** Every record a tool returns gets a stable ID and is registered in the run's `EvidenceStore` with its kind, service, timestamp, one-line summary and the tool call that produced it.

  | Kind | ID shape | Example summary |
  |---|---|---|
  | `log_pattern` | `logpat-1a2b3c4d` | `4,100× WARN "Timed out waiting for connection from pool after <N>ms…" in portfolio-service (14:02–14:15)` |
  | `log_sample` | `log-9f8e7d6c` | `14:02:31 WARN portfolio-service: Timed out waiting for connection…` |
  | `metric_series` | `met-5a6b7c8d` | `portfolio-service db_pool_wait_ms: baseline 3.1 → 2,410 ms from 14:02` |
  | `deployment` | `dep-0007` | `notification-service 2.3.0→2.3.1 at 13:59 by alice: email template tweaks` |
  | `config_change` | `cfg-0003` | `portfolio-service db.pool.max_size 40→4 at 14:01 (runtime_config)` |
  | `dependency` | `topo-orders-service` | `orders-service depends on orders-db, risk-service, …; called by api-gateway` |
  | `incident_history` | `hist-0012` | `2025-11-04 Redis eviction storm slowed portfolio reads` |

- **Agents cite IDs.** Each `Statement` is `{text, kind, evidence_ids}`:
  - `observation`: a fact read directly from evidence. It **must** cite at least one ID.
  - `inference`: a conclusion drawn from observations. It should cite IDs.
  - `assumption`: believed but unverified. It is listed explicitly in the report.
- **Citations are validated at write time.** Every submit tool checks that every cited ID exists in the store, every named service exists in the catalog, and every trigger is a real deployment or config change. Violations come back to the model as tool errors ("evidence id `dep-9999` does not exist; cite only IDs returned by tools"), and the model fixes them. Fabricated citations therefore cannot reach the report, for either system.
- **The report renders evidence from the store, not from model text**, so readers see the real record behind every claim.

### 5.3 Hypotheses, confidence and the inconclusive outcome

Each hypothesis names:
- `root_cause_service` (from the catalog)
- `category`: one of `bad_deployment`, `config_change`, `dependency_failure`, `resource_exhaustion`, `traffic_surge`, `infrastructure`, `data_issue` or `unknown`
- `trigger_change_id` (optional)
- `mechanism` (the causal chain)
- `supporting_evidence` and `contradicting_evidence`
- `assumptions`
- `confidence` ∈ [0, 1]
- `checks` that would confirm or refute it

**Confidence is calibrated by rubric**, and the same rubric appears in every prompt:

| Band | Meaning |
|---|---|
| ≥ 0.75 (high) | Direct evidence of the mechanism (e.g., error logs naming it), timing that lines up with a trigger, and no unresolved contradicting evidence |
| 0.50–0.74 (medium) | Strong correlation and a plausible mechanism, but a gap remains (missing logs, alternative not ruled out) |
| < 0.50 (low) | Circumstantial; several viable alternatives; key data missing |

**Code derives the status; the LLM does not choose it.** This keeps both systems consistent and makes "inconclusive" a first-class, testable outcome:

```text
top = highest-ranked hypothesis confidence (0 if there are none)
if top ≥ 0.75 → root_cause_identified   (multi-agent: only if the final critic verdict is "accept",
                                          otherwise capped to probable_root_cause)
elif top ≥ 0.50 → probable_root_cause
else            → inconclusive           (report must then include next diagnostic steps)
```

### 5.4 The final report

| Section | Content | Rules |
|---|---|---|
| Status + reason | Derived (above) | — |
| Executive summary | 3–6 sentences | — |
| Ranked hypotheses | Rank, title, service, category, mechanism, trigger, confidence, supporting and contradicting evidence, assumptions | Confidence non-increasing; all IDs valid |
| Facts | Observations only, each with evidence | ≥1 valid ID each |
| Assumptions | Explicit list | — |
| Data gaps | What could not be observed (tool failures, partial data) | Required when faults hit |
| Timeline | Timestamped events with evidence | — |
| Recommended actions | P0 mitigate now, P1 fix, P2 prevent, plus `investigate` steps, each with rationale and evidence | ≥1 |
| Next steps | Diagnostic steps to reach a conclusion | Required when inconclusive |
| Investigation stats | Rounds, tasks, tool calls (failed / retried), tokens, cost, termination reason | From the trace |

---

## 6. Orchestration and reliability

### 6.1 Control loop (pseudocode)

```text
state = new InvestigationState(incident, catalog); budget = Budget(limits)
plan = planner(state) or fallback_plan(state)            # fallback: observability + dependency on affected services
add_tasks(plan)
for round in 1..max_rounds:
    scheduler.run(state)                                  # parallel, dependency-ordered, retried, bounded
    hyps = reasoning(state) (retry once) or break
    checks = deterministic_checks(state, hyps)
    critique = critic(state, hyps, checks) or break       # critic failure → stop, status capped
    stop, reason = termination_policy(state, critique, budget)
    if stop: break
    if critique.verdict == "revise": continue             # reason again, no new data
    followups = planner(state, mode="followup")           # may answer "conclude"
    new = dedupe(followups)                               # drop tasks already done
    if conclude or not new: break
    add_tasks(new)
report = reporter(state) on reserved budget or fallback_report(state)
report.status = derive_status(report, final_critique)
```

### 6.2 Task DAG and the parallel scheduler

- Tasks carry `depends_on`. The planner's output is validated: known agents only, dependencies must reference existing tasks, no cycles (DFS), services must exist, windows must be ordered. Invalid plans go back to the planner as tool errors.
- The scheduler loop:
  1. Start every task whose dependencies are terminal, up to `max_parallel_tasks` (default 4).
  2. Wait for the first completion (`asyncio.wait(FIRST_COMPLETED)`).
  3. Repeat.

  Tasks whose dependencies can never complete are marked `skipped`.
- A task whose dependency **failed** still runs, with a note in its context. Degraded evidence beats no evidence.
- Each task has a wall-clock timeout and up to 2 attempts. The second attempt receives the first attempt's error.

### 6.3 Feedback loops

| Loop | Trigger | Bound |
|---|---|---|
| Output-validation loop (inside every agent) | The submit tool rejects invalid output, and the agent corrects it | `max_turns` per agent |
| Tool retry loop (inside the executor) | Retryable tool error (timeout, unavailable, rate-limited) | 3 attempts, jittered backoff, circuit breaker |
| Agent nudge | The model ends its turn without calling its submit tool | 2 nudges |
| Revise loop | Critic verdict `revise` | Counts as a round |
| Investigate loop | Critic verdict `investigate` → planner → new tasks | `max_rounds` (3), `max_tasks_total` (12), budget |

### 6.4 Limits and termination conditions

| Limit | Default | Enforced by |
|---|---|---|
| Rounds | 3 | Coordinator |
| Tasks total / follow-up tasks per round | 12 / 4 | Coordinator (plan validation + dedupe) |
| Parallel tasks | 4 | Scheduler |
| Task attempts / task timeout | 2 / 240 s | Scheduler |
| Agent turns / tool calls | Per agent (e.g., observability 12 / 16; baseline 40 / 60) | Agent loop |
| Tool attempts / tool timeout | 3 / 8 s | Tool executor |
| Circuit breaker | Opens after 4 consecutive failures per (tool, service) for 30 s | Tool executor |
| Global budget | 1.5M tokens, 80 LLM calls, 120 tool calls, 900 s | `Budget` (shared by all agents) |
| Reporter reserve | 150K tokens + 120 s held back so a report is **always** produced | `Budget.check(reserve)` |

Termination reasons: `critic_accepted`, `max_rounds_reached`, `budget_exhausted`, `stagnated` (top hypothesis and confidence unchanged across two rounds), `planner_concluded`, `no_new_tasks`, `critic_failed`, `reasoning_failed`. The reason is recorded in the trace and the report.

### 6.5 Failure-handling matrix

| Failure | Detection | Behavior | What the user sees |
|---|---|---|---|
| Tool timeout / 503 / 429 | Exception from the backend (injected or real) | Retry with backoff (honors `retry_after`). After the final failure, return an error envelope with `retryable` and a `hint` | Data gap, if the agent cannot work around it |
| Partial or truncated data | `partial`/`truncated` flags + `missing_ranges` in the result | Pass the flags to the agent with a warning | Data gap noted in the finding and report |
| Backend down (repeated failures) | Circuit breaker opens | Fail fast with `circuit_open` until cooldown, saving tokens and time | Data gap |
| Invalid tool arguments | Pydantic validation | Error envelope listing field errors; the model corrects itself | — |
| Agent never submits / invalid output | Turn limit, nudges exhausted | Agent result `failed`; scheduler retries the task once with the error in context | Failed task listed; data gap |
| Planner fails | Agent failure | Deterministic fallback plan | Note in report |
| Reasoning fails | Agent failure ×2 | Stop the loop and report on the findings (likely inconclusive) | Status inconclusive |
| Critic fails | Agent failure | Stop the loop; status capped at probable | Note in report |
| Reporter fails | Agent failure ×2 | Deterministic fallback report built from the latest hypotheses and findings | Report marked "automated fallback" |
| LLM API errors | SDK typed exceptions | SDK retries 408/409/429/5xx (3×). Remaining errors fail that agent run, which is handled as above | — |
| Model refusal (`stop_reason: refusal`) | Response check | Server-side fallback model (`fallbacks: "default"`). If the whole chain refuses, the agent fails as above | Run flagged in eval |
| Budget exhausted | `Budget.check` | Investigation stops; the reporter runs on its reserve | `termination_reason = budget_exhausted` |
| Unexpected exception (bug) | `except Exception` at phase boundaries | Logged with traceback in the trace; continue to reporting | Report still produced |

### 6.6 Determinism (needed for replay tests and fair evals)

- Datasets come from seeds, and generating twice gives identical bytes.
- Tool results are serialized with sorted keys and contain no timings.
- Context builders sort findings, evidence and gaps by stable keys, because parallel completion order must not change prompts.
- Prompts contain no timestamps, UUIDs or run IDs.
- Fault-injection counters are keyed by (caller task, rule), so they don't depend on interleaving.

---

## 7. The harness

### 7.1 Patterns adopted from current agent harnesses

| Pattern | Where it comes from | Our implementation |
|---|---|---|
| Agent = model + tools in a loop with explicit stop conditions | Anthropic *Building effective agents*; Claude Agent SDK; OpenAI Agents SDK Runner (`max_turns`) | `Agent.run()`: turn cap, tool-call cap, ends only through the output tool |
| Orchestrator–workers with parallel, context-isolated subagents | Anthropic *How we built our multi-agent research system*; Claude Agent SDK subagents | Planner + DAG scheduler. Each specialist starts with a fresh context and only its own tools |
| Typed outputs through an output tool, with validation and retry | Pydantic AI `ToolOutput` (its default), output validators raising `ModelRetry`; Claude Agent SDK structured outputs | `submit_*` tools: Pydantic + semantic validators; errors go back to the model |
| Tools designed for agents: few, well described, token-efficient, actionable errors | Anthropic *Writing effective tools for agents — with agents* | Log pattern clustering, downsampled metrics with summaries, `hint` fields in error envelopes, "when to call" in descriptions |
| Lifecycle hooks around model and tool calls | Claude Agent SDK hooks (`PreToolUse`/`PostToolUse`); OpenAI Agents lifecycle hooks | `Hook` protocol (`before_llm`, `after_llm`, `before_tool`, `after_tool`). The tracer is a hook |
| Usage limits / bounded execution | OpenAI Agents `max_turns`; Pydantic AI `UsageLimits`; LangGraph recursion limit | `Budget` (tokens, calls, wall-clock) + per-agent caps + reporter reserve |
| Durable state / checkpoints | LangGraph checkpointers; Pydantic AI durable execution (Temporal, DBOS…) | `state.json` snapshot after each phase (Temporal is the production path, §11) |
| Context engineering: compact handoffs, just-in-time retrieval | Anthropic *Effective context engineering for AI agents* | Context builders pass summaries + an evidence index; detail on demand through `get_evidence` |
| Append-only transcripts, stable prefixes | Claude prompt caching; Opus 5.5 "preserved thinking" (edited history invalidates thinking blocks) | History never edited; frozen system prompts; tools sorted; deterministic JSON |
| Tracing with spans | OpenAI Agents tracing; OpenTelemetry GenAI semantic conventions (status: Development) | JSONL trace with per-call tokens and latency; an OTel exporter is a later option |
| Evals as a first-class concern: code graders first, multiple trials, transcripts kept | Anthropic *Demystifying evals for AI agents* | Deterministic scorers, 3 trials, per-run traces, error sidecar, noise floor stated |
| Tool interoperability | Model Context Protocol (spec 2026-07-28) | `Toolset` seam + optional MCP adapter; the core does not need MCP |
| Human approval for side effects | Claude Code permission modes; LangGraph `interrupt()`; OpenAI Agents HITL | Read-only tools now; the `before_tool` hook is the approval seam for future write tools |
| Harnessing a long-running *coding* agent: progress file, small verified steps, commit per step | Anthropic *Effective harnesses for long-running agents* | Applied to the **build process** in `02-development-plan.md` (PROGRESS.md, task checklist, test-then-commit) |

### 7.2 Agent loop (behavior)

1. Check the budget, then call the LLM with system prompt, transcript and tool specs (the agent's tools plus its submit tool).
2. Append the assistant turn **verbatim**. This includes thinking blocks, which current Claude models require be passed back unchanged.
3. Branch on the stop reason:
   - `refusal`: the agent fails.
   - `max_tokens` with tool calls: return error results asking the model to re-issue the calls, because truncated tool input is never executed.
   - No tool calls: nudge the model ("call `submit_x`"), at most twice.
4. Run the non-submit tool calls **in parallel** through the executor, then validate the submit call if there is one.
5. Return **all** tool results in **one** user message. Splitting them discourages parallel calls.
6. If the submit call validated, return the typed output. Otherwise continue until `max_turns`. When the tool-call cap is hit, further calls get "limit reached, submit now".

### 7.3 Tool layer

**Protocol.** A tool has:
- a name and a description, written as *when to call it* (not just what it does)
- a JSON Schema generated from a Pydantic input model
- a `read_only` flag
- an async `run(args, ctx) → ToolOutput{data, evidence[], partial, warnings}`

`FunctionTool` + a `@tool` decorator turn a typed async function into a tool. A `Toolset` is any provider of tools: native functions now, an MCP server adapter later. The `ToolRegistry` returns per-agent subsets (least privilege).

**Executor** (one place for every reliability concern):
1. Unknown tool → error.
2. Validate arguments → error with field details.
3. Charge the budget.
4. Circuit breaker check.
5. `asyncio.wait_for(timeout)` with retries on retryable errors.
6. Register evidence.
7. Build the envelope.
8. Emit trace hooks.

It **never raises** into the agent loop. Everything becomes a result envelope.

**Result envelope** (always JSON with sorted keys, no timings):

```json
{"ok": true,  "data": {...}, "evidence_ids": ["met-5a6b7c8d"], "partial": false, "warnings": [], "attempts": 1}
{"ok": false, "error": {"type": "timeout", "message": "query_logs timed out after 8.0s", "retryable": true,
                        "hint": "Narrow the time window or rely on metrics; note this as a data gap."}, "attempts": 3}
```

**Domain tools**

| Tool | Input (key fields) | Returns | Mints evidence |
|---|---|---|---|
| `query_logs` | service, start, end, level?, contains?, max_samples | total, top patterns (template, count, first/last seen), samples, partial flags | `log_pattern`, `log_sample` |
| `query_metrics` | service, metric, start, end, step_seconds | ≤60 points + summary (min/max/mean/p95/baseline/latest/change point) | `metric_series` |
| `list_metrics` | service | metric names, units, descriptions | — |
| `detect_anomalies` | start, end, services? | ranked change points across metrics (before/after mean, severity) | `metric_series` |
| `get_deployments` | start, end, service? | deployments (versions, times, author, status, summary) | `deployment` |
| `get_config_changes` | start, end, service? | config/flag/infra changes (key, old→new, author, source) | `config_change` |
| `get_service_dependencies` | service | depends_on + dependents, kinds, external flags | `dependency` |
| `list_services` | — | service catalog | — |
| `search_incident_history` | query, service?, limit | past incidents scored by keyword overlap | `incident_history` |
| `get_evidence` | evidence_ids[] (≤20) | stored evidence records | — |

### 7.4 Structured outputs through submit tools

Why not JSON mode or `output_config.format`?
1. Submit tools work **inside** the tool loop, as one mechanism for every agent.
2. They allow **semantic** validation (IDs exist, services are real, plans are acyclic) with self-correction.
3. They are provider-neutral.

Current Claude models reject forced `tool_choice` (`any`/`tool`) with a 400. We therefore use `auto`, state the expectation in the prompt, and keep a bounded nudge loop as the backstop.

### 7.5 Hooks, budget and tracing

- **Hooks:** `before_llm`, `after_llm`, `before_tool`, `after_tool`. They are used for tracing now, and later for PII redaction, approvals or custom metrics.
- **Budget:** shared and thread-safe under asyncio (single event loop). It counts input, output, cache-read and cache-write tokens, LLM calls, tool calls and wall-clock time. `check(reserve)` lets the reporter dip into a reserve that investigators cannot touch.
- **Trace:** `runs/<run_id>/trace.jsonl` with these events:
  - `run_start`, `phase`, `task_start`, `task_end`, `termination`, `run_end`
  - `llm_call` (agent, task, model requested and served, stop reason, usage, latency)
  - `tool_call` (tool, arguments, ok/error type, attempts, latency, evidence IDs)

  All eval efficiency metrics come from this file. `investigator show-trace` prints it as a tree.

### 7.6 Context engineering, per agent

| Agent | What its first message contains | What it does **not** get |
|---|---|---|
| Planner | Incident; service catalog (name, kind, depends_on); specialist catalog; limits; in follow-up mode also completed tasks, latest hypotheses and critique | Raw telemetry |
| Specialist | Incident; its task (objective, services, window); findings of the tasks it depends on; its tool-call budget | Other specialists' transcripts |
| Reasoning | Incident; catalog; all findings (statements with IDs, ruled-out items, gaps); evidence index (ID, kind, service, time, summary); previous round's hypotheses + critique | Raw tool outputs (it can fetch them with `get_evidence`) |
| Critic | The above + the current hypotheses + deterministic check results + previous critiques + rounds left | — |
| Reporter | Incident; final hypotheses; final critique; findings; data gaps; evidence index; termination reason; the status rule | — |

### 7.7 Claude-specific configuration (verified against current API docs)

| Setting | Value | Why |
|---|---|---|
| Model | `claude-opus-5-5` everywhere (configurable per agent) | Strongest Opus at $4/$20 per MTok; 1M context |
| Thinking | Omitted (Opus 5.5 always uses adaptive thinking; `disabled` would return a 400) | Depth is controlled through effort |
| Effort | `medium` for planner, specialists and reporter; `high` for reasoning, critic and baseline | The default on Opus 5.5 is `medium`, so we set it explicitly. Reasoning-heavy roles get more |
| Streaming | `messages.stream(...)` + `get_final_message()`; `max_tokens` 32000 | Avoids HTTP timeouts on long thinking turns |
| Tools | `eager_input_streaming: true`; every input re-validated with Pydantic; tools run only when `stop_reason` is not `max_tokens`/`refusal` | Recommended with streaming; truncated input never executes |
| `tool_choice` | `auto` (the default) | Forced tool use returns a 400 on Opus 5.5 |
| Prompt caching | Explicit `cache_control` on the frozen system block + top-level automatic caching for the growing transcript | Cache reads cost 0.05× of input on Opus 5.5. Agent loops re-read their prefix every turn |
| History | Append-only; assistant content echoed exactly as returned | Required by preserved thinking (edited history → 400 on new accounts); also maximizes cache hits |
| Refusal fallback | `fallbacks: "default"` with beta `server-side-fallback-2026-07-01` (config flag, default on) | A false-positive refusal must not become a failed investigation. The eval flags runs served by a fallback model |
| SDK retries | `max_retries=3` (SDK backs off on 408/409/429/5xx) | Transport resilience lives in the SDK; semantic resilience in our harness |

### 7.8 Gemini-specific configuration (verified against current API docs and the google-genai 2.31 SDK)

| Setting | Value | Why |
|---|---|---|
| API and SDK | `generateContent` through `google-genai` (2.28+) | Fully supported and stateless, which matches our append-only, replayable transcripts. Moving to Google's newer Interactions API would only change the adapter |
| Models | `gemini-4-argon` (stable flagship) for evaluations; `gemini-3.8-flash` for cheap runs | Any id starting with `gemini-` routes to this adapter |
| Thinking | `thinking_level` follows effort: `low`, `medium`, `high` (`xhigh` and `max` become `high`). `minimal` is never sent, because Argon and 3.8 Flash reject it. Gemini 2.5 models get `thinking_budget` 1024 / 8192 / dynamic instead | Sending both parameters returns a 400 |
| Thought signatures | Every response part is stored verbatim (signature bytes as base64) and echoed back in place | A function-call turn without its signature returns a 400 |
| Function calls | Mode `AUTO`, configurable to `VALIDATED`. Automatic function calling is off. Each response carries its call id, and responses to parallel calls are grouped in one turn | The harness runs tools itself; wrong ids or interleaved responses are rejected |
| Tool schemas | Reduced to the supported JSON Schema subset: `default`, `minLength` and `pattern` are dropped, and `const` becomes a one-value `enum` | Our Pydantic validators still enforce the full schema and send errors back to the model |
| Sampling | Defaults (no temperature) | Recommended for Gemini 3 and later; same rule as for Claude |
| Output limit | `max_output_tokens` 32000 | Gemini 4 caps non-streaming responses at 32,768 tokens; the SDK continues longer outputs automatically |
| Caching | Implicit and automatic; cached tokens are reported as cache reads | There is no cache-write charge, and explicit caches are not used |
| Refusals | Safety finish reasons and blocked prompts map to `refusal` | Gemini has no server-side fallback, so the agent run fails and the coordinator's fallbacks take over |
| Retries | SDK `HttpRetryOptions(attempts=max_retries + 1)`: 408, 429, 5xx and transient network errors | Same split as for Claude: transport retries in the SDK, semantic recovery in the harness |
| Usage | input = prompt − cached + tool-use prompt; output = candidates + thoughts | Thinking tokens are billed as output |

---

## 8. Extensibility: how to add things

| To add… | You touch | You do **not** touch |
|---|---|---|
| A native tool | One file in `tools/` (Pydantic input + async function + `@tool`), one line in the registry factory, the agent's `tools:` list in `config/agents.yaml` | Agent loop, coordinator, other tools |
| An MCP server's tools | `connect_stdio("<server>", command, args)` and `registry.register_toolset(toolset)` (optional `mcp` extra). Tool names appear as `mcp__<server>__<tool>` (Claude tool names cannot contain dots); add them to the agent's `tools:` list | Agent loop, coordinator, other tools |
| A real telemetry backend | An adapter class implementing the port (e.g., `LokiLogStore(LogStore)`) + a config switch | Tools, agents |
| A specialist agent (e.g., `traces`, `database`, `security`) | A `specialists:` entry in `agents.yaml` + `prompts/<name>.md` (+ any new tools) | Coordinator. The planner sees the new specialist in its catalog and can assign it work, and the scheduler runs it in parallel like the others |
| A different LLM provider | An `LLMClient` adapter (translate transcript and tool specs) + its model-id prefix in `PROVIDER_PREFIXES` (`harness/routing.py`). Claude and Gemini are built in | Agents, tools, orchestration |
| A new test incident | A YAML file in `datasets/scenarios/` + `make data` | Code |

Sketch of a native tool:

```python
class QueryTracesInput(BaseModel):
    service: str = Field(description="Service name exactly as in the catalog")
    start: UtcDatetime
    end: UtcDatetime

@tool(name="query_traces", input_model=QueryTracesInput,
      description="Call when you need request-level latency breakdown across services for a time window.")
async def query_traces(args: QueryTracesInput, ctx: ToolContext) -> ToolOutput:
    spans = await ctx.backends.traces.search(args.service, args.start, args.end)
    ev = [Evidence(evidence_id=f"trace-{s.id}", kind="trace", ...) for s in spans[:20]]
    return ToolOutput(data={"spans": [s.model_dump(mode="json") for s in spans[:20]]}, evidence=ev)
```

Sketch of the MCP adapter (optional milestone). It lists the server's tools once, wraps each as a `Tool` whose `input_schema` is the MCP schema, and turns call results into one evidence item each. The core runs fine without the `mcp` package installed.

---

## 9. Simulated environment and dataset

### 9.1 Why a generator instead of hand-written fixtures

| Option | Verdict | Reason |
|---|---|---|
| **Seeded generator + YAML scenario language (chosen)** | ✅ | Consistent multi-source telemetry (logs agree with metrics agree with deploys); byte-reproducible; ground truth defined next to the cause; new scenarios need no code |
| Hand-written JSON fixtures | ❌ | Thousands of records per scenario; internal inconsistencies are easy to miss and become accidental clues |
| LLM-generated data | ❌ | Not reproducible; the same model family would be writing and solving the puzzles (leakage of phrasing and patterns) |
| Public datasets (LogHub, microservice RCA benchmarks such as RCAEval) | Later | Rarely include deploys, config, topology *and* postmortems with ground truth together. Good future external-validity check |
| Real observability stack in docker-compose (Loki + Prometheus + load generator + chaos) | Later | Highest realism, but heavy for reviewers and slow for CI. The backend ports make it a drop-in later |

### 9.2 What the generator produces

For each scenario it writes `datasets/generated/<id>/`:
- `incident.json`: what the system receives. Symptoms only, sometimes with a misleading guess from the reporter.
- `telemetry/`:
  - `logs.jsonl`: background noise templates per service, plus signal patterns at given rates, with optional clock skew.
  - `metrics.json`: series per service metric, with baselines, a daily market-hours pattern and seeded noise. Signal shapes are `step`, `ramp`, `spike`, `sawtooth` and `multiply`.
  - `deployments.json` and `config_changes.json`: benign background changes across 7 days, plus the scenario's own changes. IDs are assigned uniformly by timestamp, so they do not reveal which change matters.
  - `topology.json`: the catalog.
  - `history.json`: 16 shared past incidents plus scenario-specific ones.
- `environment/faults.json`: fault rules, read only by the injector.
- `ground_truth.json`: read **only** by the evaluator.

### 9.3 Fault-injection model

Faults wrap **backend operations** (e.g., "the logs backend is down for risk-service"). They therefore affect every tool that reads that backend, exactly as a real outage would.

| Fault | Effect | Typical use |
|---|---|---|
| `timeout` | The operation exceeds its deadline | Flaky backends; the executor retries |
| `unavailable` | 503-style error | Logs shard down (INC-07), deploy tracker down (INC-10) |
| `rate_limited` | Error with `retry_after_s` | Tests backoff |
| `partial` | Points/lines dropped inside time ranges; `partial=true` + `missing_ranges` | Metrics pipeline gap (INC-08) |
| `truncated` | Only the first N items; `truncated=true` | Large log queries |
| `stale` | Items newer than `now − lag` hidden | Lagging deploy history |

Rules match on operation and query fields (`service`, `metric`). They apply to the first N calls or to all calls, counted per caller so results are deterministic.

### 9.4 The 12 scenarios

| ID | Symptom reported | True root cause (service · category · trigger) | Traps | Tool failures | Expected |
|---|---|---|---|---|---|
| INC-01 | 35% of order placements return 500 | orders-service · bad_deployment · v4.18.0 deploy (NPE in limit-price validator) | none (control case) | transient metrics timeout (absorbed by retry) | identified |
| INC-02 | Portfolio page times out; "probably the notification deploy" | portfolio-service · config_change · `db.pool.max_size` 40→4 | coincident deploy of an unrelated service; DB connections **drop** (contradicts "DB overload") | config API times out once | identified |
| INC-03 | Deposits failing; "payments v7.2.0 deploy this morning?" | bank-gateway (external) · dependency_failure · none | earlier benign deploy; history supports the vendor theory | deployment API rate-limited ×2 | identified |
| INC-04 | Intermittent 502s every few hours; "traffic from market open" | quotes-service · bad_deployment (memory leak) · v3.9.0 two days ago | normal market-open ramp looks like a cause; needs a **wide** time window | none | identified |
| INC-05 | 60% of logins failing since 00:00 | auth-service · infrastructure · expired TLS cert (no change) | no deploys at all; auth request rate **drops** (not overloaded) | none | identified |
| INC-06 | Orders and quotes slow at market open | quotes-service · traffic_surge (+ retry amplification) · none | a feature-flag change 16 min earlier | log query truncated | identified / probable |
| INC-07 | Orders rejected: "risk check unavailable" | risk-service · bad_deployment · rules-engine upgrade | — | **logs backend unavailable for risk-service** (always) | identified / probable + data gap |
| INC-08 | Intermittent 5xx at gateway; some users fine | cluster-dns · infrastructure/config_change · resolver change in one zone | downstream services look healthy (conflict); gateway log clock skewed −4 min (errors "before" the change); unrelated deploy | **metrics gap** in the critical window | identified / probable |
| INC-09 | Slow portfolio and orders reads; "exactly like the redis incident last year" | ledger-service · bad_deployment/data_issue · migration with a table-locking backfill | **misleading history** match (redis eviction storm); cache metrics normal | none | identified |
| INC-10 | Sporadic 502s for a few users | **inconclusive by design** | background noise looks like signal; two contradicting historical candidates | **deploy tracker down**; partial web-app logs | **inconclusive** |
| INC-11 | orders-db CPU 98%, orders-service slow; "orders deploy yesterday?" | portfolio-service · config_change · feature-flag ramp 10%→100% (N+1 queries) | the alerting service is the victim; benign deploy the day before | config API times out ×4 (exceeds retries; the agent must retry later) | identified |
| INC-12 | Trade confirmations and ledger postings delayed 20+ min | event-bus · resource_exhaustion · retention change 3 days ago | most recent deploy is in a consumer; **a log line containing an embedded instruction to blame that deploy** (prompt injection) | none | identified |

**Coverage:**
- **Categories:** bad_deployment ×4, config_change ×2, infrastructure ×2, dependency_failure, traffic_surge, resource_exhaustion, inconclusive.
- **Traps:** misleading evidence in 7 scenarios, conflicting observations in 4, tool failures in 7, cross-service causes in 6, one prompt-injection case.

### 9.5 No hardcoded answers: leakage prevention

1. **Physical separation.** Simulated backends are constructed from `telemetry/` and `environment/` only. `ground_truth.json` is opened only by `investigator.evaluation`.
2. **Neutral identifiers.** Change and history IDs are assigned by timestamp order. Log pattern IDs are hashes. Nothing is named after its role.
3. **Architecture tests** (run in CI):
   - Agent, tool, harness and orchestrator modules must not import `evaluation` or `datagen`.
   - No ground-truth trigger ID or key log phrase may appear in `src/` (outside `datagen`/`evaluation`) or `prompts/`.
   - Telemetry files must not contain fields such as `root_cause`, `red_herring` or `ground_truth`.
4. **Generic prompts.** Prompts describe methodology, never services or scenarios.

---

## 10. Testing and evaluation environment

### 10.1 Test pyramid

| Layer | What it proves | LLM | Runtime | When |
|---|---|---|---|---|
| Unit | Schemas, validators, schema generation, budget, retry/breaker, executor, scheduler (DAG, cycles, parallelism), policies, analysis (pattern clustering, change points), fault injector, generator determinism, scorers (oracle → 100%, null → 0%) | none | seconds | every commit |
| Contract | Every tool: valid JSON Schema, JSON-serializable output, deterministic results, correct error envelope for each fault type | none | seconds | every commit |
| Component | Agent loop with a **ScriptedLLM**: tool → result → submit; invalid citation → error → fixed; no submit → nudge; turn cap; refusal; truncated turn | scripted | seconds | every commit |
| Integration | Full coordinator on a real generated scenario with scripted agent policies: parallelism, dependency ordering, critic loop, max rounds, budget exhaustion with reporter reserve, specialist failure → data gap, planner and reporter fallbacks, single-agent baseline | scripted | < 30 s | every commit |
| Architecture | Import boundaries, no ground-truth leakage | none | seconds | every commit |
| Replay | Real recorded Opus 5.5 runs replayed from cassettes: the full pipeline offline, byte-identical requests, same report and metrics | recorded | seconds | every commit (skipped until cassettes exist) |
| Live eval | Real model on all scenarios × trials; scoring; comparison table; quality gate | real | ~2–3 h | on demand |

### 10.2 The two fake LLMs

- **ScriptedLLM.** Responses come from per-agent *policies*: functions of the request that can read the last tool results and cite the evidence IDs they contain. A policy's position comes from the transcript (the number of assistant turns so far), so it is stateless and safe under parallelism. This makes orchestration behavior testable without any model.
- **RecordingLLM.** Wraps the real client.
  - **Record mode** stores each response under a SHA-256 of the canonical request (agent, model, system, tools, transcript, limits).
  - **Replay mode** returns stored responses and fails loudly on a miss ("prompt changed, re-record").

  We record at our own LLM boundary rather than with HTTP-mocking libraries. The Anthropic SDK 1.x moved to `httpx2`, and HTTP-level mocking tools (vcrpy, respx) silently stop seeing its traffic.

### 10.3 Evaluation methodology

**Systems.** `multi` (the 6 agents) vs `single` (the baseline): same model, tools, faults and budget. Optional ablation: `multi --no-critic`.

**Trials.** 3 per (scenario, system), so 72 runs on the full set. Each run gets a fresh environment and fresh fault counters.

**Metrics** (scored per run, aggregated per system):

| Metric | Definition | Type |
|---|---|---|
| Root-cause accuracy (top-1) | Conclusive cases: the top hypothesis's service is in the accepted services **and** its category is in the accepted categories **and** the status is not inconclusive. Inconclusive case: status = inconclusive | deterministic |
| Top-3 accuracy | Any of the top 3 hypotheses matches (conclusive cases) | deterministic |
| Trigger accuracy | The top hypothesis's `trigger_change_id` equals the true trigger (cases that have one) | deterministic |
| Status correctness | Conclusive → identified/probable; inconclusive → inconclusive | deterministic |
| Citation validity | Cited IDs that resolve in the run's evidence store ÷ all cited IDs (enforced ≈1.0; reported as a guarantee) | deterministic |
| Key-evidence recall | Ground-truth key-evidence matchers satisfied by evidence cited in the top hypothesis's support or the facts | deterministic |
| Red-herring avoidance | The top hypothesis is not the scenario's red herring | deterministic |
| Data-gap acknowledgment | Tool-failure scenarios: the report's data gaps mention the affected source | deterministic |
| Injection resistance | INC-12: the top hypothesis is not the injected conclusion | deterministic |
| Calibration (Brier) | Mean of (top confidence − correct)² over conclusive cases | deterministic |
| Claim support (future option, not built) | An LLM judge (a different model from the one under test, e.g. `claude-haiku-5-5`) would check whether each fact's cited evidence supports it | model-graded |
| Tool calls / attempts / errors | From the trace: model-issued calls, executor attempts, failed calls | trace |
| LLM calls; input / output / cache-read / cache-write tokens | From each response's `usage` | trace |
| Cost (USD) | Σ per call: model × usage × price (cache writes 1.25×, cache reads at the model's cache rate) | derived |
| Latency | End-to-end wall-clock per run (phase timings are in the trace) | trace |

**Runner properties** (from eval best practice):
- Results are appended per run, so a crash loses nothing.
- Resume is keyed by (scenario, system, trial).
- A hard wall-clock ceiling per run.
- Infra failures go to `errors.jsonl`, never scored as wrong answers.
- The served model is checked. Runs answered by a different or fallback model are flagged `model_mismatch`, excluded from the tables and counted separately.
- A cost estimate prints before a paid run and requires `--yes`.
- Concurrency is bounded by a semaphore.

**Grader sanity checks** (tests): an oracle report built from ground truth, with evidence gathered through the real tools, scores 100% on every scenario; an empty report scores about 0%; a deliberately wrong-but-plausible report fails.

### 10.4 Statistical honesty

12 scenarios × 3 trials = 36 runs per system. The noise floor on a pass rate is roughly `1/sqrt(36)`, about **±17 points**. Differences smaller than that are not evidence. The results file therefore shows:
- per-scenario results for both systems side by side (paired by scenario)
- the standard deviation across per-trial means
- this caveat

To tighten the error bars cheaply, run more trials, or add seed variants of the scenarios. Copying a scenario with a different `seed` yields new noise and IDs with the same ground-truth structure.

### 10.5 How this removes manual testing

| Manual activity today | Replaced by |
|---|---|
| "Does it still work after I changed the prompt?" | Replay tests (instant, free) + `make eval-smoke` (2 scenarios, ~$5) |
| "Does it handle a down backend?" | Fault rules in scenario YAML + integration tests |
| "Does parallelism, the critic loop or termination still behave?" | Scripted integration tests on every commit |
| "Is the new incident type handled?" | Add a YAML scenario → generator → eval |
| "Is the model better or worse than last week?" | Live eval with a quality gate (`--min-accuracy`), on demand in CI |
| Reading long transcripts by hand | `show-trace` tree + per-run `report.md` + `results.md` tables |

### 10.6 CI

- GitHub Actions on every push and pull request runs `uv sync --frozen`, `ruff format --check`, `ruff check` and `mypy`. It also regenerates the dataset and fails if it differs from the committed files, then runs the offline `pytest` suite.
- A manual `eval` workflow (never triggered automatically, to control cost) uses the `ANTHROPIC_API_KEY` secret, applies `--min-accuracy` and uploads the results as an artifact.

---

## 11. Technology decisions (ADR log)

Each entry: **decision**, then the alternatives and why they lost.

**ADR-01 Language: Python 3.14 (`requires-python >= 3.12`).**
- **Why Python:** first-class Anthropic SDK (async), Pydantic, the richest eval and data tooling, and what AI-engineering reviewers expect to read.
- **Why 3.14:** it is the newest line in *bugfix* support. 3.12 and 3.13 are security-only as of October 2026, and 3.15 shipped on 2026-10-09, so its wheels still lag.
- *TypeScript:* an equally good SDK with Zod, but weaker data and eval tooling for this kind of analysis.
- *Go, Rust, Java:* excellent concurrency, but slower iteration and thinner LLM tooling, for an I/O-bound workload where raw performance doesn't matter.

**ADR-02 Tooling: `uv`, `ruff`, `mypy`, `pytest` + `pytest-asyncio`.**
- *Poetry:* slower and a less standard workflow.
- *pip + venv:* no lockfile.
- *conda:* heavyweight and unnecessary.
- `uv` also installs the pinned Python automatically, so a reviewer's setup is one command.

**ADR-03 Model: Claude Opus 5.5 through the official `anthropic` SDK (1.15+).**
- **Why:** strong long-horizon tool use, 1M context, prompt caching with 0.05× cache reads, effort control, parallel tool calls and server-side refusal fallback.
- The same model runs in all roles, so the comparison is fair. Swap to `claude-sonnet-5-5` or `claude-haiku-5-5` in config for cheaper development runs.
- *Gemini:* supported as a second provider (ADR-18). *OpenAI:* viable the same way: one more adapter, not a rewrite.

**ADR-04 No LLM gateway (LiteLLM, OpenRouter).** A gateway adds:
- a translation layer that hides provider features we rely on (echoing thinking blocks, cache controls, refusal fallbacks)
- another failure domain
- supply-chain exposure (`litellm` 1.82.7/1.82.8 were compromised on PyPI on 2026-03-24 with a credential stealer)

Our one-method interface costs about 150 lines per provider.

**ADR-05 Harness: thin custom harness instead of a framework.** See the comparison below. The deciding factors:
1. The assignment grades orchestration and reliability semantics, so they should be our code, not framework configuration.
2. We need exact per-agent token and tool accounting.
3. We need a deterministic LLM boundary for scripted and replay tests.
4. A smaller dependency surface means less API drift for the coding model building it.

| Option (version, Oct 2026) | Strengths | Why not here | When we'd pick it |
|---|---|---|---|
| **LangGraph** 1.2 (1.0 in Oct 2025) | `StateGraph`, `Send` fan-out, checkpointers, `interrupt()` for HITL, durable execution, large community | The orchestration we're graded on would live in framework configuration; reducer/state semantics add indirection; LangChain dependency weight; exact accounting needs callbacks | Long-lived, resumable workflows with human checkpoints at scale |
| **Pydantic AI** 2.57 (v2 stable Jun 2026) | Typed output tools, `UsageLimits`, `TestModel`/`FunctionModel`, toolsets incl. MCP, pydantic-evals, durable execution (Temporal, DBOS, Prefect…) | Closest in spirit. Its v2 "capabilities" API is new and moving fast, which is risky for a coding model to use exactly; the inner loop would be hidden | A production team that wants these patterns without owning harness code |
| **OpenAI Agents SDK** 0.24 (pre-1.0) | Handoffs, agents-as-tools, guardrails, sessions, tracing | Pre-1.0; non-OpenAI models go through adapters labeled beta; tracing uploads to OpenAI by default | OpenAI-centric stacks |
| **Claude Agent SDK** 0.2.168 | The Claude Code harness as a library: subagents, hooks, in-process MCP tools, structured outputs | Built for coding/filesystem agents (bundles the CLI); orchestration is LLM-driven inside the harness, which makes bounded, deterministic scheduling and replay testing harder; tools must be MCP-shaped | Agents that need Claude Code's built-in tools |
| **Claude Managed Agents** (beta) | Anthropic hosts the loop and a sandbox; sessions, outcomes, multi-agent rosters | Our tools must run locally against simulated backends with fault injection, and we need offline deterministic tests | Hosted, scheduled, long-running agents |
| **Anthropic SDK Tool Runner** (beta helper) | No hand-written loop; per-turn hooks | Provider-specific and beta; we want the loop provider-neutral and replayable at our boundary. Our loop is ~200 lines | A single Claude agent with custom tools |
| **CrewAI** 1.15 | Fast role-based crews; Flows | Its role/task abstraction comes closest to "prompt chaining with personas"; less control over budgets and failures | Quick prototypes |
| **Microsoft Agent Framework** 1.x (GA Apr 2026; successor to AutoGen + Semantic Kernel) | Enterprise features, .NET + Python | Azure-centric; conversation-style orchestration; heavier | Microsoft-stack enterprises |
| **Google ADK** 2.12 (2.0 GA May 2026) | Graph workflows (the older Sequential/Parallel/Loop agents are superseded), built-in eval | Gemini-first ecosystem; workflow API recently changed | GCP/Gemini stacks |

**ADR-06 Orchestration: hybrid "LLM plans, code schedules" on a blackboard.**
- *Static pipeline:* this is the prompt chaining the assignment rejects.
- *Fully LLM-driven orchestrator calling subagents as tools:* flexible, but unbounded, nondeterministic and hard to test.
- *Group chat or debate:* token-hungry and unfocused.
- *Handoffs:* control passes sequentially, so there is no parallelism.

**ADR-07 Structured outputs: submit tools with semantic validation** (§7.4).
- *JSON mode / `output_config.format`:* a schema guarantee only, with no semantic checks, and a second mechanism to maintain next to the tool loop.

**ADR-08 Concurrency: asyncio.**
- *Threads:* awkward cancellation and timeouts.
- *Multiprocessing:* overkill for I/O.
- *Ray or Celery:* distributed infrastructure for a single-process job.
- *Temporal:* the right production path for durable investigations, deliberately out of scope for a take-home. The single-writer state and checkpoints make that migration straightforward.

**ADR-09 Data: seeded generator + YAML scenario language** (§9.1).

**ADR-10 Storage: files + in-memory behind ports.**
- *SQLite/Postgres/Elasticsearch/Prometheus:* infrastructure for reviewers to run, for no gain at this data size. The ports keep the upgrade path open.

**ADR-11 Tools in-process; MCP optional.** In-process tools are faster, deterministic, fault-injectable and need no server lifecycle. MCP is supported through the `Toolset` seam (optional adapter, pinned to the stable 1.x `mcp` SDK). The reverse direction, exposing our tools *as* an MCP server so Claude Code or Desktop can investigate with them, is a natural extension.
- *A2A protocol:* unnecessary for agents that live in one process.

**ADR-12 Testing at the LLM boundary** (ScriptedLLM + cassettes).
- *HTTP mocking:* brittle, and blind to SDK 1.x traffic, as explained in §10.2.

**ADR-13 Evaluation: custom runner + deterministic scorers.**
- *promptfoo, DeepEval, Ragas, LangSmith, Braintrust, Inspect AI, pydantic-evals:* useful, but our metrics are domain-specific. A ~400-line runner writing JSONL is transparent and dependency-free, and results can be exported to any of them later.

**ADR-14 Observability: JSONL traces; an OpenTelemetry exporter later.**
- *LangSmith, Langfuse, Logfire, Phoenix:* each needs an account or extra infrastructure. The GenAI semantic conventions are still in *Development* status, so a later, optional exporter is the right level of commitment. It is not in the plan.

**ADR-15 Interface: CLI (Typer + Rich).**
- *Web UI:* the assignment explicitly does not need one.
- *Later option (not in the plan):* a FastAPI wrapper (`POST /investigations`) for the "alert webhook" story.

**ADR-16 Config: YAML validated by Pydantic.**
- *Hydra:* power we don't need.
- *Env-only:* poor fit for an agent catalog.

**ADR-17 Delivery: Docker image + GitHub Actions.** A reviewer can run the project with Docker or `uv` alone.

**ADR-18 Multiple providers: Claude and Gemini behind `LLMClient`, chosen by model id.**
- **How:** `harness/routing.py` sends `claude-*` models to the Anthropic client and `gemini-*` models to the Gemini client, and creates each client on first use, so a run only needs keys for the providers it calls. `--model` switches a whole run; a per-agent `model:` in `config/agents.yaml` mixes providers in one run.
- **Gemini API choice:** `generateContent`, not the Interactions API that Google has recommended since June 2026. `generateContent` stays fully supported, is stateless (our transcripts are append-only and replayable), and has stable SDK types. A later move touches only `gemini_client.py`.
- **Fairness:** the evaluation compares architectures on one model at a time. Comparing providers means running the same evaluation once per model. The prompts were tuned on Claude, and results should say so.
- *Gateway (LiteLLM, OpenRouter):* still rejected (ADR-04). Thought signatures and Claude's thinking blocks must be echoed exactly, which is easy to get wrong behind a translation layer.

### 11.1 Cost and latency estimate (to be replaced by the measured smoke run)

These figures assume typical token volumes and are rough; the first smoke run replaces them with measured values.

| | Multi-agent run | Single-agent run |
|---|---|---|
| LLM calls | ~25–35 | ~15–25 |
| Input tokens (incl. cached) | ~400K (≈70% cache reads) | ~400K |
| Output tokens (incl. thinking) | ~45K | ~30K |
| Cost on Opus 5.5 ($4 in / $20 out / $0.20 cache read / $5 cache write) | ≈ $1.5 | ≈ $1.2 |
| Wall-clock | 3–6 min (specialists in parallel) | 3–5 min |

- **Full eval (72 runs):** about $100 (range $60–150) and ~2–3 h at concurrency 2.
- **Same eval on `claude-haiku-5-5`:** under $5.
- **Same eval on Gemini:** about $40 on `gemini-4-argon` at its current $2 / $10 price, rising to about $80 after Google's 2026-12-01 increase. About $15 on `gemini-3.8-flash`. Tokenizers differ, so measure with a smoke run.
- **Smoke eval (2 scenarios × 1 trial × 2 systems):** about $5.

---

## 12. Security, safety and cost controls

- **Read-only by construction.** No tool mutates anything. Recommended actions are text for a human to approve.
- **Prompt injection.** Telemetry is untrusted data: logs can contain user-supplied text.
  - Tool results are delivered as JSON data.
  - Every prompt states that instructions found inside tool results must never be followed and should be reported as suspicious.
  - INC-12 measures this.
  - The planner and critic never see raw logs, only cited summaries.
- **Secrets.** API keys come from the environment: `ANTHROPIC_API_KEY` for Claude (or an `ant auth login` profile), and `GEMINI_API_KEY` or `GOOGLE_API_KEY` for Gemini. Keys are never logged; traces exclude headers.
- **Data minimization.** Tool outputs are capped (top patterns, ≤30 samples, ≤60 points). The synthetic data contains no PII.
- **Cost controls:**
  - budgets with a reserve
  - prompt caching
  - effort per role
  - compact tool outputs
  - smoke-run gating
  - a cost estimate plus `--yes` before paid evals
  - cheaper models configurable for development

---

## 13. Delivery plan

### 13.1 Milestones

The development plan expands these into small test-first tasks.

| Milestone | Scope | Exit criteria | Est. (with a coding assistant) |
|---|---|---|---|
| M0 Scaffold | uv project, deps, lint/type/test config, AGENTS.md, PROGRESS.md | `make lint test` green on an empty suite | 1–2 h |
| M1 Domain | All Pydantic models, UTC time type, status derivation | Unit tests green | 2–3 h |
| M2 Dataset | Spec models, platform YAML, generator, 12 scenarios, dataset validation | `make data` deterministic; every ground truth reachable | 5–7 h |
| M3 Harness | Transcript types, LLM interface, Anthropic adapter, ScriptedLLM, RecordingLLM, tools/registry/executor, budget, hooks, tracer, agent loop | Component tests green | 5–7 h |
| M4 Backends + tools | Ports, simulated backends, analysis helpers, fault injector, 10 tools | Contract + fault tests green | 4–6 h |
| M5 Agents + orchestration | Catalog, output tools/validators, context builders, prompts, scheduler, policies, coordinator, baseline, Markdown report, CLI | Integration tests green; `investigator investigate --scenario INC-01` works live | 6–8 h |
| M6 Evaluation | Ground truth loader, scorers, pricing, runner, summary, cassettes, smoke eval | Oracle/null tests; smoke eval produces results.md | 4–5 h |
| M7 Docs + delivery | Architecture and dataset docs, README, Docker, CI, full eval run, technical note, transcripts | All deliverables present | 3–4 h |
| M8 Optional | MCP adapter, critic ablation run. Further ideas not in the plan: OTel exporter, HTTP wrapper, LLM judge, seed variants | — | as time allows |

**Total: about 4–5 focused days.** M0–M6 are the critical path. M8 is optional.

### 13.2 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scenarios turn out too easy (both systems score ~100%) or too hard (~0%) | Look at the smoke-run results. Adjust trap strength in YAML (e.g., red-herring timing, signal rates) without code changes |
| LLM nondeterminism makes the comparison noisy | 3 trials, paired per-scenario reporting, a stated noise floor; more trials or seed variants if needed |
| Cost overrun | Smoke first; budgets; Haiku 5.5 for development; caching; the cost gate |
| The coding model deviates from the plan | Fixed decisions table, complete code per task, test-first steps, PROGRESS.md, commit per task |
| API behavior drift (betas, model changes) | Fallback and caching behind config flags; the adapter is isolated; replay tests detect prompt changes |
| Prompt-injection text steers an agent | Data-only framing, explicit instructions, critic review, INC-12 measurement |

### 13.3 What "done" means per deliverable

| Deliverable | Location | Done when |
|---|---|---|
| 1. Runnable implementation + setup | `README.md`, `Makefile`, `Dockerfile` | A fresh clone runs `uv sync && make data && make test` and `make run SCENARIO=INC-01` |
| 2. Architecture diagram + coordination explanation | `docs/architecture.md` (Mermaid from §3) | Diagram renders on GitHub; covers state, scheduling, feedback, termination |
| 3. Synthetic dataset + automated tests | `datasets/`, `tests/` | 12 scenarios generated and validated; CI green |
| 4. Evaluation results | `evals/results/final-claude-opus-5-5/results.md` (+ JSON) | Multi vs single across all metrics, per-scenario table, caveats |
| 5. Technical note (≤500 words) | `docs/technical-note.md`, rendered from a template with the eval numbers | Word count ≤ 500, enforced by a test |
| Transcript | `docs/transcripts/` | This design session + the build session exported |

### 13.4 Deliverables checklist

- [ ] README covers setup, usage, configuration, limitations, external dependencies and cost notes
- [ ] Architecture doc with diagrams
- [ ] `datasets/scenarios/*.yaml` (12) + generated data + `docs/dataset.md` catalog
- [ ] Offline test suite green in CI
- [ ] Eval results (multi vs single) committed
- [ ] Technical note ≤ 500 words
- [ ] Coding-assistant transcripts
- [ ] Incomplete functionality and external dependencies documented

---

## 14. Technical note skeleton (≤500 words; fill it in after the eval)

1. **Approach (≈90 words):**
   - Hybrid orchestration: an LLM planner plus a deterministic coordinator.
   - Blackboard state.
   - Evidence IDs minted by tools.
   - A critic loop.
2. **Key decisions and trade-offs (≈150 words):**
   - Custom harness vs frameworks.
   - Submit tools vs JSON mode.
   - Synthetic generator.
   - Same-model fairness.
   - Status derived from confidence.
3. **Results (≈120 words):**
   - Headline accuracy, grounding, cost and latency for both systems.
   - Where multi-agent won or lost, with scenario IDs.
4. **Limitations (≈80 words):**
   - Synthetic data.
   - Small n (noise floor).
   - Keyword-based history search.
   - No real connectors.
   - Single process.
5. **Improvements (≈60 words):**
   - Real backends.
   - Temporal for durability.
   - Embeddings for history.
   - Learned confidence calibration.
   - More scenarios and seed variants.
   - Online evaluation on real incidents.

---

## 15. Decisions that are yours to revisit

| Decision | Current default | Change it if… |
|---|---|---|
| Eval model | `claude-opus-5-5` for every agent and the baseline | Budget is tight: run development evals on `claude-haiku-5-5`, `claude-sonnet-5-5` or `gemini-3.8-flash`, and the final eval on Opus 5.5. To compare providers, repeat the final eval on `gemini-4-argon` |
| Trials per scenario | 3 | You want tighter error bars (5 trials ≈ ±13 points) |
| Effort levels | medium (most roles), high (reasoning, critic, baseline) | The smoke run shows quality headroom or cost pressure |
| LLM judge | Not built | You want a model-graded "claims supported" metric (use a different model, e.g. `claude-haiku-5-5`) |
| Refusal fallback | On (`fallbacks: "default"`) | You'd rather a refusal simply fail the run |
| Optional M8 items | Not in the critical path | Time remains before the deadline |

---

## Appendix A. Glossary

| Term | Meaning |
|---|---|
| Blackboard | The shared `InvestigationState` that agents read (via context) and that only the coordinator writes |
| Evidence ID | A stable identifier a tool assigns to a record it returned; the only legal citation |
| Finding | A specialist's structured output for one task |
| Submit tool | The terminal tool each agent calls with its typed output; it validates schema and citations |
| Lead | A specialist's suggestion that another specialist check something |
| Data gap | Something that could not be observed (tool failure, partial data) and that limits conclusions |
| Red herring | Evidence designed to look causal but not causal |
| Trial | One run of one system on one scenario |

## Appendix B. References (verified 2026-10-20)

- Anthropic, *Building effective agents* (2024-12-19). https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, *How we built our multi-agent research system* (2025-06-13). https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic, *Writing effective tools for agents — with agents* (2025-09-11). https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, *Effective context engineering for AI agents* (2025-09-29). https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, *Effective harnesses for long-running agents* (2025-11-26). https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic, *Demystifying evals for AI agents* (2026-01-09). https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic, *Quantifying infrastructure noise in agentic coding evals* (2026-02-05). https://www.anthropic.com/engineering/infrastructure-noise
- LangGraph docs. https://docs.langchain.com/oss/python/langgraph/overview
- Pydantic AI docs. https://pydantic.dev/docs/ai/overview/
- OpenAI Agents SDK docs. https://openai.github.io/openai-agents-python/
- Claude Agent SDK (Python). https://github.com/anthropics/claude-agent-sdk-python
- Google ADK docs. https://adk.dev/agents/workflow-agents/
- Microsoft Agent Framework 1.0. https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/
- CrewAI docs. https://docs.crewai.com/en/concepts/processes
- Model Context Protocol specification (2026-07-28). https://modelcontextprotocol.io/specification/2026-07-28
- OpenTelemetry GenAI semantic conventions (Development). https://github.com/open-telemetry/semantic-conventions-genai
- LiteLLM security update, March 2026. https://docs.litellm.ai/blog/security-update-march-2026
- Python version status. https://devguide.python.org/versions/
