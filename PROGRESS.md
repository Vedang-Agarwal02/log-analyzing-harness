# Build progress

Tick a box when the task's tests pass and the commit is made. Log plan fixes as `PLAN-FIX: ...` and type ignores as `TYPE-IGNORE: ...`.

- [x] 0.1 Project scaffold
- [x] 1.1 Common types
- [x] 1.2 Incident & evidence models
- [x] 1.3 Findings, hypotheses, critique, plan models
- [x] 1.4 Report & state models
- [ ] 2.1 Scenario spec models
- [ ] 2.2 Platform model & platform.yaml
- [ ] 2.3 Generator
- [ ] 2.4 Scenarios INC-01..06
- [ ] 2.5 Scenarios INC-07..12
- [ ] 3.1 Neutral modules & LLM types
- [ ] 3.2 Schema & tool abstractions
- [ ] 3.3 Retry & budget
- [ ] 3.4 Hooks & tracing
- [ ] 3.5 Tool executor
- [ ] 3.6 Scripted LLM
- [ ] 3.7 Agent loop
- [ ] 3.8 Anthropic client
- [ ] 3.9 Recording client
- [ ] 4.1 Backend ports
- [ ] 4.2 Analysis helpers
- [ ] 4.3 Simulated backends
- [ ] 4.4 Fault injector
- [ ] 4.5 Log & metric tools
- [ ] 4.6 Change, topology, history, evidence tools & registry
- [ ] 5.1 Prompts
- [ ] 5.2 Settings & config
- [ ] 5.3 Output tools & validators
- [ ] 5.4 Agent catalog
- [ ] 5.5 Context builders
- [ ] 5.6 Policies
- [ ] 5.7 Scheduler
- [ ] 5.8 Coordinator
- [ ] 5.9 Baseline, Markdown report & runtime
- [ ] 5.10 CLI
- [ ] 5.11 Integration tests
- [ ] 5.12 Architecture tests
- [ ] 6.1 Ground truth & scoring
- [ ] 6.2 Pricing
- [ ] 6.3 Eval runner
- [ ] 6.4 Summary & eval CLI
- [ ] 6.5 Cassettes, replay test & live smoke
- [ ] 7.1 Docs
- [ ] 7.2 README
- [ ] 7.3 Docker & CI
- [ ] 7.4 Full eval, technical note, transcripts
- [ ] 8.1 MCP toolset adapter (optional)
- [ ] 8.2 Critic ablation run (optional)

## Log
- 2026-10-20: 0.1 done. Docs copied from the Desktop project folder (the path in 00-execution-guide.md section 5 did not exist). Smoke test passes; ruff clean.
- 2026-10-20: 1.1 done. test_domain_common.py: 4 passed.
- 2026-10-20: 1.2 done. test_domain_incident_evidence.py: 5 passed.
- 2026-10-20: 1.3 done. test_domain_agent_outputs.py: 5 passed.
- 2026-10-20: 1.4 done. test_domain_report_state.py: 11 passed. M1 check: pytest 26 passed, mypy clean.
