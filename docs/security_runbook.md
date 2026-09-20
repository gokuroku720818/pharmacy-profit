# Repository data exposure and credential safety

## Confirmed metadata only

As of 2026-09-20, this repository is public and Git tracks `data/sales.db` and `data/export_data.json`. These files were **not opened or inspected during this review**. Their names and types suggest they may contain financial or account information; treat them as potentially sensitive until the owner verifies content privately.

`.gitignore` now blocks newly added local DB and financial exports. **It does not untrack, delete, make private, or purge any file already committed, nor does it affect existing forks/clones or copies.** Do not assume data is safe because files disappear from the latest commit.

## Owner-controlled containment sequence (before repository history changes)

1. Confirm which DB Render actually uses (`DATABASE_URL` PostgreSQL vs local SQLite) *without displaying connection strings*. Verify current deployment commit and persistent storage, then create a verified, access-controlled production backup and test restore to an isolated environment. Do not expose the backup through GitHub.
2. Restrict public access to the repository if appropriate for the business, then determine in private whether the committed DB/JSON contain private financial or authentication information. Do not paste raw content in issues, logs, or chat.
3. Rotate any real administrative password that may have been shared or committed, set a long random `SECRET_KEY` through the hosting provider, and plan a session invalidation window; an existing hard-coded fallback in application code requires a separate tested code change. If credentials for PostgreSQL or integrations were committed, rotate them in their respective services as well.
4. Only after confirming production does not depend on tracked seed files, stop tracking copies (`git rm --cached ...`); keep backups outside the repository. Removal from the current branch does **not** erase prior versions.
5. Arrange a coordinated history purge with `git filter-repo` or an equivalent tool only after repository-owner review of branches, tags, pull requests, forks, deployment assumptions and collaborators. A rewrite requires coordinated re-cloning and can break deployed references. GitHub support may be needed for cached refs. Assume previously public bytes may have been copied.
6. After deploying, check authentication, monthly summaries, Excel import/export and backups using synthetic data. Verify that the production database and files were not lost. Scan tracked paths in each new release, add secret scanning, and keep actual backups in private storage.

## Deployment gate

This PR intentionally does **not** delete tracked databases, rewrite Git history, change credentials, or alter production data. Do not merge this PR expecting it to resolve historical disclosure; `.gitignore` is only a prevention measure for future untracked files. The connection-pool and performance tests use a separate ephemeral GitHub Actions PostgreSQL database and do not connect to production.
