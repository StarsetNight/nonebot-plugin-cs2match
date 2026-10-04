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
    waiters: dict[int, int] = {}
    func_name = getattr(func, "__qualname__", func.__name__)

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        nonlocal tasks
        key = hash((args, tuple(sorted(kwargs.items()))))

        if key in tasks:
            waiters[key] = waiters.get(key, 1) + 1
            logger.debug(f"单飞命中 | 函数={func_name} 键={key} 在途任务数={len(tasks)} 合并计数={waiters[key]}")
            return await tasks[key]

        task = create_task(func(*args, **kwargs))
        tasks[key] = task
        waiters[key] = 1
        logger.debug(f"单飞未命中 | 函数={func_name} 键={key} 在途任务数={len(tasks)} 合并计数=1")

        try:
            result = await task
            return result
        finally:
            merged = waiters.pop(key, 1)
            tasks.pop(key, None)
            logger.debug(f"单飞完成 | 函数={func_name} 键={key} 在途任务数={len(tasks)} 合并计数={merged}")

    return wrapper

CACHE_TTL = config.cache_ttl
MAXSIZE = config.cache_max_size

def func_ttl_cache(maxsize: int) -> Callable[[AsyncFunc[P, T]], AsyncFunc[P, T]]:
    def _func_ttl_cache(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
        cache: OrderedDict[int, tuple[float, Any]] = OrderedDict()
        maxsize_ = maxsize
        func_name = getattr(func, "__qualname__", func.__name__)

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            nonlocal cache
            key = hash((args, tuple(sorted(kwargs.items()))))
            now = time()

            if key in cache:
                expire_at, data = cache[key]
                if expire_at > now:
                    logger.debug(
                        f"缓存命中 | 函数={func_name} 键={key} "
                        f"当前数量={len(cache)} 剩余有效期={expire_at - now:.2f}秒"
                    )
                    cache.move_to_end(key)
                    return data
                logger.debug(f"缓存过期 | 函数={func_name} 键={key} 当前数量={len(cache)}")
                del cache[key]
            else:
                logger.debug(f"缓存未命中 | 函数={func_name} 键={key} 当前数量={len(cache)}")

            start = time()
            data = await func(*args, **kwargs)
            cost = time() - start

            cache[key] = (time() + CACHE_TTL, data)
            logger.debug(
                f"缓存写入 | 函数={func_name} 键={key} "
                f"当前数量={len(cache)} 回源耗时={cost:.3f}秒 "
                f"有效期={CACHE_TTL}秒"
            )

            while len(cache) > maxsize_:
                evicted_key, _ = cache.popitem(last=False)
                logger.debug(
                    f"缓存淘汰 | 函数={func_name} 被淘汰键={evicted_key} "
                    f"当前数量={len(cache)} 上限={maxsize_}"
                )

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

    def _delete_file(self, name: str) -> int:
        path = self.cache_path / name
        size = 0
        if path.exists() and path.is_file():
            size = path.stat().st_size
            path.unlink()
        return size

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
            logger.debug(f"{key.hex()} 缓存未命中")
            return None

        new_expire = _get_time() + (config.render_cache_renewal_duration * 60)

        cache_data_path = self.cache_path / f"{key.hex().lower()}.png"
        if not cache_data_path.exists() or not cache_data_path.is_file():
            logger.warning(f"{key.hex()} 缓存读取中出现异常，对应数据不存在")
            await self.metadata_db.delete(key)
            return None

        async with ayafileio.open(cache_data_path, "rb") as f:
            cache_data = cast(bytes, await f.readall())

        await self.metadata_db.set(key, _dump_time(new_expire))

        logger.debug(f"{key.hex()} 缓存命中")

        return cache_data

    async def add_cache(self, key: bytes, data: bytes):
        assert self.metadata_db is not None

        cache_file_path = self.cache_path / f"{key.hex().lower()}.png"

        async with ayafileio.open(cache_file_path, "wb") as f:
            await f.write(data)

        expire = _get_time() + (config.render_cache_renewal_duration * 60)

        logger.debug(f"{key.hex()} 加入缓存，数据大小：{format_bytes(len(data))}")

        await self.metadata_db.set(key, _dump_time(expire))

    async def cleanup(self):
        assert self.metadata_db is not None

        logger.debug(f"开始缓存清理")

        if self.clearing:
            return
        self.clearing = True

        try:
            await self._cleanup()
        finally:
            self.clearing = False
            logger.debug(f"缓存清理完成")
        return

    async def _cleanup(self):
        assert self.metadata_db is not None

        all_cache_file = [
            p for p in self.cache_path.iterdir()
            if p.is_file() and p.suffix == '.png'
        ]

        all_cache_metadata = await self.metadata_db.items()
        all_key = {i[0].hex().lower() for i in all_cache_metadata}

        # 找出不在元信息数据库里的缓存文件
        missing_metadata_files = [
            p for p in all_cache_file
            if p.stem.lower() not in all_key
        ]

        if len(missing_metadata_files) > 0:
            logger.debug(f"元数据缺失的缓存数量: {len(missing_metadata_files)}")

        # 删除不在元信息数据库里的缓存文件
        data_length = 0
        for i in missing_metadata_files:
            data_length += self._delete_file(i.name)

        if len(missing_metadata_files) > 0:
            logger.debug(f"删除元数据缺失的缓存大小: {format_bytes(data_length)}")

        # 清理过期的
        data_length = 0
        expired_count = 0
        for k, v in all_cache_metadata:
            expire = _load_time(v)
            if _get_time() > expire:
                await self.metadata_db.delete(k)
                data_length += self._delete_file(f"{k.hex().lower()}.png")
                expired_count += 1

        if expired_count > 0:
            logger.debug(f"过期缓存数量: {expired_count}")
            logger.debug(f"清理缓存大小: {format_bytes(data_length)}")


def format_bytes(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(num_bytes)
    unit_index = 0

    while unit_index < len(units) - 1 and value / 1024 >= 1:
        value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    else:
        return f"{value:.2f} {units[unit_index]}"

