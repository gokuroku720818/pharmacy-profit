# Deployment and data safety

The application no longer writes schema or copies a SQLite database when a web worker starts. The historical 38-item miscellaneous-profit seed is disabled; existing `extra_profit` rows remain unchanged. Deploying this change does not delete any financial record or reset any account.

## Before deploying

1. Identify the production `DATABASE_URL` target privately and confirm whether it is PostgreSQL or persistent SQLite. Do not paste credentials or a financial backup into an issue or PR.
2. Make a protected backup of that exact database and restore it in an isolated environment. Compare counts for `users`, `daily_profit`, `monthly_summary`, and `extra_profit`; spot-check monthly totals and a saved day's memo. Verify that the restored account can log in.
3. Confirm the existing schema is present. For a *new, empty* database only, run `python manage_db.py init` in an operator-controlled job with the intended `DATABASE_URL` and a private `ADMIN_BOOTSTRAP_PASSWORD` of at least 16 characters. This command provisions an admin only when none exists. Never run schema setup as part of Gunicorn startup or against an unverified target.
4. Set a private, random `SECRET_KEY` of at least 32 characters in the hosting environment. Deploying a new key signs out existing sessions. Ensure it is available on every worker; never put it in git.
5. To replace the existing admin password, set `ADMIN_NEW_PASSWORD` privately, then run `python manage_db.py rotate-admin-password` against the verified target. Remove the temporary environment variable afterwards. The bootstrap password does not rotate an existing account.
6. Deploy after steps 1–5. Test login/logout, daily save and reread, miscellaneous-profit totals, Excel import rollback, report/export, and the calculator in the isolated environment first. Keep the predeployment commit and verified database backup for rollback.

## Legacy SQLite-to-PostgreSQL data

There is no automatic data transfer. If an older SQLite file is the authoritative source, treat its import as a separate planned migration: verify source and destination ownership, restore both into an isolated environment, inspect account and row counts, reconcile financial totals, and prepare a one-time transfer with a rollback. Do not copy it just because a PostgreSQL daily table happens to be empty. The explicit `init` command provisions a new administrator on a blank target.

## Timing

`runtime_metrics.py` reports slow requests by endpoint. For a short diagnostic window, set `PERF_LOG_ALL=1` and compare `total_ms`, `acquire_ms`, `sql_ms`, `fetch_ms`, and `render_ms` for login, dashboard, calendar, input, report, and calculator. Clear the flag afterwards. Use a dedicated test account and `PERF_TEST_USER`/`PERF_TEST_PASSWORD` from private environment settings to run `python latency_probe.py --url https://<site-origin> --runs 20`; the probe obtains the login CSRF token and reports p50/p95/max without writing financial rows. Compare warm requests and a controlled restart separately. The `/health` response does not test the database.
