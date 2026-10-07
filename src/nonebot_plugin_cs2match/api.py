# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from asyncio import CancelledError, gather
from typing import Any

from aiohttp import ClientSession, ClientError, ClientTimeout

from nonebot import get_plugin_config, logger
from nonebot.drivers import Request

from .cache import func_ttl_cache, single_flight, MAXSIZE
from .config import Config
from .errors import PandaScoreApiError, PandaScoreNotFound

config = get_plugin_config(Config)  # 取自 config.py 中的静态配置

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


async def check_proactive_msg_permission(bot: Any, group_id: str) -> bool | None:
    """查询机器人在该群是否被允许主动推送消息（仅 QQ 官方机器人）。

    走官方 ``GET /v2/groups/{group_openid}/bot_state`` 的 ``allow_proactive_msg``
    字段（是否接收主动推送）。该接口目前仅白名单机器人可用，因此任何拿不到结果的
    失败（11253 无接口权限、网络异常、非 QQ 适配器、响应结构异常）都返回 ``None``
    表示"无法判定"，由调用方按原有行为继续——绝不能因为查不了就拒绝用户。

    ``nonebot-adapter-qq`` 没有实现这个接口，所以这里直接复用适配器已经做好鉴权与
    token 续期的 HTTP 层（``bot.adapter.get_api_base()`` / ``get_authorization_header``
    / ``adapter.request``），与 ``panel.py`` 的做法一致。

    :return: True 允许主动推送；False 明确不允许；None 无法判定
    """
    get_name = getattr(getattr(bot, "adapter", None), "get_name", None)
    if not callable(get_name) or get_name() != "QQ":
        # 非 QQ 适配器（如 OneBot）没有这个概念，也无需多发一次请求
        return None

    try:
        # get_api_base() 同样只存在于 QQ 适配器上，放进 try 里一起兜底，
        # 保证这个函数在任何失败下都只是"无法判定"。
        request = Request(
            "GET",
            bot.adapter.get_api_base() / f"v2/groups/{group_id}/bot_state",
        )
        request.headers.update(await bot.get_authorization_header())
        response = await bot.adapter.request(request)
    except CancelledError:
        raise
    except Exception as e:
        logger.warning(
            f"主动消息权限查询失败（{group_id}），按无法判定处理："
            f"{type(e).__name__}: {e}"
        )
        return None

    data: Any = None
    if response.content:
        try:
            data = json.loads(response.content)
        except ValueError:
            data = None

    err_code = data.get("err_code") if isinstance(data, dict) else None
    if response.status_code != 200 or err_code:
        # 11253（该接口仅白名单机器人可用）属于预期情况，不该刷 ERROR
        logger.info(
            f"主动消息权限查询不可用（{group_id}）："
            f"HTTP {response.status_code} err_code={err_code}"
        )
        return None

    allowed = data.get("allow_proactive_msg") if isinstance(data, dict) else None
    if isinstance(allowed, bool):
        return allowed

    logger.info(f"主动消息权限查询返回了无法识别的结构（{group_id}）：{data!r}")
    return None


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

    @func_ttl_cache(MAXSIZE)
    @single_flight
    async def list_past_matches(self) -> list[dict[str, Any]]:
        return await self._list_matches_by_kind("past")

    @func_ttl_cache(MAXSIZE)
    @single_flight
    async def list_running_matches(self) -> list[dict[str, Any]]:
        return await self._list_matches_by_kind("running")

    @func_ttl_cache(MAXSIZE)
    @single_flight
    async def list_upcoming_matches(self) -> list[dict[str, Any]]:
        return await self._list_matches_by_kind("upcoming")

    @func_ttl_cache(MAXSIZE)
    @single_flight
    async def get_match(self, match_id: str) -> dict[str, Any]:
        return await self._get(f"/matches/{match_id}")

    @func_ttl_cache(MAXSIZE)
    @single_flight
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

    @func_ttl_cache(MAXSIZE)
    @single_flight
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
