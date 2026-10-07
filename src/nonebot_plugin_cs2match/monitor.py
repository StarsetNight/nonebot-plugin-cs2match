# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, sleep, gather
from dataclasses import dataclass, field
from time import time
from typing import TYPE_CHECKING, Any, Iterable, cast

from nonebot import require, get_plugin_config, get_bot, logger

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna.uniseg import Target, UniMessage

from . import template
from .cache import CACHE_TTL
from .config import Config
from .errors import PandaScoreNotFound
from .parser import TERMINAL_STATUS, MatchParser
from .render import typst_render

if TYPE_CHECKING:
    # 只为类型标注：运行时不引入 api（aiohttp）
    from .api import PandaScoreClient

config = get_plugin_config(Config)  # 取自 config.py 中的静态配置

# 连续多少轮"确证比赛已不存在"后自动取消监视；列表里查不到不计入
MAX_MISSES = config.max_misses


# 监视服务异常时向群聊广播的节流间隔（秒），避免每轮刷屏
FAILURE_NOTICE_INTERVAL = 1800.0


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

                # 主动消息发送失败（无权限、网络不可达、登录失效等）是意外错误：
                # 只记 ERROR 并跳过该群，**不**回滚状态——否则下一轮会重新检测到
                # 同一个变化并无限重发
                for group_id in list(target.groups):
                    try:
                        await self._notify_group(bot, group_id, message)
                    except CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - 单个群失败不得影响其他群
                        logger.opt(exception=e).error(
                            f"主动消息推送异常，跳过该群：{group_id}"
                        )
        except CancelledError:
            raise
        except Exception as e:
            logger.opt(exception=e).error(f"监视因 {e} 推送失败：{slug}")
            return

        if slug not in self.monitors:
            # 推送期间该比赛已被取消监视（/monitor cancel），不再记录状态
            return

        self.matches[slug] = current

        if current.get("status") in TERMINAL_STATUS:
            logger.info(f"比赛已结束/取消，停止监控：{slug}")
            finished_slugs.append(slug)


    @staticmethod
    def _describe_send_error(error: BaseException) -> str:
        """提取 QQ ActionFailed 的 code/message/trace_id，便于定位（如 40034105 无权限）。"""
        parts = [
            f"{name}={value}"
            for name in ("code", "message", "trace_id")
            if (value := getattr(error, name, None))
        ]
        return f"（{', '.join(parts)}）" if parts else ""

    @staticmethod
    async def _notify_group(bot: Any, group_id: str, message: Any) -> bool:
        """向单个群推送主动消息。

        发送失败（无权限、网络不可达、登录失效等）属于意外错误：只记 ERROR 并
        跳过该群，不重试、不影响其他群，也不回滚监视状态。
        :return: 是否送达
        """
        try:
            await UniMessage(message).send(target=Target.group(group_id), bot=bot)
            return True
        except CancelledError:
            raise
        except Exception as e:
            logger.opt(exception=e).error(
                f"主动消息推送失败，跳过该群：{group_id}"
                f"{MonitorClient._describe_send_error(e)}"
            )
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
            return new.get("status") in TERMINAL_STATUS
        return MonitorClient.has_changed(old, new)


    @staticmethod
    def has_changed(old: dict, new: dict) -> bool:
        return any(
            old.get(key) != new.get(key)
            for key in ("status", "results", "games")
        )
