from __future__ import annotations

import asyncio
import argparse

import discord

from settings import (
    CATEGORY_NAME,
    CHANNEL_SPECS,
    TRANSLATOR_ROLE_NAME,
    ConfigError,
    load_identity,
)

CONFIRM_PHRASE = "OZY_FIX_TRANSLATION_PERMISSIONS"

MESSAGE_PERMISSIONS = {
    "view_channel": True,
    "send_messages": True,
    "read_message_history": True,
    "embed_links": True,
    "attach_files": True,
    "add_reactions": True,
    "use_external_emojis": True,
    "send_messages_in_threads": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely repair OZY translation channel permissions.")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    parser.add_argument("--confirm", default="", help=f"Required with --apply: {CONFIRM_PHRASE}")
    return parser.parse_args()


def changes_for(overwrite: discord.PermissionOverwrite, desired: dict[str, bool]) -> list[str]:
    changes = []
    for name, value in desired.items():
        current = getattr(overwrite, name)
        if current is not value:
            changes.append(f"{name}: {current} -> {value}")
    return changes


class PermissionClient(discord.Client):
    def __init__(self, *, server_id: int, args: argparse.Namespace) -> None:
        super().__init__(intents=discord.Intents.default())
        self.server_id = server_id
        self.args = args
        self._ran = False
        self.failure: BaseException | None = None

    async def on_ready(self) -> None:
        if self._ran:
            return
        self._ran = True
        try:
            await self._run_fix()
        except Exception as exc:
            self.failure = exc
            print(f"ERROR: {exc}")
        finally:
            await self.close()

    async def _run_fix(self) -> None:
        guild = self.get_guild(self.server_id)
        if guild is None:
            raise RuntimeError(f"Expected guild {self.server_id} was not found")

        if self.args.apply and self.args.confirm != CONFIRM_PHRASE:
            raise RuntimeError(f"Refusing changes. With --apply pass --confirm {CONFIRM_PHRASE}")

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if category is None:
            raise RuntimeError(f"Category guard failed: {CATEGORY_NAME!r} not found")

        translator_role = discord.utils.get(guild.roles, name=TRANSLATOR_ROLE_NAME)
        if translator_role is None:
            raise RuntimeError(f"Role guard failed: {TRANSLATOR_ROLE_NAME!r} not found")

        print(f"Guild guard: {guild.name} ({guild.id})")
        print(f"Category guard: {category.name} ({category.id})")
        print("Mode:", "APPLY" if self.args.apply else "DRY RUN")

        for spec in CHANNEL_SPECS:
            channel = guild.get_channel(spec.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(f"Channel {spec.lang} ID {spec.channel_id} not found")
            if channel.category_id != category.id:
                raise RuntimeError(f"#{channel.name} is not inside {CATEGORY_NAME!r}")
            if channel.name != spec.channel_name:
                raise RuntimeError(
                    f"Channel-name guard failed for {spec.lang}: expected #{spec.channel_name}, got #{channel.name}"
                )

            language_role = discord.utils.get(guild.roles, name=spec.role_name)
            if language_role is None:
                raise RuntimeError(f"Role guard failed for {spec.lang}: role {spec.role_name!r} not found")

            targets = (
                (guild.default_role, {"view_channel": False, "send_messages": False}),
                (translator_role, MESSAGE_PERMISSIONS),
                (language_role, MESSAGE_PERMISSIONS),
            )

            print(f"\n#{channel.name} ({channel.id})")
            for target, desired in targets:
                overwrite = channel.overwrites_for(target)
                changes = changes_for(overwrite, desired)
                if not changes:
                    print(f"  {target.name}: already correct")
                    continue
                print(f"  {target.name}:")
                for change in changes:
                    print(f"    {change}")

                if self.args.apply:
                    overwrite.update(**desired)
                    await channel.set_permissions(
                        target,
                        overwrite=overwrite,
                        reason="OZY translator guarded permission repair",
                    )

            # Important: unrelated member/role overwrites are intentionally untouched.

        if not self.args.apply:
            print(f"\nDry run only. To execute: add --apply --confirm {CONFIRM_PHRASE}")
        else:
            print("\nPermission repair complete. Unrelated overwrites were preserved.")


def main() -> None:
    args = parse_args()
    try:
        token, server_id = load_identity()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    async def runner() -> None:
        client = PermissionClient(server_id=server_id, args=args)
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
        raise SystemExit(f"Permission repair failed: {exc}") from exc


if __name__ == "__main__":
    main()
