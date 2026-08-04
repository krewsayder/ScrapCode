# Runbook — feature `guild-key-integrity`

Written 2026-08-01 in response to the Final Wave Review Gate (Forge finding:
`environments.yaml` names the hourly backup as "the only rollback for a bad
migration" but no restore procedure existed anywhere for this feature).

Four procedures, in the order you are likely to need them. All paths are on the
production VM under `/opt/discord-bot`.

---

## ⚠️ Read this before the Slice 01 deploy

`docs/feature/sqlite-backend/devops/cutover-runbook.md` records, under *Known
gaps at cutover time*, that **the backup timer was never installed**:

> Backup timer (`platform-architecture.md` §6) is not installed. Until it is,
> take manual snapshots.

`environments.yaml` for this feature assumes the timer exists and makes
verifying it deploy step 1. **Confirm which is true before migrating**, because
Slice 01 is the only slice in this feature that carries a schema change, and
this backup is its only undo:

```bash
systemctl status discord-bot-backup.timer
```

If that reports `Unit ... could not be found`, take a manual snapshot and do not
skip it:

```bash
sudo -u discord-bot sqlite3 /opt/discord-bot/data/scrapcode.db \
  "VACUUM INTO '/opt/discord-bot/data/backups/pre-guildkey-$(date +%Y%m%dT%H%M%S).db'"
ls -la /opt/discord-bot/data/backups/ | tail -3
```

`VACUUM INTO` is a safe online backup — it does not lock out the running bot and
produces a defragmented copy.

---

## 1. The migration failed or the database is corrupt

**Symptom:** `alembic upgrade head` errored, or the bot will not start after the
Slice 01 deploy and `journalctl` shows a probe refusal that is not a version
mismatch.

**First try the ordinary rollback (§2).** Only restore from backup if the
database file itself is damaged — a restore discards every hit and roster row
written since the snapshot.

```bash
sudo systemctl stop discord-bot

# Pick the newest snapshot taken BEFORE the migration.
ls -la /opt/discord-bot/data/backups/

cd /opt/discord-bot
cp data/backups/<good-snapshot>.db data/scrapcode.db

# Load-bearing: the stale WAL and shared-memory files belong to the OLD
# database. Leaving them behind corrupts the restored copy.
rm -f data/scrapcode.db-wal data/scrapcode.db-shm

# The restored file predates the migration, so the code must too.
git checkout "$(git describe --tags --abbrev=0 HEAD^)"

sudo systemctl start discord-bot
journalctl -u discord-bot -n 50 | grep db.probe
```

**Verify** the probe passes all four steps and that row counts look sane:

```bash
sqlite3 data/scrapcode.db \
  "SELECT (SELECT COUNT(*) FROM players), (SELECT COUNT(*) FROM battle_hits), (SELECT COUNT(*) FROM bomb_hits);"
```

Data written between the snapshot and the failure is gone. Tacticus still holds
the current season, so the next successful `auto_update` re-ingests the current
season's hits — but roster history and prior seasons do not come back.

---

## 2. The rollback ordering trap

**This is the single most likely operational mistake in this feature.**

The startup probe compares the database's `alembic_version` against the
compiled head with a strict `!=` **in both directions**
([session.py:224](../../../../bot/db/session.py#L224)). So a code rollback
without a schema downgrade leaves the database *ahead* of the code, the probe
refuses, and the unit lands in `failed`.

```bash
# CORRECT — downgrade BEFORE checkout
sudo systemctl stop discord-bot
cd /opt/discord-bot
.venv/bin/alembic downgrade -1
git checkout "$(git describe --tags --abbrev=0 HEAD^)"
sudo systemctl start discord-bot
```

If you already reversed the order and the bot is down, you do **not** need the
backup. Check out the new code again, downgrade, then check out the old code:

```bash
sudo systemctl stop discord-bot
git checkout -            # back to the version whose alembic knows the revision
.venv/bin/alembic downgrade -1
git checkout "$(git describe --tags --abbrev=0 HEAD^)"
sudo systemctl start discord-bot
```

Slices 02 and 03 carry no migration: `stop → pull → start → verify`.

---

## 3. A guild is quarantined

**Symptom:** `⛔` in the update channel and in `/view_config config:guilds`; that
guild's leaderboard has stopped moving.

This is the feature working. It means the guild's key now resolves to a
different Tacticus guild than the one it is bound to — usually because the key's
owner changed guilds, which is exactly what happened on 2026-07-28.

**Recovery is a Discord command. Do not open an SSH session.**

1. Read the alert. It names both the bound guild and the guild the key now
   resolves to.
2. Get a fresh key from someone who is *currently* in the bound guild and has
   guild-scope permission.
3. Install it:
   ```
   /update_guild_key guild_id:<guild> api_key:<new key>
   ```
   The command probes the submitted key **before** storing it and refuses if it
   resolves to the wrong guild. On success the quarantine clears automatically
   and ingestion resumes on the next hourly cycle.

**If the guild genuinely moved** and you want to track it at its new identity,
re-run with `force:true`. This re-binds; it does not merge history.

**Never** use `/deregister_guild` + `/register_guild` to swap a key. That
CASCADE-deletes every player and hit row for the guild.

**Never** edit `api_key` with raw SQL. It is Fernet ciphertext and
`api_key_hmac` must be recomputed in the same write.

Confirm afterwards:

```bash
grep 'guild.key.updated' /opt/discord-bot/discord.log | tail -1
```

---

## 4. Every guild reports "identity verification is offline"

**Symptom:** `guild.key.unverifiable` for every guild in the same cycle, a loud
persistent alert, and **nothing quarantined**.

Tacticus has stopped returning `guildId`. That field is undocumented by the
vendor and is the single field this whole feature binds on, so its removal
disables verification cluster-wide.

The system is behaving correctly: it degrades loudly and blocks nothing.
Ingestion continues, because quarantining on a vendor change would take every
guild down over someone else's release note.

**What to do:**

1. Confirm it is the vendor and not one guild:
   ```bash
   grep 'guild.key.unverifiable' /opt/discord-bot/discord.log | tail -20
   ```
   Every registered guild in one cycle means vendor. One guild means that
   guild's key.

2. Confirm against the live endpoint with a real key:
   ```bash
   SCRAPCODE_TACTICUS_CONTRACT_KEY=<key> \
     .venv/bin/python -m pytest tests/acceptance/guild-key-integrity/ \
     -k requires_external -v
   ```

3. If the field is gone for good, this feature's guarantee is gone with it.
   Re-record `tests/acceptance/guild-key-integrity/fixtures/guild_response_recorded.json`
   from the new response and review the diff — that diff is the decision
   record for what to bind on next.

**Do not** "fix" this by falling back to comparing `guildTag`. Both guilds in
the original incident shared the 【UNDV】 alliance prefix. A tag comparison would
have looked reassuring and proved nothing, which is the same failure shape as
the incident itself.
