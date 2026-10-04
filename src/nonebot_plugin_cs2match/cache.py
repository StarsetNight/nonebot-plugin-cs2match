# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import ParamSpec, TypeVar, Coroutine, Any, Callable, cast
from asyncio import create_task, Task
from functools import wraps
from collections import OrderedDict
from time import time
from pathlib import Path

import ayafileio

from nonebot import logger

from . import config
from .db import KvDB

P = ParamSpec("P")
T = TypeVar("T")
AsyncFunc = Callable[P, Coroutine[Any, Any, T]]

def single_flight(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
    tasks: dict[int, Task[T]] = {}
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        nonlocal tasks
        key = hash((args, tuple(sorted(kwargs.items()))))
        if key in tasks:
            return await tasks[key]
        task = create_task(func(*args, **kwargs))
        tasks[key] = task
        try:
            result = await task
            return result
        finally:
            tasks.pop(key, None)
    return wrapper

CACHE_TTL = config.cache_ttl
MAXSIZE = config.cache_max_size

def func_ttl_cache(maxsize: int) -> Callable[[AsyncFunc[P, T]], AsyncFunc[P, T]]:
    def _func_ttl_cache(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
        cache: OrderedDict[int, tuple[float, Any]] = OrderedDict()
        maxsize_ = maxsize

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            nonlocal cache
            key = hash((args, tuple(sorted(kwargs.items()))))
            now = time()

            if key in cache:
                ttl, data = cache[key]
                if ttl > now:
                    cache.move_to_end(key)
                    return data
                del cache[key]

            data = await func(*args, **kwargs)
            cache[key] = (now + CACHE_TTL, data)

            while len(cache) > maxsize_:
                cache.popitem(last=False)

            return data

        return wrapper

    return _func_ttl_cache

def _get_time() -> int:
    return int(time())

def _dump_time(ts: int) -> bytes:
    return ts.to_bytes(8, "big", signed=False)

def _load_time(b: bytes) -> int:
    return int.from_bytes(b, "big", signed=False)

class RenderCache:
    def __init__(self, cache_path: Path, cache_metadata_db: Path):
        self.clearing = False
        self.cleanup_task = None
        self.next_cleanup_time = _get_time() + (config.render_cache_cleanup_interval * 60)

        cache_path.mkdir(exist_ok=True, parents=True)
        self.cache_path = cache_path

        self.metadata_db_path = cache_metadata_db
        self.metadata_db: KvDB | None = None

    @classmethod
    async def create(cls, cache_path: Path, cache_metadata_db: Path) -> RenderCache:
        self = cls(cache_path, cache_metadata_db)
        self.metadata_db = await KvDB.open(self.metadata_db_path)
        return self

    def _delete_file(self, name: str):
        path = self.cache_path / name
        if path.exists() and path.is_file():
            path.unlink()

    @staticmethod
    def _on_cleanup_done(task: Task[None]) -> None:
        """取回后台清理任务的异常：否则失败会静默，只等 GC 时由 asyncio 报一条无上下文的日志。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"渲染缓存清理失败，本轮跳过，下个周期重试：{type(exc).__name__}: {exc}")

    async def get_cache(self, key: bytes) -> None | bytes:
        assert self.metadata_db is not None

        if _get_time() > self.next_cleanup_time:
            self.next_cleanup_time = _get_time() + config.render_cache_cleanup_interval * 60
            self.cleanup_task = create_task(self.cleanup())
            self.cleanup_task.add_done_callback(self._on_cleanup_done)

        res = await self.metadata_db.contains(key)
        if not res:
            return None

        new_expire = _get_time() + (config.render_cache_renewal_duration * 60)

        cache_data_path = self.cache_path / f"{key.hex().lower()}.jpg"
        if not cache_data_path.exists() or not cache_data_path.is_file():
            await self.metadata_db.delete(key)
            return None

        async with ayafileio.open(cache_data_path, "rb") as f:
            cache_data = cast(bytes, await f.readall())

        await self.metadata_db.set(key, _dump_time(new_expire))

        return cache_data

    async def add_cache(self, key: bytes, data: bytes):
        assert self.metadata_db is not None

        cache_file_path = self.cache_path / f"{key.hex().lower()}.jpg"

        async with ayafileio.open(cache_file_path, "wb") as f:
            await f.write(data)

        expire = _get_time() + (config.render_cache_renewal_duration * 60)
        await self.metadata_db.set(key, _dump_time(expire))

    async def cleanup(self):
        assert self.metadata_db is not None

        if self.clearing:
            return
        self.clearing = True

        try:
            await self._cleanup()
        finally:
            self.clearing = False
        return

    async def _cleanup(self):
        assert self.metadata_db is not None

        all_cache_file = [
            p for p in self.cache_path.iterdir()
            if p.is_file() and p.suffix == '.jpg'
        ]

        all_cache_metadata = await self.metadata_db.items()
        all_key = {i[0].hex().lower() for i in all_cache_metadata}

        # 找出不在元信息数据库里的缓存文件
        missing_metadata_files = [
            p for p in all_cache_file
            if p.stem.lower() not in all_key
        ]

        # 删除不在元信息数据库里的缓存文件
        for i in missing_metadata_files:
            self._delete_file(i.name)

        # 清理过期的
        for k, v in all_cache_metadata:
            expire = _load_time(v)
            if _get_time() > expire:
                await self.metadata_db.delete(k)
                self._delete_file(f"{k.hex().lower()}.jpg")