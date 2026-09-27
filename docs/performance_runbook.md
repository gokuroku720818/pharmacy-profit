# Performance measurement

The Gunicorn process runs one worker with four threads because cache invalidation is local to that process. `runtime_metrics.py` logs requests above 300 ms and records `total_ms`, `acquire_ms`, `sql_ms`, `fetch_ms`, and `render_ms` without financial values. `pool_guard.py` returns 503 when a created pool is exhausted. Pool creation failure and stale checkout failure may still open a direct connection; the guard is not a universal connection budget.

Database setup is an explicit `python manage_db.py init` operation, not an application import side effect. The web worker does not import data from a local SQLite file. See [operations.md](operations.md) before deployment.

## Verify

Run `python -m pytest -q` locally; PostgreSQL integration tests need a disposable `CI_DATABASE_URL`. GitHub Actions supplies a PostgreSQL 16 test service, runs the synthetic suite, and boots Gunicorn from an isolated copy without real financial files. CI success does not establish production speed or correctness of an operator backup.

For a short window, enable `PERF_LOG_ALL=1`, inspect endpoint timing, and then disable it. With a dedicated test account and credentials in private environment variables, use `python latency_probe.py --url https://<site-origin> --runs 20` to compare authenticated login and page round trips. The probe obtains the login CSRF token but does not write financial rows. Compare p50/p95 for warm and known fresh restarts separately. Browser paint and chart work require a separate browser waterfall. `/health` does not access the database.

Interpret high `acquire_ms` as connection or database-network time, high `sql_ms`/`fetch_ms` as a candidate for staging `EXPLAIN (ANALYZE, BUFFERS)`, and high `total_ms` with small measured parts as application computation or uninstrumented work. If HTTP is fast but the interface feels slow, inspect asset download and chart rendering. Never infer a production speedup from synthetic SQLite timings.

## September 27 stability and latency changes

- Daily-ledger pages reuse monthly components already aggregated for their
  summaries, avoiding a second database connection and aggregate scan.
- Input pages reuse the latest applicable row from recent history: the usual
  cold request needs two SELECTs instead of three, and a warm request opens no
  connection. The comparison cache expires at the Korean calendar-day boundary.
- Excel imports buffer 200 upserts at a time. PostgreSQL uses `execute_batch`:
  450 standard rows take three batch round trips instead of 450 single-row
  round trips, plus the final commit. This is an operation count, not a measured
  production latency ratio. All batches share one transaction; a malformed
  later row rolls back earlier batches. Row order and existing legacy memos are
  preserved. Performance metrics count each batch call once and include its time.
- A disconnected PostgreSQL transaction is never resumed halfway on a fresh
  connection. Only an isolated initial SELECT (or an autocommit read) may retry;
  a failed save must be retried by the user as a whole operation.
- Calculator-setting cache invalidation waits for in-flight readers. Early
  request rejection no longer causes a second error in the metrics response hook.
  Compression respects clients that explicitly refuse gzip.

Regression coverage is in `tests/test_stability_speed.py`,
`tests/test_runtime_metrics.py`, and `tests/test_postgres_integration.py`.
No schema migration or production-data rewrite is required.
