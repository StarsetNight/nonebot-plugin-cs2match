# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from typing import Coroutine, Iterable
from typing import ParamSpec, TypeVar
from asyncio import CancelledError, create_task, Task, to_thread, sleep, gather
from functools import wraps
from typing import Any, cast, Callable
from datetime import datetime
from collections import defaultdict, OrderedDict
from binascii import crc32
from time import time

from aiohttp import ClientSession, ClientError, ClientTimeout
from ayafileio import open
import typst

from nonebot import require, logger, get_bot

require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
from nonebot_plugin_alconna.uniseg import Image, UniMessage, Target
from nonebot_plugin_localstore import get_plugin_cache_dir

from . import template, config

RENDER_CACHE_DIR = get_plugin_cache_dir() / "render_cache"
RENDER_CACHE_DIR.mkdir(exist_ok=True)

P = ParamSpec("P")
T = TypeVar("T")
AsyncFunc = Callable[P, Coroutine[Any, Any, T]]

_KNOWN_STATUS = {"not_started", "running", "finished", "canceled", "postponed"}
_TERMINAL_STATUS = {"finished", "canceled"}


def _typst_str(value: Any) -> str:
    """把任意值转成安全的 typst 字符串字面量，防止 API 数据破坏模板。"""
    return json.dumps(str(value), ensure_ascii=False)


def _safe_status(status: str) -> str:
    """把比赛状态归一化到模板中已定义的状态标识符。"""
    return status if status in _KNOWN_STATUS else "unknown"

def async_dedupe(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
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


def format_iso(iso: str) -> str:
    try:
        if not iso:
            return "时间未知"

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))

        # 自动读取系统时区
        local_tz = datetime.now().astimezone().tzinfo

        dt_local = dt.astimezone(local_tz)

        return dt_local.strftime("%m-%d %H:%M")

    except ValueError:
        return "时间未知"

@async_dedupe
async def typst_render(typst_content: str, cache_key: str) -> Image:
    cache_index = crc32(typst_content.encode("utf-8"))

    cache_file_path = RENDER_CACHE_DIR / f"{cache_key}_{cache_index:08X}.png"

    if cache_file_path.exists():
        cache_file = open(cache_file_path, "rb")
        cache_data = cast(bytes, await cache_file.readall())  # 我都二进制打开图像文件了怎么可能读出来str？
        await cache_file.close()
        return Image(raw=cache_data)

    # 清理死缓存
    for i in RENDER_CACHE_DIR.rglob(f"{cache_key}_*.png"):
        i.unlink()

    file_data = await to_thread(_typst_render, typst_content)
    cache_file = open(cache_file_path, "wb")
    await cache_file.write(file_data)
    await cache_file.close()

    return Image(raw=file_data)

def _typst_render(typst_content: str) -> bytes:
    # 一般来说是不会输出多页的，所以干脆写个cast哄一下检查器了
    return cast(bytes, typst.compile(typst_content.encode(), format="png", ppi=144.0))


class MonitorClient:
    def __init__(self, client: PandaScoreClient, self_id: str):
        self.client: PandaScoreClient = client
        self.self_id: str = self_id
        # slug -> 群场景ID集合
        self.monitors: dict[str, set[str]] = {}
        # slug -> 最近一次比赛数据
        self.matches: dict[str, dict[str, Any]] = {}
        # slug -> 连续轮询未发现次数
        self.miss_count: dict[str, int] = {}
        self.task: Task = create_task(self.monitor_loop())


    def add_monitor(self, slug: str, group_id: str):
        self.monitors.setdefault(slug, set()).add(group_id)


    def remove_monitor(self, group_id: str):
        for (_, groups) in self.monitors.items():
            groups.discard(group_id)

        self.monitors = {
            k: v for k, v in self.monitors.items() if v
        }


    async def stop(self) -> None:
        """停止后台监视任务，供插件关闭时调用。"""
        task = self.task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except CancelledError:
            pass


    async def monitor_loop(self):
        while True:
            try:
                if not self.monitors:
                    await sleep(CACHE_TTL)
                    continue

                try:
                    bot = get_bot(self.self_id)
                except ValueError:
                    logger.warning(f"监视推送失败：找不到Bot实例（{self.self_id}）")
                    await sleep(CACHE_TTL)
                    continue

                # 三个接口分别容错：单个接口失败不影响本轮其它比赛的检测
                results = await gather(
                    self.client.list_past_matches(),
                    self.client.list_running_matches(),
                    self.client.list_upcoming_matches(),
                    return_exceptions=True,
                )
                matches: list[dict[str, Any]] = []
                failed = 0
                for r in results:
                    if isinstance(r, BaseException):
                        failed += 1
                        logger.warning(f"监视轮询接口请求失败：{type(r).__name__}: {r}")
                    else:
                        # 有病吧？我都isinstance完了你静态检查器还在这执迷不悟
                        r = cast(Iterable[dict[str, Any]], r)
                        matches.extend(r)
                if failed == 3:
                    logger.warning("监视轮询所有接口均失败，本轮跳过")
                    await sleep(CACHE_TTL)
                    continue

                finished_slugs: list[str] = []

                for slug, groups in self.monitors.items():
                    current = next(
                        (m for m in matches if m.get("slug", "").lower() == slug),
                        None
                    )

                    if current is None:
                        # 可能处于"比赛结束但past接口尚未索引"的窗口期，也可能已被删除
                        if failed > 0:
                            # 本轮部分接口失败，数据不完整："目标消失"不可信，不计数
                            continue
                        misses = self.miss_count.get(slug, 0) + 1
                        self.miss_count[slug] = misses
                        if misses >= config.MAX_MISSES:
                            logger.warning(f"监控目标长时间消失，取消监视：{slug}")
                            finished_slugs.append(slug)
                            for group_id in groups:
                                try:
                                    await UniMessage(
                                        ">监控目标长时间未出现，已自动取消监视<\n"
                                        "比赛可能已被删除。"
                                    ).send(target=Target.group(group_id), bot=bot)
                                except Exception as e:
                                    logger.exception(f"取消监视通知因 {e} 发送失败：{slug}")
                        else:
                            logger.warning(f"监控目标消失：{slug}（第{misses}/{config.MAX_MISSES}次）")
                        continue

                    self.miss_count.pop(slug, None)

                    try:
                        old = self.matches.get(slug)

                        if self.should_notify(old, current):
                            logger.info(f"比赛发生变化：{slug}")

                            comment = (
                                "比赛已结束，自动监视已取消。"
                                if old is None
                                else template.push_comment
                            )

                            message = await typst_render(
                                MatchParser.prerender_match(current, comment),
                                "monitor"
                            )

                            for group_id in groups:
                                await UniMessage(message).send(
                                    target=Target.group(group_id), bot=bot
                                )
                    except Exception as e:
                        logger.exception(f"监视因 {e} 推送失败：{slug}")
                        continue

                    self.matches[slug] = current

                    if current.get("status") in _TERMINAL_STATUS:
                        logger.info(f"比赛已结束/取消，停止监控：{slug}")
                        finished_slugs.append(slug)

                # 循环结束后统一移除，避免迭代中修改字典
                for slug in finished_slugs:
                    self.monitors.pop(slug, None)
                    self.matches.pop(slug, None)
                    self.miss_count.pop(slug, None)

            except CancelledError:
                logger.info("比赛监视服务已停止")
                raise
            except Exception as e:
                try:
                    bot = get_bot(self.self_id)
                except ValueError:
                    bot = None
                for groups in self.monitors.values():
                    logger.exception("比赛监视服务异常")
                    for group_id in groups:
                        if bot is not None:
                            await UniMessage(
                                f">比赛监视服务异常<\n"
                                f"错误：{type(e).__name__}\n"
                                f"详情请管理员查看日志，\n"
                                f"如再次看到此消息，请取消监视。"
                            ).send(target=Target.group(group_id), bot=bot)
            await sleep(CACHE_TTL)



    @staticmethod
    def should_notify(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
        """是否需要推送：首次见到终态（确保比赛结束不静默消失），或状态/比分/地图发生变化。"""
        if old is None:
            return new.get("status") in _TERMINAL_STATUS
        return MonitorClient.has_changed(old, new)



    @staticmethod
    def has_changed(old: dict, new: dict) -> bool:
        return any(
            old.get(key) != new.get(key)
            for key in ("status", "results", "games")
        )


def find_match(
    matches: list[dict[str, Any]],
    query: str,
    *,
    slug_case_sensitive: bool = True,
) -> tuple[dict[str, Any] | None, str, int]:
    """在比赛列表中定位比赛。

    优先按 slug 精确定位（沿用各调用方原有的大小写语义）；未命中时后备为
    按战队名查找：将双方队名与查询串做大小写不敏感的整名精确匹配。

    :param matches: 比赛列表（如 PandaScoreClient.list_matches 的结果）
    :param query: 用户输入的查询串（建议已去除首尾空白）
    :param slug_case_sensitive: slug 匹配是否区分大小写
        （on_check_match 沿用 True，on_monitor_match 沿用 False）
    :return: (match, matched_by, team_hit_count)
        - match: 命中的比赛；未命中为 None
        - matched_by: "slug" | "team" | ""（未命中）
        - team_hit_count: 队名命中的比赛总数（仅队名后备命中时才有意义；
          大于 1 时调用方应提示"匹配到多个，取第一个"）
    """
    # 1) slug 精确定位，沿用原有语义
    if slug_case_sensitive:
        hit = next((m for m in matches if m.get("slug") == query), None)
    else:
        hit = next((m for m in matches if m.get("slug", "").lower() == query), None)

    if hit is not None:
        return hit, "slug", 0

    # 2) 队名后备：大小写不敏感的整名精确匹配，命中多个时取列表第一个
    query_lower = query.lower()
    team_hits = [
        m for m in matches
        if any(
            (o.get("opponent") or {}).get("name", "").lower() == query_lower
            for o in m.get("opponents") or []
        )
    ]

    if team_hits:
        return team_hits[0], "team", len(team_hits)

    return None, "", 0


class MatchParser:
    @staticmethod
    def parse(match: dict[str, Any]) -> dict[str, Any]:
        # 基础信息
        serie = (match.get("serie") or {}).get("full_name", "Unknown Match")
        slug = match.get("slug", "unknown")
        match_time = match.get("scheduled_at") or match.get("begin_at") or "unknown time"
        status = _safe_status(match.get("status", "unknown"))

        # 队伍
        opponents = match.get("opponents", [])
        if len(opponents) >= 2:
            team_a = opponents[0].get("opponent", {}).get("name", "TBD")
            team_b = opponents[1].get("opponent", {}).get("name", "TBD")
        else:
            team_a = "TBD"
            team_b = "TBD"

        # 比分（bo match）
        score_map: dict[int, int] = {}
        for r in match.get("results") or []:
            tid = r.get("team_id")
            if tid is not None:
                score_map[tid] = r.get("score", 0)

        # 按顺序映射
        score_a = 0
        score_b = 0

        if len(opponents) >= 2:
            a_id = opponents[0].get("opponent", {}).get("id")
            b_id = opponents[1].get("opponent", {}).get("id")

            score_a = score_map.get(a_id, 0) if a_id is not None else 0
            score_b = score_map.get(b_id, 0) if b_id is not None else 0

        return {
            "serie": serie,
            "slug": slug,
            "time": format_iso(match_time),
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score_a,
            "score_b": score_b,
            "status": status,
        }

    @staticmethod
    def team_names(match: dict[str, Any]) -> tuple[str, str]:
        """提取对阵双方队名（缺少对手信息时对应位置返回"未知"）。"""
        opponents = match.get("opponents") or []

        team_a = (
            opponents[0]
            .get("opponent", {})
            .get("name", "未知")
            if len(opponents) >= 2
            else "未知"
        )

        team_b = (
            opponents[1]
            .get("opponent", {})
            .get("name", "未知")
            if len(opponents) >= 2
            else "未知"
        )

        return team_a, team_b

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

        content = template.list_match

        for serie_name, serie_matches in sorted_series.items():
            content += f'#series_card({_typst_str(serie_name)}, [\n'

            serie_matches.sort(key=lambda x: x.get("scheduled_at") or "")

            for match in serie_matches:
                match_json = cls.parse(match)

                content += (
                    f'#match_card('
                    f'{_typst_str(match_json["slug"])},'
                    f'{_typst_str(match_json["time"])},'
                    f'{_typst_str(match_json["team_a"])},'
                    f'{match_json["score_a"]},'
                    f'{match_json["score_b"]},'
                    f'{_typst_str(match_json["team_b"])},'
                    f'{match_json["status"]}'
                    f')\n'
                )

            content += '])\n\n'

        return content

    @classmethod
    def prerender_match(cls, match: dict[str, Any], comment: str = "") -> str:
        opponents = match.get("opponents") or []

        team_a, team_b = cls.team_names(match)

        # 如果只能获取到一边选手的信息，那还有什么意义呢？
        if len(opponents) >= 2:
            a_id = opponents[0].get("opponent", {}).get("id")
            b_id = opponents[1].get("opponent", {}).get("id")
        else:
            a_id = b_id = None

        # 与 MatchParser.parse 保持一致：按 team_id 映射比分，
        # 不依赖 results 数组与 opponents 数组的索引顺序
        score_map: dict[int, int] = {}
        for r in match.get("results") or []:
            tid = r.get("team_id")
            if tid is not None:
                score_map[tid] = r.get("score") or 0

        if a_id is not None and b_id is not None:
            a_id = cast(int, a_id)
            b_id = cast(int, b_id)
            score_a = score_map.get(a_id, 0)
            score_b = score_map.get(b_id, 0)
        else:
            score_a = score_b = 0

        games = []

        for game in match.get("games") or []:
            winner_id = (
                    game.get("winner") or {}
            ).get("id")

            if winner_id == a_id:
                winner = team_a

            elif winner_id == b_id:
                winner = team_b

            else:
                winner = "未知"

            games.append(
                f"""
                    (
                        position: {int(game.get("position") or 0)},
                        winner: {_typst_str(winner)},
                        status: {_typst_str(_safe_status(game.get("status", "unknown")))},
                    ),
                    """
            )

        games_text = "\n".join(games)

        return f"""{template.get_match}
        #let match = (
            name: {_typst_str(match.get("name", "未知比赛"))},
            league: {_typst_str((match.get("league") or {}).get("name", "未知赛事"))},
            serie: {_typst_str((match.get("serie") or {}).get("full_name", "未知系列"))},
            team_a: {_typst_str(team_a)},
            team_b: {_typst_str(team_b)},
            score_a: {score_a},
            score_b: {score_b},
            status: {_typst_str(_safe_status(match.get("status", "unknown")))},
            time: {_typst_str(format_iso(match.get("scheduled_at") or match.get("begin_at") or "未知时间"))},
            bo: {int(match.get("number_of_games") or 0)},
            games: (
                {games_text}
            ),
        )
        
        {comment}

        #match_detail(match)
        """

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
        self.session = ClientSession(timeout=ClientTimeout(total=config.client_timeout))

    async def close(self) -> None:
        """关闭底层 aiohttp 会话，释放连接池资源。"""
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _get(self, path, params=None) -> Any:
        url = f"{self.base}{path}"
        try:
            async with self.session.get(url, headers=self.headers, params=params) as resp:
                return await resp.json()
        except (ClientError, TimeoutError, asyncio.TimeoutError) as e:
            logger.warning(f"请求失败：{url}：{e}")
            raise

    async def list_matches(self) -> list[dict[str, Any]]:
        """
        注意，这个函数调用消耗3次API调用额度，并且会存储3份不同类型的比赛列表缓存。
        """
        past, running, upcoming = await gather(
            self.list_past_matches(),
            self.list_running_matches(),
            self.list_upcoming_matches(),
        )
        return past + running + upcoming

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_past_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/past")
            if isinstance(m, dict) and m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_running_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/running")
            if isinstance(m, dict) and m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def list_upcoming_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/upcoming")
            if isinstance(m, dict) and m.get("videogame", {}).get("id") == 3
        ]

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def get_match(self, match_id: str) -> dict[str, Any]:
        return await self._get(f"/matches/{match_id}")

    @async_dedupe
    @func_ttl_cache(MAXSIZE)
    async def get_match_score(self, match_id: str) -> dict[str, int] | None:
        match = await self.get_match(match_id)

        results = match.get("results") or []
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