# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

import aiosqlite
from pathlib import Path
from typing import AsyncIterator, Mapping

BUILD_TABLE = """
  CREATE TABLE IF NOT EXISTS Dict (
    key BLOB UNIQUE NOT NULL,
    value BLOB NOT NULL
  )
"""
GET_SIZE   = "SELECT COUNT(key) FROM Dict"
LOOKUP_KEY = "SELECT value FROM Dict WHERE key = CAST(? AS BLOB)"
STORE_KV   = "REPLACE INTO Dict (key, value) VALUES (CAST(? AS BLOB), CAST(? AS BLOB))"
DELETE_KEY = "DELETE FROM Dict WHERE key = CAST(? AS BLOB)"
ITER_KEYS  = "SELECT key FROM Dict"
ITER_ITEMS = "SELECT key, value FROM Dict"
CLEAR_ALL  = "DELETE FROM Dict"
REORGANIZE = "VACUUM"


class KvDB:
    def __init__(self, path: str | Path) -> None:
        self._path: Path = Path(path)
        self._cx: aiosqlite.Connection | None = None

    @classmethod
    async def open(cls, path: str | Path) -> "KvDB":
        self = cls(path)
        self._cx = await aiosqlite.connect(self._path, isolation_level=None)
        assert self._cx is not None
        try:
            await self._cx.execute("PRAGMA journal_mode = wal")
        except aiosqlite.OperationalError:
            pass
        await self._execute(BUILD_TABLE)
        return self

    async def _execute(self, sql: str, parameters: tuple[bytes, ...] = ()) -> aiosqlite.Cursor:
        if self._cx is None:
            raise RuntimeError("DBM object has already been closed")
        return await self._cx.execute(sql, parameters)

    async def set(self, key: bytes, value: bytes) -> None:
        cu = await self._execute(STORE_KV, (key, value))
        await cu.close()

    async def get(self, key: bytes) -> bytes | None:
        cu = await self._execute(LOOKUP_KEY, (key,))
        row = await cu.fetchone()
        await cu.close()
        return None if row is None else row[0]

    async def delete(self, key: bytes) -> bool:
        cu = await self._execute(DELETE_KEY, (key,))
        n = cu.rowcount
        await cu.close()
        return n != 0  # 返回删除的键是否存在

    async def contains(self, key: bytes) -> bool:
        cu = await self._execute(LOOKUP_KEY, (key,))
        row = await cu.fetchone()
        await cu.close()
        return row is not None

    async def size(self) -> int:
        cu = await self._execute(GET_SIZE)
        row = await cu.fetchone()
        await cu.close()
        assert row is not None
        return row[0]

    async def update(self, mapping: Mapping[bytes, bytes]) -> None:
        assert self._cx is not None
        await self._cx.execute("BEGIN")
        try:
            for k, v in mapping.items():
                await self._cx.execute(STORE_KV, (k, v))
            await self._cx.execute("COMMIT")
        except Exception:
            await self._cx.execute("ROLLBACK")
            raise

    async def clear(self) -> None:
        cu = await self._execute(CLEAR_ALL)
        await cu.close()

    async def iter_keys(self) -> AsyncIterator[bytes]:
        cu = await self._execute(ITER_KEYS)
        try:
            async for row in cu:
                yield row[0]
        finally:
            await cu.close()

    async def iter_items(self) -> AsyncIterator[tuple[bytes, bytes]]:
        cu = await self._execute(ITER_ITEMS)
        try:
            async for row in cu:
                yield row[0], row[1]
        finally:
            await cu.close()

    async def keys(self) -> list[bytes]:
        return [k async for k in self.iter_keys()]

    async def items(self) -> list[tuple[bytes, bytes]]:
        return [it async for it in self.iter_items()]

    async def close(self) -> None:
        if self._cx is not None:
            await self._cx.close()
            self._cx = None

    async def reorganize(self) -> None:
        cu = await self._execute(REORGANIZE)
        await cu.close()