"""SQLite backend package for ScrapCode.

Houses the SQLAlchemy 2.0 / Alembic / Fernet implementations per ADR-006
+ ADR-007: `session.Database` (engine + session_scope + probe), `models`
(the 12 ORM tables), `secrets` (Fernet), the Alembic baseline, and the
JSON->SQLite migration CLI. All DELIVER-wave scaffolds have been replaced
by real implementations; no scaffold markers remain.
"""