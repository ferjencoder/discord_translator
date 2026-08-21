from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from pathlib import Path

import discord

from settings import ConfigError, load_identity


class ExportClient(discord.Client):
    def __init__(self, *, server_id: int, output: Path) -> None:
        super().__init__(intents=discord.Intents.default())
        self.server_id = server_id
        self.output = output
        self._ran = False
        self.failure: BaseException | None = None

    async def on_ready(self) -> None:
        if self._ran:
            return
        self._ran = True
        try:
            await self._export()
        except Exception as exc:
            self.failure = exc
            print(f"ERROR: {exc}")
        finally:
            await self.close()

    async def _export(self) -> None:
        guild = self.get_guild(self.server_id)
        if guild is None:
            raise RuntimeError(f"Expected guild {self.server_id} was not found")

        with self.output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "Guild",
                    "Guild ID",
                    "Category",
                    "Category ID",
                    "Channel Name",
                    "Channel ID",
                    "Target",
                    "Target ID",
                    "Type",
                    "Allowed Permissions",
                    "Denied Permissions",
                ]
            )

            for channel in guild.channels:
                category_name = channel.category.name if channel.category else "No Category"
                category_id = channel.category.id if channel.category else ""
                for target, overwrite in channel.overwrites.items():
                    target_name = getattr(target, "name", str(target))
                    target_type = "Role" if isinstance(target, discord.Role) else "Member"
                    allowed = [perm for perm, value in overwrite if value is True]
                    denied = [perm for perm, value in overwrite if value is False]
                    writer.writerow(
                        [
                            guild.name,
                            guild.id,
                            category_name,
                            category_id,
                            channel.name,
                            channel.id,
                            target_name,
                            target.id,
                            target_type,
                            ", ".join(allowed) if allowed else "None",
                            ", ".join(denied) if denied else "None",
                        ]
                    )

        print(f"Permission export saved to {self.output}")


def main() -> None:
    try:
        token, server_id = load_identity()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = Path(f"channel_permissions_export_{timestamp}.csv")
    async def runner() -> None:
        client = ExportClient(server_id=server_id, output=output)
        try:
            await client.start(token)
        finally:
            if not client.is_closed():
                await client.close()
        if client.failure:
            raise RuntimeError(str(client.failure)) from client.failure

    try:
        asyncio.run(runner())
    except RuntimeError as exc:
        raise SystemExit(f"Permission export failed: {exc}") from exc


if __name__ == "__main__":
    main()
