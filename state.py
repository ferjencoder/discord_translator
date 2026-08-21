from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class MessageMapRow:
    source_message_id: int
    target_channel_id: int
    chunk_index: int
    webhook_message_id: int


@dataclass(frozen=True)
class ReactionMapRow:
    source_message_id: int
    source_channel_id: int
    target_lang: str
    chunk_index: int
    bot_message_id: int


class MessageState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_map (
                    source_message_id INTEGER NOT NULL,
                    target_channel_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    webhook_message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_message_id, target_channel_id, chunk_index)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_map_source ON message_map(source_message_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reaction_map (
                    source_message_id INTEGER NOT NULL,
                    source_channel_id INTEGER NOT NULL,
                    target_lang TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    bot_message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_message_id, target_lang, chunk_index)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reaction_map_source ON reaction_map(source_message_id)"
            )

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _replace_target_sync(self, source_message_id: int, target_channel_id: int, message_ids: list[int]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM message_map WHERE source_message_id=? AND target_channel_id=?",
                (source_message_id, target_channel_id),
            )
            conn.executemany(
                """
                INSERT INTO message_map
                (source_message_id, target_channel_id, chunk_index, webhook_message_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(source_message_id, target_channel_id, i, mid, now) for i, mid in enumerate(message_ids)],
            )

    async def replace_target(self, source_message_id: int, target_channel_id: int, message_ids: list[int]) -> None:
        await asyncio.to_thread(self._replace_target_sync, source_message_id, target_channel_id, message_ids)

    def _get_sync(self, source_message_id: int) -> list[MessageMapRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_message_id, target_channel_id, chunk_index, webhook_message_id
                FROM message_map WHERE source_message_id=?
                ORDER BY target_channel_id, chunk_index
                """,
                (source_message_id,),
            ).fetchall()
        return [MessageMapRow(*row) for row in rows]

    async def get(self, source_message_id: int) -> list[MessageMapRow]:
        return await asyncio.to_thread(self._get_sync, source_message_id)

    def _delete_source_sync(self, source_message_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM message_map WHERE source_message_id=?", (source_message_id,))

    async def delete_source(self, source_message_id: int) -> None:
        await asyncio.to_thread(self._delete_source_sync, source_message_id)

    def _replace_reaction_sync(
        self, source_message_id: int, source_channel_id: int, target_lang: str, message_ids: list[int]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM reaction_map WHERE source_message_id=? AND target_lang=?",
                (source_message_id, target_lang),
            )
            conn.executemany(
                """
                INSERT INTO reaction_map
                (source_message_id, source_channel_id, target_lang, chunk_index, bot_message_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(source_message_id, source_channel_id, target_lang, i, mid, now) for i, mid in enumerate(message_ids)],
            )

    async def replace_reaction(
        self, source_message_id: int, source_channel_id: int, target_lang: str, message_ids: list[int]
    ) -> None:
        await asyncio.to_thread(
            self._replace_reaction_sync, source_message_id, source_channel_id, target_lang, message_ids
        )

    def _get_reactions_sync(self, source_message_id: int) -> list[ReactionMapRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_message_id, source_channel_id, target_lang, chunk_index, bot_message_id
                FROM reaction_map WHERE source_message_id=?
                ORDER BY target_lang, chunk_index
                """,
                (source_message_id,),
            ).fetchall()
        return [ReactionMapRow(*row) for row in rows]

    async def get_reactions(self, source_message_id: int) -> list[ReactionMapRow]:
        return await asyncio.to_thread(self._get_reactions_sync, source_message_id)

    def _has_reaction_sync(self, source_message_id: int, target_lang: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM reaction_map WHERE source_message_id=? AND target_lang=? LIMIT 1",
                (source_message_id, target_lang),
            ).fetchone()
        return row is not None

    async def has_reaction(self, source_message_id: int, target_lang: str) -> bool:
        return await asyncio.to_thread(self._has_reaction_sync, source_message_id, target_lang)

    def _reaction_languages_sync(self, source_message_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT target_lang FROM reaction_map WHERE source_message_id=? ORDER BY target_lang",
                (source_message_id,),
            ).fetchall()
        return [row[0] for row in rows]

    async def reaction_languages(self, source_message_id: int) -> list[str]:
        return await asyncio.to_thread(self._reaction_languages_sync, source_message_id)

    def _delete_reaction_target_sync(self, source_message_id: int, target_lang: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM reaction_map WHERE source_message_id=? AND target_lang=?",
                (source_message_id, target_lang),
            )

    async def delete_reaction_target(self, source_message_id: int, target_lang: str) -> None:
        await asyncio.to_thread(self._delete_reaction_target_sync, source_message_id, target_lang)

    def _delete_reaction_source_sync(self, source_message_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM reaction_map WHERE source_message_id=?", (source_message_id,))

    async def delete_reaction_source(self, source_message_id: int) -> None:
        await asyncio.to_thread(self._delete_reaction_source_sync, source_message_id)

    def _cleanup_sync(self, retention_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._connect() as conn:
            a = conn.execute("DELETE FROM message_map WHERE created_at < ?", (cutoff,)).rowcount or 0
            b = conn.execute("DELETE FROM reaction_map WHERE created_at < ?", (cutoff,)).rowcount or 0
            return int(a + b)

    async def cleanup(self, retention_days: int) -> int:
        return await asyncio.to_thread(self._cleanup_sync, retention_days)
