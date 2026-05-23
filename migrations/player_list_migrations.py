EPOCH = "1970-01-01T00:00:00Z"


class PlayerListMigrator:
    CURRENT_VERSION = 2

    @classmethod
    def get_version(cls, data: dict) -> int:
        return data.get("__meta__", {}).get("version", 1)

    @classmethod
    def migrate(cls, data: dict) -> tuple[dict, bool]:
        """Run chained migrations until CURRENT_VERSION. Returns (data, was_migrated)."""
        version = cls.get_version(data)
        migrated = False
        while version < cls.CURRENT_VERSION:
            fn = getattr(cls, f"_migrate_v{version}_to_v{version + 1}")
            data = fn(data)
            version = cls.get_version(data)
            migrated = True
        return data, migrated

    @staticmethod
    def _migrate_v1_to_v2(data: dict) -> dict:
        """Flip {name: tacticus_id} to v2 structure. Sets last_validated=epoch so first
        validate_if_stale call triggers a real refresh from the API."""
        players = {}
        for name, uid in data.items():
            if isinstance(name, str) and isinstance(uid, str):
                players[uid] = {
                    "display_name": name,
                    "last_validated": EPOCH,
                    "is_former": False,
                }
        return {
            "__meta__": {"version": 2},
            "players": players,
        }
