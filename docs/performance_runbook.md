# Login and navigation performance runbook

## Scope

`runtime_metrics.py` is enabled in Gunicorn via `post_worker_init`; it does not modify the standalone calculator, database schema, caching semantics, or financial calculations. It emits a single `PERF` line only for requests slower than 300 ms (or HTTP 5xx). A single worker with four threads is intentionally retained because the existing caches live in process memory. HTTP keep-alive is set to five seconds. **No measured speedup is claimed.**

Measurements are generated inside Flask after routing, not during Render cold boot, browser asset downloading, chart painting, or network round trips. `total_ms` is the server request time. `acquire_ms` measures getting a DB connection (including health check); `sql_ms` covers query executions; `fetch_ms` covers `fetchone`, `fetchall` and `fetchmany`; `render_ms` covers calls to the imported `render_template` function. A large `total_ms` with low measured DB and render time indicates application analysis, waiting, or other uninstrumented work. Logs contain only endpoint names, HTTP status, counts, and aggregate durations—not paths, URLs, customer identifiers, SQL, parameters, passwords or profit data. Streaming response-body time is not captured.

### Temporary runtime flags

- `PERF_LOG_SLOW_MS=300`: default log threshold; lower to `100` for a short sampling window.
- `PERF_LOG_ALL=1`: record all requests temporarily, then unset to limit log volume.
- `PERF_SERVER_TIMING=1`: expose timing breakdown through a `Server-Timing` response header temporarily; keep disabled unless needed.

These flags are optional. Never set credentials in source, PR descriptions, logs, or shell command arguments. Keep `SECRET_KEY` set to a long random environment secret; rotate any previously disclosed admin password through the normal account process. In the current codebase, an unset `SECRET_KEY` falls back to a committed constant—correct this before exposing the service more broadly, after verifying the session migration plan.

## Measure actual HTTP timings

Create a dedicated least-privilege test account. Supply its credentials as `PERF_TEST_USER` and `PERF_TEST_PASSWORD` environment variables privately, then run:

```bash
python latency_probe.py --url https://pharmacy-profit.onrender.com --runs 20
```

The standard-library script follows one read-only session per cycle, measuring login-form GET, login POST without following its redirect, then dashboard, calendar, input, report, trend and calculator GET separately. It accepts only HTTPS remote origins, refuses query/userinfo in the target URL, and prints only aggregate nearest-rank p50/p95/max timing in milliseconds. The login POST is the only write-like request and creates a session; no financial records are changed. It requires a valid test login and stops if any authenticated screen redirects unexpectedly. Browser visual completion must be measured separately using DevTools/Playwright. Compare baseline and candidate in the same region and account with the same data, both warm and immediately after a known restart. An initial HTTP request **is not proof of a true cold start**.

Expected user flow: measure login GET, login POST, first dashboard GET, dashboard revisit, calendar month navigation, and input↔report switches individually. Track 5xx errors and stale/missing numbers alongside latency. Do not use `/ping` as a proxy for the login/dashboard speed; it does not touch PostgreSQL.

## Decision tree after measurement

1. If only the first request after deployment is slow, inspect Render startup logs and the import-time `init_db()` path before modifying caching. Schema creation/migration during web import may dominate cold boot; moving it needs a verified deployment migration step and rollback strategy.
2. If `acquire_ms` dominates, inspect PostgreSQL pool reuse/stale connection failures and database region/compute. Pool limit is 10 per process, configured Gunicorn concurrency is four request threads. The current `database.get_db()` can create a direct connection on pool exhaustion, so bound this fallback only after observing whether it happens and validating DB connection budgets.
3. If `sql_ms`/`fetch_ms` dominates, run `EXPLAIN (ANALYZE, BUFFERS)` in a private staging database on `load_analysis_rows` and monthly queries, inspect returned rows, then change projections/indexes based on those results. Current dashboard requests re-read an analysis window and no longer use the old full-context dashboard cache.
4. If unaccounted `total_ms` dominates, profile forecasting, narrative building, and HTML/JSON generation. Show essential summary first and load detailed analytics separately only with a consistent snapshot strategy; never cache full cross-user rendered responses or reintroduce stale profit amounts.
5. If server time is low but the page feels slow, inspect browser waterfall, chart initialization, CDN waterfall, main-thread blocking and mobile network. Keep separate calculator arithmetic unchanged.

## Checks before merging/deploying

Run `python -m pytest` in the real repository environment; verify Gunicorn startup with the actual `gunicorn app:app --workers 1 --threads 4 --worker-class gthread` command. Run an authenticated smoke test for all pages, a save→read freshness test, and concurrent readers/writer tests on staging PostgreSQL. Verify protected `/debug/perf`, login/logout, user isolation, Excel upload rollback and existing profit regression tests. Compare p50/p95 and error rate before/after; only deploy to production when behavior and data consistency match. Instrumentation unit tests alone do not establish PostgreSQL correctness or a speed gain.
