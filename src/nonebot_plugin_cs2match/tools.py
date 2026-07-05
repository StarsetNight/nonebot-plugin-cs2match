# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT
from typing import Coroutine
from typing import ParamSpec, TypeVar
from asyncio import create_task, Task
from functools import wraps
from asyncio import to_thread
from typing import Any, cast, Callable
from datetime import datetime, timezone, timedelta
from collections import defaultdict, OrderedDict
from binascii import crc32
from time import time

from aiohttp import ClientSession
from ayafileio import open
import typst

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot import require, get_driver, get_plugin_config, logger

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_plugin_cache_dir

from . import typst_template
from .config import Config

driver = get_driver()
global_config = driver.config
config = get_plugin_config(Config)

RENDER_CACHE_DIR = get_plugin_cache_dir() / "render_cache"
RENDER_CACHE_DIR.mkdir(exist_ok=True)

P = ParamSpec("P")
T = TypeVar("T")
AsyncFunc = Callable[P, Coroutine[Any, Any, T]]

def async_dedupe(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
    tasks: dict[int, Task[T]] = {}
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        nonlocal tasks
        key = hash((args, tuple(sorted(kwargs.items()))))
        print(tasks)
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


def format_iso(iso: str) -> str:
    try:
        if not iso:
            return "时间未知"

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))

        dt_sh = dt.astimezone(
            timezone(timedelta(hours=8))
        )

        return dt_sh.strftime("%m-%d %H:%M")

    except ValueError:
        return "时间未知"

@async_dedupe
async def typst_render(typst_content: str, cache_key: str) -> MessageSegment:
    cache_index = crc32(typst_content.encode("utf-8"))

    cache_file_path = RENDER_CACHE_DIR / f"{cache_key}_{cache_index:08X}.png"

    if cache_file_path.exists():
        cache_file = open(cache_file_path, "rb")
        cache_data = await cache_file.readall()
        await cache_file.close()
        return MessageSegment.image(cache_data)

    # 清理死缓存
    for i in RENDER_CACHE_DIR.rglob(f"{cache_key}_*.png"):
        i.unlink()

    file_data = await to_thread(_typst_render, typst_content)
    cache_file = open(cache_file_path, "wb")
    await cache_file.write(file_data)
    await cache_file.close()

    return MessageSegment.image(file_data)

def _typst_render(typst_content: str) -> bytes:
    # 一般来说是不会输出多页的，所以干脆写个cast哄一下检查器了
    # pyrefly: ignore [redundant-cast]
    return cast(bytes, typst.compile(typst_content.encode(), format="png", ppi=144.0))

class MatchParser:
    @staticmethod
    def parse(match: dict[str, Any]) -> dict[str, Any]:
        # 基础信息
        serie = match.get("serie", {}).get("full_name", "Unknown Match")
        slug = match.get("slug", "unknown")
        time = match.get("scheduled_at") or match.get("begin_at") or "unknown time"
        status = match.get("status", "unknown")

        # 队伍
        opponents = match.get("opponents", [])
        if len(opponents) >= 2:
            team_a = opponents[0]["opponent"]["name"]
            team_b = opponents[1]["opponent"]["name"]
        else:
            team_a = "TBD"
            team_b = "TBD"

        # 比分（bo match）
        score_map: dict[int, int] = {}
        for r in match.get("results", []):
            tid = r.get("team_id")
            score_map[tid] = r.get("score", 0)

        # 按顺序映射
        score_a = 0
        score_b = 0

        if len(opponents) >= 2:
            a_id = opponents[0]["opponent"]["id"]
            b_id = opponents[1]["opponent"]["id"]

            score_a = score_map.get(a_id)
            score_b = score_map.get(b_id)

        return {
            "serie": serie,
            "slug": slug,
            "time": format_iso(time),
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score_a,
            "score_b": score_b,
            "status": status,
        }

    @classmethod
    def prerender_list(cls, matches: list[dict[str, Any]], priority_mode: str) -> str:
        series = defaultdict(list)

        for match in matches:
            serie = (match.get("serie") or {}).get("full_name", "未知赛事")
            if (
                priority_mode == "whitelist-only"
                and cls.serie_priority(serie) == 0
            ):
                continue
            series[serie].append(match)

        sorted_series = dict(sorted(
            series.items(),
            key=lambda s: cls.serie_priority(s[0]),
            reverse=True
        ))

        content = typst_template.list_match

        for serie_name, serie_matches in sorted_series.items():
            content += f'#series_card("{serie_name}", [\n'

            serie_matches.sort(key=lambda x: x["scheduled_at"])

            for match in serie_matches:
                match_json = cls.parse(match)

                content += (
                    f'#match_card('
                    f'"{match_json["slug"]}",'
                    f'"{match_json["time"]}",'
                    f'"{match_json["team_a"]}",'
                    f'{match_json["score_a"]},'
                    f'{match_json["score_b"]},'
                    f'"{match_json["team_b"]}",'
                    f'{match_json["status"]}'
                    f')\n'
                )

            content += '])\n\n'

        return content

    @classmethod
    def classify_serie(cls, name: str) -> str:
        if not name:
            return "other"

        n = name.lower()

        for key, _, keywords in config.serie_rules:
            if any(k in n for k in keywords):
                return key

        return "other"

    @classmethod
    def serie_priority(cls, name: str) -> int:
        key = cls.classify_serie(name)

        for k, priority, _ in config.serie_rules:
            if k == key:
                return priority

        return 0




class PandaScoreClient:
    def __init__(self, token: str) -> None:
        self.base = "https://api.pandascore.co"
        self.headers = {
            "Authorization": f"Bearer {token}"
        }
        self.session: ClientSession | None = None

    async def _get(self, path, params=None) -> Any:
        if not self.session:
            self.session = ClientSession()
        url = f"{self.base}{path}"
        async with self.session.get(url, headers=self.headers, params=params) as resp:
            return await resp.json()

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches")
            if m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_past_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/past")
            if m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_running_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/running")
            if m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_upcoming_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/upcoming")
            if m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def get_match(self, match_id: str) -> dict[str, Any]:
        return await self._get(f"/matches/{match_id}")

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def get_match_score(self, match_id: str) -> dict[str, int] | None:
        match = await self.get_match(match_id)

        results = match.get("results", [])
        opponents = match.get("opponents", [])

        if len(opponents) < 2:
            return None

        team_map = {
            opponents[0]["opponent"]["id"]: opponents[0]["opponent"]["name"],
            opponents[1]["opponent"]["id"]: opponents[1]["opponent"]["name"]
        }

        score = {}
        for r in results:
            score[team_map.get(r["team_id"], str(r["team_id"]))] = r["score"]

        return score

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def get_teams(self, match_id: str) -> list[dict[str, Any]]:
        match = await self.get_match(match_id)

        opponents = match.get("opponents", [])

        teams = []
        for o in opponents:
            t = o["opponent"]
            teams.append({
                "id": t["id"],
                "name": t["name"],
                "acronym": t.get("acronym"),
                "country": t.get("location"),
                "image": t.get("image_url")
            })

        return teams