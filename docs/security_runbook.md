# Security and data operations

See [operations.md](operations.md) for the current backup, session-key, password-rotation, schema setup, and deployment order.

The repository is public. Earlier project documentation mentioned tracked financial seed files; determine any historical exposure privately before making claims about the contents of past commits. A `.gitignore` rule cannot erase historical copies. Do not publish account credentials, database URLs, backups, or financial records in code or PRs.

The fixed session-key fallback and default admin password are removed in the proposed code. Hosted PostgreSQL derives a stable session key from its existing private `DATABASE_URL`; changing that URL signs out existing sessions. Existing admin credentials are not automatically changed: an operator can run `python manage_db.py rotate-admin-password` with a private `ADMIN_NEW_PASSWORD` against a verified target. The app no longer creates schema or imports legacy SQLite data when a web worker starts. Identify the authoritative Neon database and verify a backup and application flows before changing production data.
