# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from typing import Iterable
from asyncio import CancelledError, create_task, Task, to_thread, sleep, gather
from typing import Any, cast
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
from hashlib import blake2s
from time import time

import ayafileio
from aiohttp import ClientSession, ClientError, ClientTimeout
import typst

from nonebot import require, logger, get_bot

require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
from nonebot_plugin_alconna.uniseg import Image, UniMessage, Target
from nonebot_plugin_localstore import get_plugin_cache_dir

from . import template, config, driver
from .errors import PandaScoreApiError, PandaScoreNotFound
from .cache import func_ttl_cache, single_flight, RenderCache, CACHE_TTL, MAXSIZE

RENDER_CACHE_DIR = get_plugin_cache_dir() / "render_cache"
RENDER_CACHE_DIR.mkdir(exist_ok=True)
STABLE_RENDER_CACHE_DIR = RENDER_CACHE_DIR / "stable_cache"
STABLE_RENDER_CACHE_DIR.mkdir(exist_ok=True)

_KNOWN_STATUS = {"not_started", "running", "finished", "canceled", "postponed"}
_TERMINAL_STATUS = {"finished", "canceled"}

# 连续多少轮"确证比赛已不存在"后自动取消监视；列表里查不到不计入
MAX_MISSES = config.max_misses
MATCH_PAGE_SIZE = config.match_page_size

# 列表接口路径。CS2 数据归属 videogame id=3，/csgo/* 是官方给 CS 的专用入口
# （一页里全是 CS 比赛，而不是"全游戏第一页里恰好属于 CS 的几场"）。
# 若当前套餐不支持 /csgo/*（403/404），运行时自动回退到 _FALLBACK_MATCH_PATHS。
_CS_MATCH_PATHS = {
    "past": "/csgo/matches/past",
    "running": "/csgo/matches/running",
    "upcoming": "/csgo/matches/upcoming",
}
_FALLBACK_MATCH_PATHS = {
    "past": "/matches/past",
    "running": "/matches/running",
    "upcoming": "/matches/upcoming",
}

# 监视服务异常时向群聊广播的节流间隔（秒），避免每轮刷屏
FAILURE_NOTICE_INTERVAL = 1800.0

render_cache: RenderCache | None = None


@driver.on_startup
async def _():
    global render_cache
    render_cache = await RenderCache.create(RENDER_CACHE_DIR / "ttl_render_cache", RENDER_CACHE_DIR / "metadata.db")
    await render_cache.cleanup()

def _typst_str(value: Any) -> str:
    """把任意值转成安全的 typst 字符串字面量，防止 API 数据破坏模板。"""
    return json.dumps(str(value), ensure_ascii=False)


def _safe_status(status: str) -> str:
    """把比赛状态归一化到模板中已定义的状态标识符。"""
    return status if status in _KNOWN_STATUS else "unknown"


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

@single_flight
async def typst_render_for_stable_cache(typst_content: str, index_key: str) -> Image:
    """
    专门用来解决help这种不容易变动的缓存
    话说这名字也太长了，谁想的
    """
    key = blake2s(typst_content.encode("utf-8"))
    file_name = f"{index_key}_{key.hexdigest()}.png"
    cache_file_path = STABLE_RENDER_CACHE_DIR / file_name

    if cache_file_path.exists() and cache_file_path.is_file():
        f = ayafileio.open(cache_file_path, "rb")
        image_data = cast(bytes, await f.readall()) # 牛魔我都rb了哪来的str
        await f.close()
        logger.debug(f"{index_key} 缓存命中")
        return Image(raw=image_data)

    all_expired_cache_file = [
        p for p in STABLE_RENDER_CACHE_DIR.iterdir()
        if p.is_file() and p.name.startswith(f"{index_key}_")
    ]

    if len(all_expired_cache_file) > 0:
        logger.debug(f"清理失效缓存 {len(all_expired_cache_file)} 个")

    for i in all_expired_cache_file:
        i.unlink()

    image_data = await to_thread(_typst_render, typst_content)

    async with ayafileio.open(STABLE_RENDER_CACHE_DIR / file_name, "wb") as f:
        await f.write(image_data)

    logger.debug(f"{index_key} 缓存未命中")
    logger.debug(f"已创建 {STABLE_RENDER_CACHE_DIR / file_name} 缓存")

    return Image(raw=image_data)

@single_flight
async def typst_render(typst_content: str) -> Image:
    assert render_cache is not None

    cache_key = blake2s(typst_content.encode("utf-8")).digest()

    image_data = await render_cache.get_cache(cache_key)
    if image_data is None:
        image_data = await to_thread(_typst_render, typst_content)
        await render_cache.add_cache(cache_key, image_data)

    return Image(raw=image_data)

def _typst_render(typst_content: str) -> bytes:
    # 一般来说是不会输出多页的，所以干脆写个cast哄一下检查器了
    return cast(bytes, typst.compile(typst_content.encode(), format="png", ppi=144.0))


@dataclass
class MonitorTarget:
    """一个被监视的比赛。

    :param slug: 比赛 slug（小写，作为监视键）
    :param match_id: 比赛 ID；用于在列表窗口之外按 ID 直查，确证比赛是否还存在
    :param groups: 订阅该比赛的群场景ID集合
    """

    slug: str
    match_id: int | None = None
    groups: set[str] = field(default_factory=set)


class MonitorClient:
    def __init__(self, client: PandaScoreClient, self_id: str):
        self.client: PandaScoreClient = client
        self.self_id: str = self_id
        # slug -> 监视目标
        self.monitors: dict[str, MonitorTarget] = {}
        # slug -> 最近一次比赛数据
        self.matches: dict[str, dict[str, Any]] = {}
        # slug -> 连续“确证比赛不存在”的次数
        self.miss_count: dict[str, int] = {}
        # 上次广播"监视服务异常"的时间戳（节流用）
        self._last_failure_notice: float = 0.0
        self.task: Task[None] | None = None
        self.start()


    def add_monitor(self, slug: str, group_id: str, match_id: int | None = None) -> None:
        target = self.monitors.get(slug)
        if target is None:
            target = MonitorTarget(slug=slug, match_id=match_id)
            self.monitors[slug] = target
        elif match_id is not None:
            target.match_id = match_id
        target.groups.add(group_id)
        # 重新监视时清空历史计数，避免残留的 miss 让刚加上的监视立刻被取消
        self.miss_count.pop(slug, None)
        # 任务若已异常退出，这里顺带重启，避免"监视静默失效"
        self.start()


    def remove_monitor(self, group_id: str) -> None:
        # 快照迭代：本方法可能在监视轮询的 await 期间被调用
        for slug in list(self.monitors):
            target = self.monitors[slug]
            target.groups.discard(group_id)
            if not target.groups:
                del self.monitors[slug]
                self.matches.pop(slug, None)
                self.miss_count.pop(slug, None)


    def start(self) -> None:
        """确保监视循环处于运行状态（幂等）。"""
        task = self.task
        if task is not None and not task.done():
            return
        if task is not None:
            logger.warning("检测到比赛监视任务已停止，正在重新启动")
        self.task = create_task(self.monitor_loop())
        assert self.task is not None
        self.task.add_done_callback(self._on_task_done)


    @staticmethod
    def _on_task_done(task: Task[None]) -> None:
        """任务退出时记录原因：否则监视停止将完全静默。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            logger.error("比赛监视任务意外结束（协程未被取消，且未抛出异常信息），监视已停止工作")
        else:
            logger.opt(exception=exc).error("比赛监视任务异常退出，监视已停止工作")


    async def stop(self) -> None:
        """停止后台监视任务，供插件关闭时调用。"""
        task = self.task
        self.task = None
        if task is None:
            return
        if task.done():
            # 已结束：取回异常，避免 "Task exception was never retrieved"
            if not task.cancelled():
                task.exception()
            return
        task.cancel()
        try:
            await task
        except CancelledError:
            pass


    async def monitor_loop(self) -> None:
        logger.info(f"比赛监视服务已启动（Bot {self.self_id}）")
        while True:
            try:
                if self.monitors:
                    await self._run_round()
            except CancelledError:
                logger.info("比赛监视服务已停止")
                raise
            except Exception as e:
                # 兜底：任何异常都只记录+通知，绝不允许逃出循环（逃出去=监视静默死亡）
                logger.opt(exception=e).error("比赛监视服务异常")
                await self._notify_failure(e)
            await sleep(CACHE_TTL)


    async def _run_round(self) -> None:
        bot = self._get_bot()
        if bot is None:
            logger.warning(f"监视推送失败：找不到Bot实例（{self.self_id}）")
            return

        matches, failed = await self._collect_matches()
        if failed >= 3:
            logger.warning("监视轮询所有接口均失败，本轮跳过")
            return

        by_slug: dict[str, dict[str, Any]] = {}
        for match in matches:
            match_slug = match.get("slug")
            if isinstance(match_slug, str):
                # 同一场比赛可能同时出现在running/past：保持"首个命中优先"的原有语义
                by_slug.setdefault(match_slug.lower(), match)

        finished_slugs: list[str] = []

        # 快照迭代：轮询期间可能有 /monitor、/monitor cancel 并发修改容器
        for slug, target in list(self.monitors.items()):
            try:
                await self._check_target(bot, target, by_slug.get(slug), failed, finished_slugs)
            except CancelledError:
                raise
            except Exception as e:
                # 单个目标出问题不影响本轮其它目标
                logger.opt(exception=e).error(f"监视目标处理失败，本轮跳过：{slug}")

        # 循环结束后统一移除，避免迭代中修改字典
        for slug in finished_slugs:
            self.monitors.pop(slug, None)
            self.matches.pop(slug, None)
            self.miss_count.pop(slug, None)


    def _get_bot(self) -> Any | None:
        """取得该 self_id 对应的Bot实例，取不到返回 None。

        注意：nonebot.get_bot(self_id) 在"该 self_id 的Bot未连接"时抛的是 KeyError
        （而不是 ValueError）。只捕获 ValueError 会让 Bot 一掉线就炸穿整个监视循环。
        """
        try:
            return get_bot(self.self_id)
        except (ValueError, KeyError):
            return None


    async def _collect_matches(self) -> tuple[list[dict[str, Any]], int]:
        """并行拉取三个列表接口：单个接口失败不影响本轮其它比赛的检测。"""
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
                matches.extend(cast(Iterable[dict[str, Any]], r))
        return matches, failed


    async def _resolve_target(
        self,
        target: MonitorTarget,
        listed: dict[str, Any] | None,
        failed: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """定位被监视的比赛。

        列表接口只覆盖"当前一页窗口"，比赛掉出窗口并不代表它消失了；
        因此列表中查不到时，再用单场比赛接口按ID确证一次。

        :return: (比赛数据, 状态)
            - "found"：已取得比赛数据（列表命中或单场接口命中）
            - "missing"：确证比赛不存在（单场接口404）
            - "unknown"：无法确证（接口异常、缺少比赛ID或本轮列表不完整）
        """
        if listed is not None:
            return listed, "found"

        if failed > 0:
            # 本轮列表数据不完整，"找不到"不可信
            return None, "unknown"

        if target.match_id is None:
            logger.warning(f"监视目标不在列表窗口中且缺少比赛ID，无法确证：{target.slug}")
            return None, "unknown"

        try:
            return await self.client.get_match(str(target.match_id)), "found"
        except PandaScoreNotFound:
            return None, "missing"
        except CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"单场比赛查询失败，本轮不判定监视目标消失："
                f"{target.slug}：{type(e).__name__}: {e}"
            )
            return None, "unknown"


    async def _check_target(
        self,
        bot: Any,
        target: MonitorTarget,
        listed: dict[str, Any] | None,
        failed: int,
        finished_slugs: list[str],
    ) -> None:
        slug = target.slug
        current, state = await self._resolve_target(target, listed, failed)

        if state == "unknown":
            return

        if state == "missing":
            misses = self.miss_count.get(slug, 0) + 1
            self.miss_count[slug] = misses
            if misses < MAX_MISSES:
                logger.warning(f"监控目标已不存在：{slug}（第{misses}/{MAX_MISSES}次）")
                return
            logger.warning(f"监控目标已被删除，取消监视：{slug}")
            finished_slugs.append(slug)
            for group_id in list(target.groups):
                await self._notify_group(
                    bot, group_id,
                    ">监视目标已从数据中心删除，已自动取消监视<\n"
                    "比赛可能已被移除。",
                )
            return

        current = cast(dict[str, Any], current)
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
                    MatchParser.prerender_match(current, comment)
                )

                delivered = True
                for group_id in list(target.groups):
                    if not await self._notify_group(bot, group_id, message):
                        delivered = False
                if not delivered:
                    # 保留旧状态，下一轮重新检测并重试推送
                    logger.warning(f"监视推送未全部送达，保留旧状态以便重试：{slug}")
                    return
        except CancelledError:
            raise
        except Exception as e:
            logger.opt(exception=e).error(f"监视因 {e} 推送失败：{slug}")
            return

        if slug not in self.monitors:
            # 推送期间该比赛已被取消监视（/monitor cancel），不再记录状态
            return

        self.matches[slug] = current

        if current.get("status") in _TERMINAL_STATUS:
            logger.info(f"比赛已结束/取消，停止监控：{slug}")
            finished_slugs.append(slug)


    @staticmethod
    async def _notify_group(bot: Any, group_id: str, message: Any) -> bool:
        """向单个群推送消息。

        推送失败只记录日志，绝不允许推送异常影响监视循环本身。
        :return: 是否送达
        """
        try:
            await UniMessage(message).send(target=Target.group(group_id), bot=bot)
            return True
        except CancelledError:
            raise
        except Exception as e:
            logger.opt(exception=e).error(f"监视推送失败：{group_id}")
            return False


    async def _notify_failure(self, error: BaseException) -> None:
        """监视服务异常时向所有订阅群广播（节流，且内部完全兜底）。"""
        try:
            now = time()
            if now - self._last_failure_notice < FAILURE_NOTICE_INTERVAL:
                return
            self._last_failure_notice = now

            bot = self._get_bot()
            if bot is None:
                return

            for target in list(self.monitors.values()):
                for group_id in list(target.groups):
                    await self._notify_group(
                        bot, group_id,
                        ">比赛监视服务异常<\n"
                        f"错误：{type(error).__name__}\n"
                        f"详情请管理员查看日志，\n"
                        f"如再次看到此消息，请取消监视。"
                    )
        except CancelledError:
            raise
        except Exception as e:
            logger.opt(exception=e).error("发送监视异常通知时再次失败")



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
        # 套餐不支持 /csgo/* 时自动回退到全游戏接口（只回退一次）
        self.use_cs_endpoints = True

    async def close(self) -> None:
        """关闭底层 aiohttp 会话，释放连接池资源。"""
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """发起 GET 请求并检查状态码。

        注意：必须显式检查 HTTP 状态码。PandaScore 的错误响应体也是 JSON，
        直接 resp.json() 既不抛异常、也不是列表，会被上层误判成"接口成功但没有比赛"，
        进而把所有监视目标都算成"已消失"。
        """
        url = f"{self.base}{path}"
        try:
            async with self.session.get(url, headers=self.headers, params=params) as resp:
                if resp.status == 404:
                    raise PandaScoreNotFound(url)
                if resp.status >= 400:
                    raise PandaScoreApiError(url, resp.status, await resp.text())
                return await resp.json()
        except (ClientError, TimeoutError, asyncio.TimeoutError) as e:
            logger.warning(f"请求失败：{url}：{e}")
            raise

    async def _get_list(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """发起 GET 请求，并断言响应确实是列表。"""
        data = await self._get(path, params)
        if not isinstance(data, list):
            raise PandaScoreApiError(
                f"{self.base}{path}", 200,
                f"响应结构异常：期望 list，实际 {type(data).__name__}",
            )
        return [m for m in data if isinstance(m, dict)]

    async def _list_matches_by_kind(self, kind: str) -> list[dict[str, Any]]:
        """按 past/running/upcoming 拉取比赛列表。

        优先使用 CS 专用接口（一页里全是 CS 比赛，而不是"全游戏第一页里恰好属于CS的几场"）；
        若当前套餐不支持，则永久回退到全游戏接口并在本地按 videogame.id == 3 过滤。

        注意：监视的正确性不依赖这里的窗口大小——窗口之外的比赛由 MonitorClient
        按比赛ID直查兜底；这里只决定"窗口内能不能顺手看到"。
        """
        params = {"page[size]": MATCH_PAGE_SIZE}
        if self.use_cs_endpoints:
            try:
                return await self._get_list(_CS_MATCH_PATHS[kind], params)
            except PandaScoreApiError as e:
                if e.status not in (403, 404):
                    raise
                self.use_cs_endpoints = False
                logger.warning(
                    f"当前套餐不可用 {_CS_MATCH_PATHS[kind]}（HTTP {e.status}），"
                    f"已回退到全游戏比赛接口 {_FALLBACK_MATCH_PATHS[kind]}"
                )
        data = await self._get_list(_FALLBACK_MATCH_PATHS[kind], params)
        return [m for m in data if (m.get("videogame") or {}).get("id") == 3]

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

    @single_flight
    @func_ttl_cache(MAXSIZE)
    async def list_past_matches(self) -> list[dict[str, Any]]:
        return await self._list_matches_by_kind("past")

    @single_flight
    @func_ttl_cache(MAXSIZE)
    async def list_running_matches(self) -> list[dict[str, Any]]:
        return await self._list_matches_by_kind("running")

    @single_flight
    @func_ttl_cache(MAXSIZE)
    async def list_upcoming_matches(self) -> list[dict[str, Any]]:
        return await self._list_matches_by_kind("upcoming")

    @single_flight
    @func_ttl_cache(MAXSIZE)
    async def get_match(self, match_id: str) -> dict[str, Any]:
        return await self._get(f"/matches/{match_id}")

    @single_flight
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

    @single_flight
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