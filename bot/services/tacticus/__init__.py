"""Tacticus-direct service calls.

The only package permitted to issue requests against api.tacticusgame.com for
guild-scoped data (ADR-003 allow-list row #2, amended by feature
`guild-key-integrity`). `bot/services/chronicl3r/` keeps its Chronicler calls
and no longer holds any Tacticus HTTP.

This `__init__.py` exists so the import-linter contracts in pyproject.toml can
resolve the package — `bot/services/` had exactly one subpackage before.
"""
