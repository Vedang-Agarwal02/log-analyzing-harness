# Incident investigation: INC-02 - Portfolio page timing out

**Status:** Root cause identified (top confidence 0.95 >= 0.75)
**System:** single_agent | **Termination:** completed

## Executive summary

At 14:01 UTC, an automated capacity review change (cfg-0006) reduced portfolio-service's db.pool.max_size from 40 to 4. Because portfolio-service normally utilizes approximately 12 database connections under standard traffic (~183 rps), this change immediately caused severe connection starvation. Database pool wait time jumped from ~3 ms to over 2,300 ms, driving p99 latency to over 8.2 seconds and triggering database connection acquisition timeouts for GET /api/v1/portfolio requests (portfolio-service error rate rose to ~11.3%, and web-app error rate rose to ~3.9%). The notification-service deployment at 13:59 UTC was coincidental and unrelated to the failure.

## Ranked hypotheses

### 1. Runtime configuration change downsized db.pool.max_size to 4, starving portfolio-service of database connections (confidence 0.95, high)

- **Root-cause service:** portfolio-service
- **Category:** config_change
- **Trigger:** cfg-0006
- **Mechanism:** Runtime configuration change cfg-0006 reduced portfolio-service's db.pool.max_size from 40 to 4. Because the service routinely utilizes ~12 connections to satisfy normal traffic (~183 rps), the 4-connection cap caused severe pool contention. Requests were forced to wait over 2 seconds to acquire a connection (db_pool_wait_ms jumping from ~3 ms to over 2,300 ms), leading to connection acquisition timeouts, p99 latencies exceeding 8.2 seconds, and elevated 5xx error rates across portfolio-service and web-app.
- **Supporting evidence:**
  - `cfg-0006`: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
  - `met-eb2b7c3c`: portfolio-service db_pool_in_use: 11.872 -> 4.062 count from 2026-03-11T14:01:00Z (max 15.123)
  - `met-bcb9c30a`: portfolio-service db_pool_in_use: 11.818 -> 4.062 count from 2026-03-11T14:01:00Z (max 13.663)
  - `met-3c8b0ae1`: portfolio-db active_connections: 35.073 -> 5.022 count from 2026-03-11T14:01:00Z (max 40.272)
  - `met-e3c57dc8`: portfolio-db active_connections: 35.341 -> 5.022 count from 2026-03-11T14:01:00Z (max 38.673)
  - `met-d4a2d97e`: portfolio-service db_pool_wait_ms: 3.035 -> 2386.557 ms from 2026-03-11T14:02:00Z (max 3527.988)
  - `met-9e8d8aa8`: portfolio-service db_pool_wait_ms: 3.855 -> 1830.293 ms from 2026-03-11T13:55:00Z (max 3527.988)
  - `met-630f1043`: portfolio-service latency_p50_ms: 54.955 -> 1910.756 ms from 2026-03-11T14:02:00Z (max 2042.3)
  - `met-94fba730`: portfolio-service latency_p99_ms: 256.836 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
  - `met-f0207b18`: portfolio-service latency_p99_ms: 248.111 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
  - `met-51108f6f`: portfolio-service error_rate_pct: 0.298 -> 11.333 % from 2026-03-11T14:03:00Z (max 14.219)
  - `met-98989961`: portfolio-service error_rate_pct: 0.279 -> 10.85 % from 2026-03-11T14:02:00Z (max 14.219)
  - `met-0d2cc40b`: web-app error_rate_pct: 0.197 -> 3.886 % from 2026-03-11T14:03:00Z (max 5.092)
  - `met-1aa78d26`: web-app error_rate_pct: 0.229 -> 3.201 % from 2026-03-11T13:58:00Z (max 5.092)
  - `logpat-f6a782a6`: 690x WARN "Timed out waiting for connection from pool after <N>ms (pool=portfolio-db, max=<N>)" in portfolio-service (2026-03-11T14:02:01Z to 2026-03-11T14:24:59Z)
  - `log-e92aba56bb`: 2026-03-11T14:02:01Z WARN portfolio-service: Timed out waiting for connection from pool after 2189ms (pool=portfolio-db, max=4)
  - `logpat-4a133374`: 264x ERROR "Request GET /api/v<N>/portfolio failed: timeout acquiring database connection" in portfolio-service (2026-03-11T14:03:00Z to 2026-03-11T14:24:59Z)
  - `log-722011b09b`: 2026-03-11T14:03:00Z ERROR portfolio-service: Request GET /api/v1/portfolio failed: timeout acquiring database connection
- **Contradicting evidence:**
  - none
- **Assumptions:**
  - The runtime config dynamically resized connection pools across portfolio-service pods.

### 2. Deployment of notification-service 2.3.1 caused downstream instability (confidence 0.03, low)

- **Root-cause service:** notification-service
- **Category:** bad_deployment
- **Trigger:** dep-0009
- **Mechanism:** The notification-service deploy (dep-0009) starting at 13:59 UTC introduced instability that propagated to portfolio-service.
- **Supporting evidence:**
  - `dep-0009`: notification-service 2.3.0 -> 2.3.1 deployed 2026-03-11T13:59:00Z by alice (succeeded): Tweak trade confirmation email templates
- **Contradicting evidence:**
  - `topo-portfolio-service`: portfolio-service depends on [app-cache, cluster-dns, ledger-service, orders-db, portfolio-db, quotes-service]; depended on by [api-gateway]
  - `met-23594927`: notification-service error_rate_pct: no significant change; mean 0.102, max 0.138 % (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
  - `met-07eef516`: notification-service latency_p99_ms: no significant change; mean 201.061, max 230.171 ms (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
  - `logpat-f6a782a6`: 690x WARN "Timed out waiting for connection from pool after <N>ms (pool=portfolio-db, max=<N>)" in portfolio-service (2026-03-11T14:02:01Z to 2026-03-11T14:24:59Z)
  - `cfg-0006`: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
- **Assumptions:**
  - An undocumented dependency or shared infrastructure contention exists between notification-service and portfolio-service.

### 3. Traffic surge exhausted connection pools on portfolio-service (confidence 0.02, low)

- **Root-cause service:** portfolio-service
- **Category:** traffic_surge
- **Trigger:** none identified
- **Mechanism:** A sudden surge in incoming customer traffic exhausted portfolio-service database connection pools.
- **Supporting evidence:**
  - `met-9e8d8aa8`: portfolio-service db_pool_wait_ms: 3.855 -> 1830.293 ms from 2026-03-11T13:55:00Z (max 3527.988)
  - `met-f0207b18`: portfolio-service latency_p99_ms: 248.111 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
- **Contradicting evidence:**
  - `met-fb817f95`: portfolio-service request_rate_rps: no significant change; mean 182.502, max 196.149 rps (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
  - `cfg-0006`: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
  - `met-bcb9c30a`: portfolio-service db_pool_in_use: 11.818 -> 4.062 count from 2026-03-11T14:01:00Z (max 13.663)

## Facts (verified observations)

- At 2026-03-11T14:01:00Z, ops-automation applied runtime configuration change cfg-0006, modifying portfolio-service db.pool.max_size from 40 to 4. (`cfg-0006`)
- portfolio-service db_pool_in_use dropped from a baseline mean of ~11.8 to ~4.0 at 14:01:00Z. (`met-eb2b7c3c`, `met-bcb9c30a`)
- portfolio-db active_connections fell from ~35.1 to ~5.0 at 14:01:00Z, and queries_per_sec fell from ~948.7 to ~413.4 at 14:02:00Z. (`met-3c8b0ae1`, `met-e3c57dc8`, `met-a33e03c6`)
- portfolio-service db_pool_wait_ms rose from ~3.0 ms to ~2386.6 ms starting at 14:02:00Z. (`met-d4a2d97e`, `met-9e8d8aa8`)
- portfolio-service latency_p50_ms surged from ~55 ms to ~1910 ms and latency_p99_ms surged from ~248 ms to ~8243 ms at 14:02:00Z. (`met-630f1043`, `met-94fba730`, `met-f0207b18`)
- At 14:02:01Z, portfolio-service began logging WARN messages: 'Timed out waiting for connection from pool after 2189ms (pool=portfolio-db, max=4)'. (`logpat-f6a782a6`, `log-e92aba56bb`)
- At 14:03:00Z, portfolio-service began logging ERROR messages: 'Request GET /api/v1/portfolio failed: timeout acquiring database connection', with error_rate_pct rising from 0.28% to 11.35%. (`logpat-4a133374`, `log-722011b09b`, `met-51108f6f`, `met-98989961`)
- web-app error_rate_pct increased from ~0.2% to ~3.9% starting at 14:03:00Z. (`met-0d2cc40b`, `met-1aa78d26`)
- notification-service deployed version 2.3.1 between 13:59:00Z and 14:03:00Z, but notification-service metrics remained normal and portfolio-service does not depend on notification-service. (`dep-0009`, `topo-portfolio-service`, `met-23594927`, `met-07eef516`)
- portfolio-service request_rate_rps (~183 rps) and db_queries_per_request (~3.0) remained steady across the entire incident window. (`met-fb817f95`, `met-492a3857`)

## Assumptions

- The runtime configuration mechanism dynamically reloads db.pool.max_size across running portfolio-service instances without requiring pod restarts.

## Data gaps

- none reported

## Timeline

- 2026-03-11T13:59:00Z: Deployment dep-0009 of notification-service version 2.3.1 started by alice. (`dep-0009`)
- 2026-03-11T14:01:00Z: ops-automation applied runtime configuration change cfg-0006 to portfolio-service, reducing db.pool.max_size from 40 to 4. (`cfg-0006`)
- 2026-03-11T14:01:00Z: portfolio-service db_pool_in_use dropped from ~11.8 to ~4.0, and portfolio-db active_connections dropped from ~35 to ~5. (`met-eb2b7c3c`, `met-3c8b0ae1`)
- 2026-03-11T14:02:00Z: portfolio-service db_pool_wait_ms surged to ~2386 ms, and latency_p99_ms increased from ~248 ms to ~8243 ms. (`met-d4a2d97e`, `met-94fba730`)
- 2026-03-11T14:02:01Z: portfolio-service started logging WARN errors indicating connection pool wait timeouts with pool=portfolio-db, max=4. (`logpat-f6a782a6`, `log-e92aba56bb`)
- 2026-03-11T14:03:00Z: Deployment dep-0009 finished successfully; notification-service metrics remained normal. (`dep-0009`, `met-23594927`)
- 2026-03-11T14:03:00Z: portfolio-service began throwing ERROR logs for GET /api/v1/portfolio due to database connection acquisition timeouts; error rate rose to ~11.3% and web-app error rate rose to ~3.9%. (`logpat-4a133374`, `log-722011b09b`, `met-51108f6f`, `met-0d2cc40b`)
- 2026-03-11T14:15:00Z: Incident INC-02 reported by support escalation regarding portfolio page timeouts and elevated error rates. (no evidence)

## Recommended actions

| Priority | Kind | Action | Rationale | Evidence |
|---|---|---|---|---|
| P0 | mitigate | Revert runtime configuration change cfg-0006 on portfolio-service, restoring db.pool.max_size to 40. | Restoring db.pool.max_size to its pre-incident value (40) will immediately eliminate connection pool acquisition queuing and resolve the 8-second page timeouts and 5xx errors. | cfg-0006, met-bcb9c30a, met-9e8d8aa8, logpat-f6a782a6 |
| P1 | fix | Update ops-automation capacity review scripts with safety guardrails that prevent reducing connection pools below historical peak and average usage metrics. | Automated pool right-sizing downsized the pool to 4 despite historical baseline utilization being ~12 connections, revealing an absence of sanity bounds in automation scripts. | cfg-0006, met-eb2b7c3c |
| P2 | prevent | Configure alerting on portfolio-service db_pool_wait_ms (>100 ms) and require canary deployment or peer review for runtime config changes. | High connection pool wait times are an early leading indicator of pool exhaustion; alerting will catch pool starvation before it impacts customer requests. | met-d4a2d97e, met-94fba730 |

## Investigation statistics

- LLM calls: 14; tool calls: 31 (0 failed, 32 attempts)
- Tokens: 70702 input, 17372 output, 234309 cache read, 0 cache write
- Wall-clock: 141.0s

## Evidence appendix

- `cfg-0006` [config_change, portfolio-service, 2026-03-11T14:01:00Z, via get_config_changes]: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
- `dep-0009` [deployment, notification-service, 2026-03-11T13:59:00Z, via get_deployments]: notification-service 2.3.0 -> 2.3.1 deployed 2026-03-11T13:59:00Z by alice (succeeded): Tweak trade confirmation email templates
- `log-722011b09b` [log_sample, portfolio-service, 2026-03-11T14:03:00Z, via query_logs]: 2026-03-11T14:03:00Z ERROR portfolio-service: Request GET /api/v1/portfolio failed: timeout acquiring database connection
- `log-e92aba56bb` [log_sample, portfolio-service, 2026-03-11T14:02:01Z, via query_logs]: 2026-03-11T14:02:01Z WARN portfolio-service: Timed out waiting for connection from pool after 2189ms (pool=portfolio-db, max=4)
- `logpat-4a133374` [log_pattern, portfolio-service, 2026-03-11T14:03:00Z, via query_logs]: 264x ERROR "Request GET /api/v<N>/portfolio failed: timeout acquiring database connection" in portfolio-service (2026-03-11T14:03:00Z to 2026-03-11T14:24:59Z)
- `logpat-f6a782a6` [log_pattern, portfolio-service, 2026-03-11T14:02:01Z, via query_logs]: 690x WARN "Timed out waiting for connection from pool after <N>ms (pool=portfolio-db, max=<N>)" in portfolio-service (2026-03-11T14:02:01Z to 2026-03-11T14:24:59Z)
- `met-07eef516` [metric_series, notification-service, 2026-03-11T13:50:00Z, via query_metrics]: notification-service latency_p99_ms: no significant change; mean 201.061, max 230.171 ms (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
- `met-0d2cc40b` [metric_series, web-app, 2026-03-11T14:03:00Z, via detect_anomalies]: web-app error_rate_pct: 0.197 -> 3.886 % from 2026-03-11T14:03:00Z (max 5.092)
- `met-1aa78d26` [metric_series, web-app, 2026-03-11T13:58:00Z, via query_metrics]: web-app error_rate_pct: 0.229 -> 3.201 % from 2026-03-11T13:58:00Z (max 5.092)
- `met-23594927` [metric_series, notification-service, 2026-03-11T13:50:00Z, via query_metrics]: notification-service error_rate_pct: no significant change; mean 0.102, max 0.138 % (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
- `met-3c8b0ae1` [metric_series, portfolio-db, 2026-03-11T14:01:00Z, via detect_anomalies]: portfolio-db active_connections: 35.073 -> 5.022 count from 2026-03-11T14:01:00Z (max 40.272)
- `met-492a3857` [metric_series, portfolio-service, 2026-03-11T13:50:00Z, via query_metrics]: portfolio-service db_queries_per_request: no significant change; mean 3.004, max 3.264 count (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
- `met-51108f6f` [metric_series, portfolio-service, 2026-03-11T14:03:00Z, via detect_anomalies]: portfolio-service error_rate_pct: 0.298 -> 11.333 % from 2026-03-11T14:03:00Z (max 14.219)
- `met-630f1043` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-service latency_p50_ms: 54.955 -> 1910.756 ms from 2026-03-11T14:02:00Z (max 2042.3)
- `met-94fba730` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-service latency_p99_ms: 256.836 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
- `met-98989961` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via query_metrics]: portfolio-service error_rate_pct: 0.279 -> 10.85 % from 2026-03-11T14:02:00Z (max 14.219)
- `met-9e8d8aa8` [metric_series, portfolio-service, 2026-03-11T13:55:00Z, via query_metrics]: portfolio-service db_pool_wait_ms: 3.855 -> 1830.293 ms from 2026-03-11T13:55:00Z (max 3527.988)
- `met-a33e03c6` [metric_series, portfolio-db, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-db queries_per_sec: 948.727 -> 413.37 qps from 2026-03-11T14:02:00Z (max 1280.936)
- `met-bcb9c30a` [metric_series, portfolio-service, 2026-03-11T14:01:00Z, via query_metrics]: portfolio-service db_pool_in_use: 11.818 -> 4.062 count from 2026-03-11T14:01:00Z (max 13.663)
- `met-d4a2d97e` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-service db_pool_wait_ms: 3.035 -> 2386.557 ms from 2026-03-11T14:02:00Z (max 3527.988)
- `met-e3c57dc8` [metric_series, portfolio-db, 2026-03-11T14:01:00Z, via query_metrics]: portfolio-db active_connections: 35.341 -> 5.022 count from 2026-03-11T14:01:00Z (max 38.673)
- `met-eb2b7c3c` [metric_series, portfolio-service, 2026-03-11T14:01:00Z, via detect_anomalies]: portfolio-service db_pool_in_use: 11.872 -> 4.062 count from 2026-03-11T14:01:00Z (max 15.123)
- `met-f0207b18` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via query_metrics]: portfolio-service latency_p99_ms: 248.111 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
- `met-fb817f95` [metric_series, portfolio-service, 2026-03-11T13:50:00Z, via query_metrics]: portfolio-service request_rate_rps: no significant change; mean 182.502, max 196.149 rps (2026-03-11T13:50:00Z to 2026-03-11T14:25:00Z)
- `topo-portfolio-service` [dependency, portfolio-service, -, via get_service_dependencies]: portfolio-service depends on [app-cache, cluster-dns, ledger-service, orders-db, portfolio-db, quotes-service]; depended on by [api-gateway]
