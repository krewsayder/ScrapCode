# Slice 06 — Admin command safety

**Feature:** `guild-key-integrity` · **Remediates:** KPI-6, AC-003.4, ADR-008 DDD-4
**Estimate:** ~1 day (≤6 h crafter dispatch) · **Order:** 3rd of the remediation set

## Goal

No admin command discloses key material, destroys history while reporting the
opposite, or launders a quarantine back to active.

## Learning hypothesis

**Disproves** "KPI-6 holds by construction" **if** any error path can still put
key-derived material into a log or a Discord message.

**Confirms** that "by construction" means the *failure* paths were designed,
not just the success paths. KPI-6's baseline cites a replacement key left in a
temp file — an error-handling artefact, not a feature. The same class of
mistake is what this slice closes.

## IN scope

- **Typed refusal on an `api_key_hmac` collision.**
  `guilds.api_key_hmac` is `UNIQUE` **table-global**
  ([`models.py:87`](../../../../bot/db/models.py#L87),
  [`0001_baseline_schema.py:60`](../../../../bot/db/alembic/versions/0001_baseline_schema.py#L60)).
  Nothing on the key-write path catches `IntegrityError` — the only catch, at
  [`repository_sqlalchemy.py:620`](../../../../bot/repository_sqlalchemy.py#L620),
  is scoped to the replay-URL constraint. The exception escapes to
  [`main.py`](../../../../main.py#L91), which does
  `print(f"Command error: {error}")` **and** `f"❌ An error occurred: {error}"`
  → `followup.send`. SQLAlchemy inlines `[parameters: …]`, so the **Fernet
  ciphertext of the key and the full 64-hex `api_key_hmac`** land in
  `discord.log`, the journal, and a Discord message. Translate to a typed
  exception the cog renders as "that key is already registered to another
  guild" — the same pattern already used for the replay-URL constraint.
- **Make AC-003.4 reachable.** Force-rebind onto a key present elsewhere in
  the table currently always raises, so the admin cannot install the correct
  key at all. The typed refusal must state which guild holds it (by slug,
  never by key material).
- **`/deregister_guild` must tell the truth.**
  [`admin_cog.py:223-225`](../../../../bot/cogs/admin_cog.py#L223) replies
  *"⚠️ Their data folder has been left intact in case you need it."* That was
  true on JSON. Post-cutover `save_guilds` deletes the `GuildRow`,
  `PRAGMA foreign_keys=ON` is live, and every child FK is `ondelete="CASCADE"`
  — players, battle_hits, bomb_hits and the binding are all destroyed.
  Measured: `{players:1, battle_hits:1, bomb_hits:1, bindings:1}` → all `0`.
  The reply must state what is actually deleted, and the command must confirm
  before doing it.
- **Re-registration must not launder a quarantine.** CASCADE drops the
  binding, so deregister + re-register the same slug returns `is_unbound=True`
  and trust-on-first-use silently adopts a drifted key. Two commands, no
  warning. At minimum: warn on re-registering a slug whose binding was
  quarantined.
- **`guild_key_bindings` into `_DATA_TABLES_DELETE_ORDER`.**
  [`migrations_json_to_sqlite.py:65-77`](../../../../bot/db/migrations_json_to_sqlite.py#L65)
  omits it, and `_rollback_data` deletes with `PRAGMA foreign_keys=OFF`, so a
  parity rollback leaves orphaned quarantined bindings that a later
  re-registration silently re-adopts. `GuildKeyBindingRow`'s own docstring
  calls the CASCADE "load-bearing" against exactly this.
- **Guard `replace_guild_key(..., "")`** — currently blanks the key and NULLs
  the hmac with no error. Unreachable via the cog today; the repository method
  is the sanctioned write path and should not depend on that.

## OUT of scope

- Changing `api_key_hmac`'s UNIQUE to be per-tenant. It is a real
  cross-tenant coupling (two Discord servers cannot register the same Tacticus
  key, and one tenant can detect another's key by collision) but it is a
  schema decision needing its own ADR — recorded in `../remediation-plan.md`.
- Soft-delete / archival for deregistered guilds — **decided against**
  (operator, 2026-08-02). Deregistering destroys the data by design; the CASCADE
  stays exactly as it is.
- Redacting `main.py`'s generic handler globally. Worth doing, but it is a
  cross-cutting change and this slice must not depend on it: the fix here is
  that the exception never reaches that handler.

## Required behaviour (proposed AC-009.x — tests owned by `@nw-acceptance-designer`)

1. `/update_guild_key` with a key already registered to a sibling guild
   replies with a refusal naming the holding guild, and **no** `discord.log`
   line, Discord message, or traceback contains the ciphertext, the hmac, or
   any SQL.
2. Same with `force:true` — the refusal is still clean, and AC-003.4's
   legitimate force-rebind path succeeds where no collision exists.
3. `/deregister_guild` states the actual row counts it will delete and
   requires confirmation.
4. Re-registering a slug whose prior binding was quarantined surfaces that
   history rather than silently adopting.
5. After `_rollback_data`, `guild_key_bindings` is empty.
6. `replace_guild_key(..., "")` raises rather than blanking the row.

## Production-data criterion

Not synthetic. Install one real Tacticus key against two scratch guilds in the
same cluster to force a genuine `UNIQUE` violation from SQLite, then grep the
live `discord.log` for the guild's `api_key_hmac` prefix and assert zero hits.

## Dogfood moment (same day)

Operator pastes the wrong (sibling's) key into `/update_guild_key`, sees a
plain-language refusal naming the other guild, and finds `discord.log` clean —
then runs `/deregister_guild` and is told exactly how many hits and players
will be destroyed before confirming.

## Dependencies

None on slices 04/05. Can run in parallel if capacity allows.

## Reference class

`sqlite-backend` Slice 03 — translating storage-layer exceptions into typed
domain refusals at the repository boundary. The replay-URL `IntegrityError`
catch at `repository_sqlalchemy.py:603-620` is the pattern to copy verbatim.

## Pre-slice SPIKE

**Not required.** Both blocking defects were reproduced end-to-end against a
real SQLite DB with the real error text captured.

## Operator decision — 2026-08-02

**`/deregister_guild` destroys the data. That is intended.** No soft-delete, no
archival, no change to the CASCADE. The defect is purely that the reply claims
the opposite — *"Their data folder has been left intact"* — so the fix is to
make the message true, not to make the behaviour gentler.

The confirmation step stays in scope. Reading "destroy the data" as also
meaning "destroy it without asking" would be a stretch: the command
irreversibly deletes a guild's entire raid history with no undo and no backup,
and a Discord slash command takes one keystroke to fire. If you want it to
fire immediately instead, that is a one-line change — say so and it comes out.

## Risk

Low. Each fix is local and additive.

The residual risk is the reverse of the usual one: an operator who has learned
that deregistering is safe — because the bot has been telling them so — may
now discover it is not. The corrected message is the mitigation, and it should
state the actual row counts *before* the deletion, not after.
