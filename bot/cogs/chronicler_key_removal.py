"""Best-effort removal of one player key from Chronicl3r, shared by every cog.

`/registration register` (a replaced key), `/registration unregister` and
`/deregister_guild` all leave the SAME record for a key Chronicl3r may still
hold, so one grep finds every key the bot failed to remove. A success leaves
no record.

Holds no key and reads none: the caller passes the key in, and it goes no
further than the service call. The record is emitted on the CALLER's logger,
so it stays greppable by origin.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.obs import emit_structured

if TYPE_CHECKING:
    from bot.services.chronicl3r.player_service import PlayerService

CHRONICLER_UNLINK_EVENT = "chronicler.key.unlink"
UNLINK_FAILED_CODE = "unlink_failed"

# What a raised Chronicl3r call is recorded as: the same as an unreachable
# Chronicl3r, since the outcome for the player is identical.
RAISED_CODE = "transport"


async def remove_key_from_chronicler(
    player_service: PlayerService, key: str, *,
    logger: logging.Logger, server_id, discord_id: str,
) -> bool:
    """True when Chronicl3r confirmed the removal; a failure is recorded, never raised."""
    try:
        code = None if await player_service.unlink_key(key) else UNLINK_FAILED_CODE
    except Exception:
        code = RAISED_CODE
    if code is None:
        return True
    emit_structured(
        logger, logging.WARNING, CHRONICLER_UNLINK_EVENT,
        server_id=server_id, discord_id=discord_id, code=code,
    )
    return False
