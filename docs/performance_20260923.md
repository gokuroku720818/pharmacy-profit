# Dashboard and input performance verification

Changes:
- Cache dashboard calculations in the existing per-user cache for at most 300 seconds.
- Re-render HTML per request; never cache session UI or flash messages.
- Existing write invalidation clears this context; Korean date changes also expire it.
- Guard publication with bucket identity and retain the production per-user read lock.
- Remove the partial-month input preload and retain the independent recent-history cache.
- Fetch the OCR engine only when an image is processed; concurrent loads share one promise.
- Respect gzip quality negotiation, and vary identity responses by Accept-Encoding too.
- Restore the missing regression workflow and test the actual Gunicorn config.

Local comparison: temporary SQLite database, 1,725 generated daily records,
production extension bootstrap, one warm-up and 50 sequential dashboard requests.

| Measurement | Before | After |
| --- | ---: | ---: |
| Dashboard connections across 50 requests | 50 | 0 |
| Dashboard SELECTs across 50 requests | 150 | 0 |
| Median local request time | 3.74 ms | 1.26–1.38 ms |
| p95 local request time | 4.78 ms | 1.70–1.72 ms |

These are local server measurements, not production browser/network speed claims.
Cold starts, hosting region, and live PostgreSQL latency were not measured here.
Direct external database changes bypassing app invalidation may remain cached up to
300 seconds, as with existing monthly caches. Keep the configured single worker.

Validation: 114 Python tests passed, 5 environment-dependent tests skipped; OCR
loader test passed. Temporary Gunicorn instance served 32 requests with 8 clients,
all HTTP 200. No live financial data was edited during verification.
