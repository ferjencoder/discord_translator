from __future__ import annotations

import asyncio
import argparse
from datetime import datetime, timedelta, timezone

import discord

from settings import CATEGORY_NAME, CHANNELS_BY_LANG, ConfigError, load_identity

CONFIRM_PHRASE = "OZY_DELETE_TRANSLATIONS"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely purge configured OZY translation channels.")
    parser.add_argument(
        "--channels",
        nargs="+",
        choices=sorted(CHANNELS_BY_LANG),
        required=True,
        help="Language channel codes to purge, e.g. --channels en es fr",
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete messages. Default is dry-run.")
    parser.add_argument("--confirm", default="", help=f"Required with --apply: {CONFIRM_PHRASE}")
    parser.add_argument("--include-pinned", action="store_true", help="Also delete pinned messages.")
    parser.add_argument("--max-delete", type=int, default=5000, help="Safety ceiling per channel (default: 5000).")
    return parser.parse_args()


class PurgeClient(discord.Client):
    def __init__(self, *, server_id: int, args: argparse.Namespace) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.server_id = server_id
        self.args = args
        self._ran = False
        self.failure: BaseException | None = None

    async def on_ready(self) -> None:
        if self._ran:
            return
        self._ran = True
        try:
            await self._run_purge()
        except Exception as exc:
            self.failure = exc
            print(f"ERROR: {exc}")
        finally:
            await self.close()

    async def _run_purge(self) -> None:
        guild = self.get_guild(self.server_id)
        if guild is None:
            raise RuntimeError(f"Expected guild {self.server_id} was not found")

        print(f"Guild guard: {guild.name} ({guild.id})")
        print("Mode:", "APPLY" if self.args.apply else "DRY RUN")

        if self.args.apply and self.args.confirm != CONFIRM_PHRASE:
            raise RuntimeError(f"Refusing deletion. With --apply you must pass --confirm {CONFIRM_PHRASE}")
        if self.args.max_delete < 1:
            raise RuntimeError("--max-delete must be >= 1")

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if category is None:
            raise RuntimeError(f"Category guard failed: {CATEGORY_NAME!r} not found")

        for lang in self.args.channels:
            spec = CHANNELS_BY_LANG[lang]
            channel = guild.get_channel(spec.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(f"Channel guard failed for {lang}: ID {spec.channel_id} not found")
            if channel.category_id != category.id:
                raise RuntimeError(f"Channel guard failed for #{channel.name}: wrong category")
            if channel.name != spec.channel_name:
                raise RuntimeError(
                    f"Channel guard failed for {lang}: expected #{spec.channel_name}, got #{channel.name}"
                )

            candidates: list[discord.Message] = []
            async for message in channel.history(limit=None):
                if message.pinned and not self.args.include_pinned:
                    continue
                candidates.append(message)
                if len(candidates) > self.args.max_delete:
                    break

            if len(candidates) > self.args.max_delete:
                raise RuntimeError(
                    f"#{channel.name} exceeds --max-delete={self.args.max_delete}; refusing to continue"
                )

            print(f"#{channel.name} ({channel.id}): {len(candidates)} messages eligible")
            if not self.args.apply or not candidates:
                continue

            cutoff = datetime.now(timezone.utc) - timedelta(days=13, hours=23)
            recent = [m for m in candidates if m.created_at > cutoff]
            old = [m for m in candidates if m.created_at <= cutoff]

            # Discord bulk-delete accepts up to 100 messages and cannot bulk-delete >14-day messages.
            for i in range(0, len(recent), 100):
                batch = recent[i : i + 100]
                if len(batch) == 1:
                    await batch[0].delete(reason="OZY translator maintenance purge")
                elif batch:
                    await channel.delete_messages(batch, reason="OZY translator maintenance purge")

            for message in old:
                await message.delete(reason="OZY translator maintenance purge")

            print(f"  deleted {len(candidates)} messages")

        if not self.args.apply:
            print(f"Dry run only. To execute: add --apply --confirm {CONFIRM_PHRASE}")


def main() -> None:
    args = parse_args()
    try:
        token, server_id = load_identity()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    async def runner() -> None:
        client = PurgeClient(server_id=server_id, args=args)
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
        raise SystemExit(f"Purge failed: {exc}") from exc


if __name__ == "__main__":
    main()