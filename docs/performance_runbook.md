# Login and navigation performance runbook

## Scope and verified behavior

`runtime_metrics.py` is enabled in Gunicorn via `post_worker_init`. The same hook installs `pool_guard.py` before traffic. The guard translates **only PostgreSQL pool exhaustion** to HTTP 503 (`Retry-After: 1`, `Cache-Control: no-store`), instead of allowing the existing `get_db()` PoolError handler to open potentially unbounded direct connections. Healthy pooled connections, database schema, cache semantics, standalone calculator, and financial calculations are unchanged. The single worker with four threads is retained because current caches live in process memory; HTTP keep-alive is five seconds. Pool creation failure and stale-connection retry exhaustion can still enter the original direct-connection path; this guard is not a universal connection limit.

The observable tests are NOT a measured production speedup. GitHub Actions runs SQLite regressions and an isolated PostgreSQL 16 service: 52 tests passed on the pool-guard change, covering reuse, rollback, four concurrent connections, a saturated pool returning 503 without opening a direct connection, and normal checkout/release. It also boots Gunicorn from a temporary code/template copy **without tracked financial data** and checks `/health`. Neither test connects to Render or production PostgreSQL, tests actual logged-in browser navigation, or measures p50/p95 under real load.

`runtime_metrics.py` emits one `PERF` line only for requests slower than 300 ms (or HTTP 5xx). Measurements are taken inside Flask, not during Render cold boot, browser asset downloading, chart painting, or network round trips. `total_ms` measures server request time; `acquire_ms` includes DB connection health check, `sql_ms` query execution, `fetch_ms` row fetching, and `render_ms` the imported template renderer. Large `total_ms` with small measured parts suggests application analysis/waiting or uninstrumented work. The log does not contain paths, URLs, account identifiers, query text, parameters, passwords or profit values. Streaming response-body time is not captured.

### Temporary runtime flags

- `PERF_LOG_SLOW_MS=300`: default log threshold; lower to `100` for a short sampling window.
- `PERF_LOG_ALL=1`: log all requests temporarily, then unset to limit volume.
- `PERF_SERVER_TIMING=1`: temporarily expose timing breakdown through a response header; keep disabled unless needed.

Never put credentials in source, PR descriptions, logs or shell arguments. Set a long random `SECRET_KEY` in the hosting environment and rotate the previously disclosed administrator password via a verified account process. The current code still has a static `SECRET_KEY` fallback; correcting it needs a planned session migration to avoid unexpected logouts.

## Production startup and data migration: hard deployment gate

`app.py` calls `init_db()` at module import. `database.init_postgres_db()` creates tables and indexes, then checks `SELECT COUNT(*) FROM daily_profit`. If the remote table is empty and `data/sales.db` is present, it automatically copies users, daily rows and monthly summaries from the tracked SQLite file. This could lengthen cold startup and populate an empty staging/production database with old or sensitive data. It also means deleting tracked DB files or moving initialization to a separate release phase without backing up and testing the migration can break a fresh deployment. An HTTP `/health` success is **not** proof that a production database has correct data or that cold boot is fast.

Before decoupling migrations from application import, the owner must verify the current Render deploy/start commands, authoritative live database and backup/restore, test the existing migration on disposable synthetic data, make migrations an explicit idempotent one-time deployment step, and verify the deployed service starts successfully without schema writes. Preserve a documented rollback to the previous commit and DB backup. Never exercise this migration against production or against the tracked financial seed data in CI. This PR deliberately leaves import-time initialization untouched.

## Measure actual authenticated HTTP timings

Create a dedicated least-privilege test account and supply `PERF_TEST_USER` and `PERF_TEST_PASSWORD` privately as environment variables. Run:

```bash
python latency_probe.py --url https://pharmacy-profit.onrender.com --runs 20
```

The standard-library tool measures login form GET, login POST without following redirects, then dashboard, calendar, input, report, trend and calculator GET using an authenticated session. It accepts only HTTPS remote origins, rejects query and userinfo in the base URL, prints only aggregate nearest-rank p50/p95/max in milliseconds, and does not write financial rows. A login POST creates a session; stop if a screen unexpectedly redirects. Measure visual completion separately using browser DevTools or Playwright. Compare baseline and candidate with the same account, data and region for both warm requests and a known fresh restart. One initially slow HTTP request is not proof of a true cold boot.

Measure login GET/POST, first dashboard GET, dashboard revisit, calendar month changes and input-to-report switches individually. Record 5xx and stale/missing figures alongside timings. `/ping` and `/health` do not touch PostgreSQL and are NOT proxies for login/dashboard performance. For 503s, record pool occupancy/worker counts and the database's connection cap privately; do not increase pool size without a server-wide connection budget.

## Decision tree after measurement

1. If only startup/first request is slow, inspect Render worker startup and the import-time initialization/migration above. Never remove it without the deployment and data-recovery gate.
2. If `acquire_ms` dominates, inspect reuse, idle checks, PostgreSQL network distance and DB compute. In Gunicorn the new guard returns 503 rather than creating unlimited *extra* connections **only when an existing pool is exhausted**. Creation failures and two failed stale-connection checkouts still use legacy direct fallback; investigate and bound those separately after integration tests. With four threads, a ten-connection per-process pool may be more than required, but changing this requires the total worker/process budget and load measurements.
3. If `sql_ms` or `fetch_ms` dominates, run `EXPLAIN (ANALYZE, BUFFERS)` only on staging/synthetic data for the batched dashboard and monthly queries. Check row counts, planner/index use and projection before changing queries; do not infer an index benefit without plans.
4. If `total_ms` dominates without corresponding DB/render timings, profile forecasting, narrative creation and JSON/HTML generation. Defer detailed analytics only with a consistent snapshot and freshness strategy, and protect user isolation. Do not reintroduce stale profit values for a superficially faster initial render.
5. If server response time is low but navigation feels slow, inspect mobile/browser network waterfall, chart initialization and main-thread blocking. Do not touch standalone calculator arithmetic.

## Before merging or deployment

Review the PR diff and run all tests including PostgreSQL 16 integration and the isolated Gunicorn boot smoke. Validate Gunicorn startup under the actual Render command and environment. On a private staging copy, test login/logout, all authenticated routes, save-then-read freshness, concurrent readers/writer, financial totals, Excel import rollback and tenant isolation. Capture p50/p95 and 5xx before/after, including 503s under capacity. Never describe the current CI results as production speed improvement. Only merge/deploy once live-data backup, security containment and measured behavior are verified.
