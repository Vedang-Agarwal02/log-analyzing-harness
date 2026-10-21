# Incident investigation: INC-02 - Portfolio page timing out

**Status:** Root cause identified (top confidence 0.88 >= 0.75)
**System:** multi_agent | **Termination:** critic_accepted

## Executive summary

Portfolio page load failures and elevated latencies on web-app are caused by database connection pool starvation in portfolio-service. At 14:01:00 UTC, automated runtime configuration change cfg-0006 reduced portfolio-service's db.pool.max_size from 40 to 4. Following the configuration reload at 14:01:29 UTC, active database connections dropped to 4, driving database pool wait times above 2,300 ms, spiking portfolio-service p99 latency above 8.2 seconds, and causing ~11% of portfolio requests to fail with connection acquisition timeouts. We have high confidence (0.88) in this diagnosis; the earlier notification-service deployment at 13:59 UTC was ruled out due to topological isolation and normal telemetry. The immediate mitigation is to revert db.pool.max_size on portfolio-service back to 40 via runtime configuration.

## Ranked hypotheses

### 1. Runtime config change reduced portfolio-service connection pool size causing connection starvation and request timeouts (confidence 0.88, high)

- **Root-cause service:** portfolio-service
- **Category:** config_change
- **Trigger:** cfg-0006
- **Mechanism:** At 14:01:00Z, runtime configuration change cfg-0006 updated portfolio-service db.pool.max_size from 40 to 4. At 14:01:29Z, portfolio-service dynamically reloaded this configuration (logpat-4de848f8), immediately shrinking active database connections from ~11.9 to 4.0 (met-13a4f01d, met-3c8b0ae1). Normal concurrent portfolio request traffic required more than 4 connections, causing connection pool wait times to surge from 3 ms to over 2,300 ms starting at 14:02:00Z (met-d4a2d97e). This led to pool acquisition timeout warnings (logpat-f6a782a6) and drove p99 latency to >8,200 ms (met-820e229b). By 14:03:00Z, connection acquisition timeouts began failing GET /api/v1/portfolio requests (logpat-4a133374, met-51108f6f), propagating through api-gateway to cause elevated error rates and spinning timeouts on web-app (met-0d2cc40b).
- **Supporting evidence:**
  - `cfg-0006`: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
  - `logpat-4de848f8`: 1x INFO "Configuration reloaded: db.pool.max_size=<N>" in portfolio-service (2026-03-11T14:01:29Z to 2026-03-11T14:01:29Z)
  - `log-aa5252e484`: 2026-03-11T14:01:29Z INFO portfolio-service: Configuration reloaded: db.pool.max_size=4
  - `met-13a4f01d`: portfolio-service db_pool_in_use: 11.603 -> 4.062 count from 2026-03-11T14:01:00Z (max 13.663)
  - `met-eb2b7c3c`: portfolio-service db_pool_in_use: 11.872 -> 4.062 count from 2026-03-11T14:01:00Z (max 15.123)
  - `met-3c8b0ae1`: portfolio-db active_connections: 35.073 -> 5.022 count from 2026-03-11T14:01:00Z (max 40.272)
  - `met-d4a2d97e`: portfolio-service db_pool_wait_ms: 3.035 -> 2386.557 ms from 2026-03-11T14:02:00Z (max 3527.988)
  - `met-f06a4d08`: portfolio-service db_pool_wait_ms: 3.241 -> 1484.748 ms from 2026-03-11T13:48:00Z (max 3527.988)
  - `logpat-f6a782a6`: 690x WARN "Timed out waiting for connection from pool after <N>ms (pool=portfolio-db, max=<N>)" in portfolio-service (2026-03-11T14:02:01Z to 2026-03-11T14:24:59Z)
  - `log-e92aba56bb`: 2026-03-11T14:02:01Z WARN portfolio-service: Timed out waiting for connection from pool after 2189ms (pool=portfolio-db, max=4)
  - `met-5ee951db`: portfolio-service latency_p50_ms: 53.549 -> 1910.756 ms from 2026-03-11T14:02:00Z (max 2042.3)
  - `met-820e229b`: portfolio-service latency_p99_ms: 247.718 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
  - `met-630f1043`: portfolio-service latency_p50_ms: 54.955 -> 1910.756 ms from 2026-03-11T14:02:00Z (max 2042.3)
  - `met-94fba730`: portfolio-service latency_p99_ms: 256.836 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
  - `logpat-4a133374`: 264x ERROR "Request GET /api/v<N>/portfolio failed: timeout acquiring database connection" in portfolio-service (2026-03-11T14:03:00Z to 2026-03-11T14:24:59Z)
  - `log-722011b09b`: 2026-03-11T14:03:00Z ERROR portfolio-service: Request GET /api/v1/portfolio failed: timeout acquiring database connection
  - `met-51108f6f`: portfolio-service error_rate_pct: 0.298 -> 11.333 % from 2026-03-11T14:03:00Z (max 14.219)
  - `met-0d2cc40b`: web-app error_rate_pct: 0.197 -> 3.886 % from 2026-03-11T14:03:00Z (max 5.092)
  - `topo-web-app`: web-app depends on [api-gateway, cluster-dns]; depended on by [nothing]
  - `topo-api-gateway`: api-gateway depends on [auth-service, cluster-dns, orders-service, payments-service, portfolio-service, quotes-service]; depended on by [web-app]
  - `topo-portfolio-service`: portfolio-service depends on [app-cache, cluster-dns, ledger-service, orders-db, portfolio-db, quotes-service]; depended on by [api-gateway]
- **Contradicting evidence:**
  - none
- **Assumptions:**
  - The baseline concurrency of portfolio requests regularly demands 10-15 simultaneous database connections per service instance.
  - The runtime configuration reload took effect immediately across all active portfolio-service instances.

### 2. Database lock contention or query slowdown on portfolio-db causing connection queueing in portfolio-service (confidence 0.08, low)

- **Root-cause service:** portfolio-db
- **Category:** resource_exhaustion
- **Trigger:** none identified
- **Mechanism:** Slow queries or lock contention inside portfolio-db tied up database connections, causing portfolio-service client connection pool slots to remain occupied indefinitely, eventually starving incoming GET /api/v1/portfolio requests and resulting in pool wait timeouts.
- **Supporting evidence:**
  - `met-d4a2d97e`: portfolio-service db_pool_wait_ms: 3.035 -> 2386.557 ms from 2026-03-11T14:02:00Z (max 3527.988)
  - `met-820e229b`: portfolio-service latency_p99_ms: 247.718 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
  - `logpat-f6a782a6`: 690x WARN "Timed out waiting for connection from pool after <N>ms (pool=portfolio-db, max=<N>)" in portfolio-service (2026-03-11T14:02:01Z to 2026-03-11T14:24:59Z)
- **Contradicting evidence:**
  - `met-3c8b0ae1`: portfolio-db active_connections: 35.073 -> 5.022 count from 2026-03-11T14:01:00Z (max 40.272)
  - `met-7d703026`: portfolio-db cpu_pct: no significant change; mean 39.411, max 42.505 % (2026-03-11T13:45:00Z to 2026-03-11T14:25:00Z)
  - `met-ede6e038`: portfolio-db lock_wait_ms: no significant change; mean 1.04, max 1.499 ms (2026-03-11T13:45:00Z to 2026-03-11T14:25:00Z)
  - `met-a33e03c6`: portfolio-db queries_per_sec: 948.727 -> 413.37 qps from 2026-03-11T14:02:00Z (max 1280.936)
  - `cfg-0006`: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
  - `logpat-4de848f8`: 1x INFO "Configuration reloaded: db.pool.max_size=<N>" in portfolio-service (2026-03-11T14:01:29Z to 2026-03-11T14:01:29Z)
- **Assumptions:**
  - Queries executing on portfolio-db suffered severe latency degradation without causing noticeable CPU utilization or lock wait increases.

### 3. Notification-service deployment 2.3.1 caused cascading degradation to upstream request pipelines (confidence 0.04, low)

- **Root-cause service:** notification-service
- **Category:** bad_deployment
- **Trigger:** dep-0009
- **Mechanism:** The deployment of notification-service version 2.3.1 at 13:59:00Z created unhandled background load, event-bus backpressure, or shared infrastructure resource exhaustion that cascaded into portfolio request timeouts.
- **Supporting evidence:**
  - `dep-0009`: notification-service 2.3.0 -> 2.3.1 deployed 2026-03-11T13:59:00Z by alice (succeeded): Tweak trade confirmation email templates
- **Contradicting evidence:**
  - `met-6962b1de`: notification-service cpu_pct: no significant change; mean 19.347, max 22.44 % (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
  - `met-65f3b4fe`: notification-service error_rate_pct: no significant change; mean 0.105, max 0.138 % (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
  - `met-9dc7cc89`: notification-service latency_p99_ms: no significant change; mean 198.852, max 230.171 ms (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
  - `met-2b2e91bf`: event-bus produce_error_rate_pct: no significant change; mean 0.05, max 0.078 % (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
  - `met-ed2fdbb2`: event-bus under_replicated_partitions: no significant change; mean 0.0, max 0.0 count (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
  - `logpat-5926117c`: 7x INFO "Sent trade confirmation email <ID>" in notification-service (2026-03-11T13:53:42Z to 2026-03-11T14:08:29Z)
  - `topo-notification-service`: notification-service depends on [cluster-dns, email-provider, event-bus]; depended on by [nothing]
  - `topo-portfolio-service`: portfolio-service depends on [app-cache, cluster-dns, ledger-service, orders-db, portfolio-db, quotes-service]; depended on by [api-gateway]
- **Assumptions:**
  - Notification-service interacts with or impacts the portfolio request path indirectly through unmodeled shared infrastructure.

## Facts (verified observations)

- At 2026-03-11T14:01:00Z, ops-automation updated db.pool.max_size on portfolio-service from 40 to 4 via runtime configuration. (`cfg-0006`)
- Portfolio-service reloaded configuration setting db.pool.max_size=4 at 2026-03-11T14:01:29Z. (`logpat-4de848f8`, `log-aa5252e484`)
- Portfolio-service database connections in use dropped from a mean of ~11.9 to 4.0 at 2026-03-11T14:01:00Z. (`met-13a4f01d`, `met-eb2b7c3c`)
- Portfolio-db active client connections dropped from ~35 to ~5 at 2026-03-11T14:01:00Z. (`met-3c8b0ae1`)
- Portfolio-service database pool wait time surged from 3.0 ms to an average of 2386.6 ms starting at 2026-03-11T14:02:00Z. (`met-d4a2d97e`, `met-f06a4d08`)
- Portfolio-service latency spiked at 2026-03-11T14:02:00Z, with p50 increasing from ~54 ms to ~1910 ms and p99 increasing from ~248 ms to ~8244 ms. (`met-5ee951db`, `met-820e229b`, `met-630f1043`, `met-94fba730`)
- Portfolio-service logged 690 warnings starting at 2026-03-11T14:02:01Z reporting timeouts waiting for connections from pool portfolio-db with max=4. (`logpat-f6a782a6`, `log-e92aba56bb`)
- Portfolio-service error rate rose from 0.30% to 11.33% starting at 2026-03-11T14:03:00Z, with 264 errors stating request GET /api/v1/portfolio failed due to database connection acquisition timeout. (`met-51108f6f`, `logpat-4a133374`, `log-722011b09b`)
- Web-app error rate rose from 0.20% to ~3.89% starting at 2026-03-11T14:03:00Z. (`met-0d2cc40b`)
- Portfolio-db CPU utilization remained stable at ~39% and database lock wait time remained flat at ~1 ms throughout the incident. (`met-7d703026`, `met-ede6e038`)
- Notification-service showed no metric anomalies, maintaining ~19% CPU utilization, ~0.1% error rate, and normal p99 latency following deployment dep-0009. (`met-6962b1de`, `met-65f3b4fe`, `met-9dc7cc89`, `logpat-5926117c`)
- Web-app calls api-gateway which routes to portfolio-service, whereas notification-service has no downstream dependents and does not interact with portfolio-service. (`topo-web-app`, `topo-api-gateway`, `topo-portfolio-service`, `topo-notification-service`)

## Assumptions

- Normal portfolio request concurrency requires approximately 10 to 15 concurrent database connections per portfolio-service instance.
- The runtime configuration reload applied immediately and uniformly across all active portfolio-service instances.

## Data gaps

- Detailed endpoint-level latency distributions and downstream dependency traces for web-app could not be retrieved due to telemetry query constraints.

## Timeline

- 2026-03-11T13:59:00Z: Alice deployed notification-service version 2.3.1; service remained healthy and was ruled out as the trigger. (`dep-0009`, `met-6962b1de`)
- 2026-03-11T14:01:00Z: Ops-automation updated db.pool.max_size on portfolio-service from 40 to 4 via runtime_config. Active database connections dropped from ~11.9 to 4.0. (`cfg-0006`, `met-13a4f01d`, `met-3c8b0ae1`)
- 2026-03-11T14:01:29Z: Portfolio-service dynamically reloaded configuration: db.pool.max_size=4. (`logpat-4de848f8`, `log-aa5252e484`)
- 2026-03-11T14:02:00Z: Portfolio-service db_pool_wait_ms surged from 3 ms to ~2,386 ms; p50 latency increased to ~1,910 ms and p99 latency increased to ~8,244 ms. (`met-d4a2d97e`, `met-5ee951db`, `met-820e229b`)
- 2026-03-11T14:02:01Z: Portfolio-service began logging warnings for database pool wait timeouts (pool=portfolio-db, max=4). (`logpat-f6a782a6`, `log-e92aba56bb`)
- 2026-03-11T14:03:00Z: Portfolio-service error rate spiked to 11.3% with database connection acquisition failures on GET /api/v1/portfolio; web-app error rate rose to ~3.9% with client timeouts. (`met-51108f6f`, `logpat-4a133374`, `log-722011b09b`, `met-0d2cc40b`)

## Recommended actions

| Priority | Kind | Action | Rationale | Evidence |
|---|---|---|---|---|
| P0 | mitigate | Revert db.pool.max_size on portfolio-service back to 40 via runtime_config. | Restoring connection pool capacity to 40 will immediately eliminate connection acquisition bottlenecks and resolve request timeouts for portfolio page traffic. | cfg-0006, met-d4a2d97e, logpat-4a133374 |
| P1 | fix | Add bounds validation to configuration management tools to prevent lowering database connection pool sizes below known concurrent traffic requirements without load verification. | Ensures automated or manual capacity management tasks cannot deploy crippling configuration reductions directly to production runtime configurations. | cfg-0006, met-eb2b7c3c |
| P2 | prevent | Implement alert rules for portfolio-service database connection wait duration (db_pool_wait_ms > 200ms) and connection acquisition timeout log occurrences. | Enables early detection and alerting before connection pool saturation cascades into user-facing HTTP request timeouts. | met-d4a2d97e, logpat-f6a782a6 |

## Investigation statistics

- LLM calls: 37; tool calls: 63 (0 failed, 64 attempts)
- Tokens: 185083 input, 40252 output, 174443 cache read, 0 cache write
- Wall-clock: 269.0s

## Evidence appendix

- `cfg-0006` [config_change, portfolio-service, 2026-03-11T14:01:00Z, via get_config_changes]: portfolio-service db.pool.max_size: 40 -> 4 at 2026-03-11T14:01:00Z by ops-automation (runtime_config): Right-size database connection pools per the Q1 capacity review
- `dep-0009` [deployment, notification-service, 2026-03-11T13:59:00Z, via get_deployments]: notification-service 2.3.0 -> 2.3.1 deployed 2026-03-11T13:59:00Z by alice (succeeded): Tweak trade confirmation email templates
- `log-722011b09b` [log_sample, portfolio-service, 2026-03-11T14:03:00Z, via query_logs]: 2026-03-11T14:03:00Z ERROR portfolio-service: Request GET /api/v1/portfolio failed: timeout acquiring database connection
- `log-aa5252e484` [log_sample, portfolio-service, 2026-03-11T14:01:29Z, via query_logs]: 2026-03-11T14:01:29Z INFO portfolio-service: Configuration reloaded: db.pool.max_size=4
- `log-e92aba56bb` [log_sample, portfolio-service, 2026-03-11T14:02:01Z, via query_logs]: 2026-03-11T14:02:01Z WARN portfolio-service: Timed out waiting for connection from pool after 2189ms (pool=portfolio-db, max=4)
- `logpat-4a133374` [log_pattern, portfolio-service, 2026-03-11T14:03:00Z, via query_logs]: 264x ERROR "Request GET /api/v<N>/portfolio failed: timeout acquiring database connection" in portfolio-service (2026-03-11T14:03:00Z to 2026-03-11T14:24:59Z)
- `logpat-4de848f8` [log_pattern, portfolio-service, 2026-03-11T14:01:29Z, via query_logs]: 1x INFO "Configuration reloaded: db.pool.max_size=<N>" in portfolio-service (2026-03-11T14:01:29Z to 2026-03-11T14:01:29Z)
- `logpat-5926117c` [log_pattern, notification-service, 2026-03-11T13:53:42Z, via query_logs]: 7x INFO "Sent trade confirmation email <ID>" in notification-service (2026-03-11T13:53:42Z to 2026-03-11T14:08:29Z)
- `logpat-f6a782a6` [log_pattern, portfolio-service, 2026-03-11T14:02:01Z, via query_logs]: 690x WARN "Timed out waiting for connection from pool after <N>ms (pool=portfolio-db, max=<N>)" in portfolio-service (2026-03-11T14:02:01Z to 2026-03-11T14:24:59Z)
- `met-0d2cc40b` [metric_series, web-app, 2026-03-11T14:03:00Z, via detect_anomalies]: web-app error_rate_pct: 0.197 -> 3.886 % from 2026-03-11T14:03:00Z (max 5.092)
- `met-13a4f01d` [metric_series, portfolio-service, 2026-03-11T14:01:00Z, via query_metrics]: portfolio-service db_pool_in_use: 11.603 -> 4.062 count from 2026-03-11T14:01:00Z (max 13.663)
- `met-2b2e91bf` [metric_series, event-bus, 2026-03-11T13:30:00Z, via query_metrics]: event-bus produce_error_rate_pct: no significant change; mean 0.05, max 0.078 % (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
- `met-3c8b0ae1` [metric_series, portfolio-db, 2026-03-11T14:01:00Z, via detect_anomalies]: portfolio-db active_connections: 35.073 -> 5.022 count from 2026-03-11T14:01:00Z (max 40.272)
- `met-51108f6f` [metric_series, portfolio-service, 2026-03-11T14:03:00Z, via detect_anomalies]: portfolio-service error_rate_pct: 0.298 -> 11.333 % from 2026-03-11T14:03:00Z (max 14.219)
- `met-5ee951db` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via query_metrics]: portfolio-service latency_p50_ms: 53.549 -> 1910.756 ms from 2026-03-11T14:02:00Z (max 2042.3)
- `met-630f1043` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-service latency_p50_ms: 54.955 -> 1910.756 ms from 2026-03-11T14:02:00Z (max 2042.3)
- `met-65f3b4fe` [metric_series, notification-service, 2026-03-11T13:30:00Z, via query_metrics]: notification-service error_rate_pct: no significant change; mean 0.105, max 0.138 % (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
- `met-6962b1de` [metric_series, notification-service, 2026-03-11T13:30:00Z, via query_metrics]: notification-service cpu_pct: no significant change; mean 19.347, max 22.44 % (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
- `met-7d703026` [metric_series, portfolio-db, 2026-03-11T13:45:00Z, via query_metrics]: portfolio-db cpu_pct: no significant change; mean 39.411, max 42.505 % (2026-03-11T13:45:00Z to 2026-03-11T14:25:00Z)
- `met-820e229b` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via query_metrics]: portfolio-service latency_p99_ms: 247.718 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
- `met-94fba730` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-service latency_p99_ms: 256.836 -> 8243.667 ms from 2026-03-11T14:02:00Z (max 9355.486)
- `met-9dc7cc89` [metric_series, notification-service, 2026-03-11T13:30:00Z, via query_metrics]: notification-service latency_p99_ms: no significant change; mean 198.852, max 230.171 ms (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
- `met-a33e03c6` [metric_series, portfolio-db, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-db queries_per_sec: 948.727 -> 413.37 qps from 2026-03-11T14:02:00Z (max 1280.936)
- `met-d4a2d97e` [metric_series, portfolio-service, 2026-03-11T14:02:00Z, via detect_anomalies]: portfolio-service db_pool_wait_ms: 3.035 -> 2386.557 ms from 2026-03-11T14:02:00Z (max 3527.988)
- `met-eb2b7c3c` [metric_series, portfolio-service, 2026-03-11T14:01:00Z, via detect_anomalies]: portfolio-service db_pool_in_use: 11.872 -> 4.062 count from 2026-03-11T14:01:00Z (max 15.123)
- `met-ed2fdbb2` [metric_series, event-bus, 2026-03-11T13:30:00Z, via query_metrics]: event-bus under_replicated_partitions: no significant change; mean 0.0, max 0.0 count (2026-03-11T13:30:00Z to 2026-03-11T14:25:00Z)
- `met-ede6e038` [metric_series, portfolio-db, 2026-03-11T13:45:00Z, via query_metrics]: portfolio-db lock_wait_ms: no significant change; mean 1.04, max 1.499 ms (2026-03-11T13:45:00Z to 2026-03-11T14:25:00Z)
- `met-f06a4d08` [metric_series, portfolio-service, 2026-03-11T13:48:00Z, via query_metrics]: portfolio-service db_pool_wait_ms: 3.241 -> 1484.748 ms from 2026-03-11T13:48:00Z (max 3527.988)
- `topo-api-gateway` [dependency, api-gateway, -, via get_service_dependencies]: api-gateway depends on [auth-service, cluster-dns, orders-service, payments-service, portfolio-service, quotes-service]; depended on by [web-app]
- `topo-notification-service` [dependency, notification-service, -, via get_service_dependencies]: notification-service depends on [cluster-dns, email-provider, event-bus]; depended on by [nothing]
- `topo-portfolio-service` [dependency, portfolio-service, -, via get_service_dependencies]: portfolio-service depends on [app-cache, cluster-dns, ledger-service, orders-db, portfolio-db, quotes-service]; depended on by [api-gateway]
- `topo-web-app` [dependency, web-app, -, via get_service_dependencies]: web-app depends on [api-gateway, cluster-dns]; depended on by [nothing]
