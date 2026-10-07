# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

"""QQ 开放平台「指令面板」同步：把写死的命令帮助通过 API 写进 QQ 客户端。

QQ 客户端里展示命令帮助的可编程入口是开放平台的指令面板接口：

- ``POST   /v2/panels``            创建面板（10 QPM，一个机器人最多 20 个）
- ``PUT    /v2/panels/{panel_id}`` 修改面板内容（10 QPM）
- ``GET    /v2/panels``            分页查询面板（30 QPM）

``nonebot-adapter-qq`` 没有实现这几个接口（``Adapter._call_api`` 只能转发
Bot 类上已有的方法），因此这里直接复用适配器已经做好鉴权与 token 续期的
HTTP 层：

- ``bot.get_authorization_header()`` 提供 ``Authorization: QQBot {token}``；
- ``bot.adapter.get_api_base()`` 提供正式/沙箱地址；
- ``bot.adapter.request(Request(...))`` 发出请求。

面板内容由本模块的 ``COMMAND_SPECS`` 写死，机器人每次连接后自动同步一次
（按 remark 幂等 upsert），**不**提供任何运行时修改入口：面板只能是这里
定义的样子。若接口域名不支持 ``/v2/panels``（404），把适配器已有的
``QQ_API_BASE`` 指向 ``https://api.bot.qq.com`` 即可。

为了让插件在只装 OneBot 的环境里也能正常加载，本模块**不**在导入期引用
``nonebot.adapters.qq``，QQ 判断走 ``bot.adapter.get_name() == "QQ"``。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nonebot import logger
from nonebot.drivers import URL, Request

from .errors import QQPanelApiError

if TYPE_CHECKING:
    from nonebot.adapters import Bot

# 面板备注：仅开发者可见，用来识别「这个面板是本插件创建的」，
# 从而让重复同步走 PUT 而不会堆积出重复面板。发布后不要修改。
PANEL_REMARK = "cs2match-cmd-panel"

# 需要维护面板的场景，写死为单聊 + 群聊；channel / dm 面板本项目不建。
PANEL_SCOPES: tuple[str, ...] = ("c2c", "group")

# 官方限制：name ≤ 14 字符（约 7 个中文汉字），desc ≤ 30 字符（约 15 个中文汉字）。
# 这里按「非 ASCII 记 2 单位」保守计算，宁可短一点也不要被接口打回。
MAX_NAME_UNITS = 14
MAX_DESC_UNITS = 30

# 面板接口限频 10 QPM；「指令面板操作进行中」退避后重试一次
_RETRYABLE_ERR_CODES = {40030009}
_RETRY_DELAY_SECONDS = 2.0

_LIST_PAGE_LIMIT = 50
_CREATE = "POST"
_UPDATE = "PUT"
_LIST = "GET"


@dataclass(frozen=True)
class CommandSpec:
    """一条面板元素（对应插件的一个命令）。"""

    name: str
    desc: str
    admin_only: bool = False
    scopes: tuple[str, ...] = PANEL_SCOPES


# 面板内容写死在这里：name 是点选后填入输入框的文本，必须与 alconna 的实际
# 可匹配文本一致。本项目 command_start 为空、alconna_use_command_start 默认
# False，即命令是「无前缀」匹配的，因此这里的 name 也不带 "/"。
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(name="cs2help", desc="查看插件全部命令用法"),
    CommandSpec(name="比赛列表", desc="查看比赛列表及筛选参数"),
    CommandSpec(name="比分", desc="输入队名或slug查询大比分"),
    # monitor 仅群聊可用，且需要群管/SUPERUSER
    CommandSpec(
        name="监视",
        desc="群内监视比赛，管理员可用",
        admin_only=True,
        scopes=("group",),
    ),
    CommandSpec(
        name="白名单",
        desc="切换仅白名单赛事模式",
        admin_only=True,
        scopes=("group",),
    ),
    CommandSpec(name="我的id", desc="查看用户ID与当前场景ID"),
)


def is_qq_bot(bot: "Bot") -> bool:
    """判断 Bot 是否来自 QQ 官方机器人适配器（鸭子类型，不 import 适配器包）。"""
    adapter = getattr(bot, "adapter", None)
    get_name = getattr(adapter, "get_name", None)
    if not callable(get_name):
        return False
    try:
        return get_name() == "QQ"
    except Exception:
        return False


def _units(text: str) -> int:
    """按「非 ASCII 记 2 单位」估算面板元素长度。"""
    return sum(1 if ch.isascii() else 2 for ch in text)


def fit_text(text: str, limit: int) -> str:
    """把文本裁剪到长度预算内，裁剪时记一条 warning 方便发现文案写超了。"""
    if _units(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for ch in text:
        unit = 1 if ch.isascii() else 2
        if used + unit > limit:
            break
        kept.append(ch)
        used += unit
    fitted = "".join(kept)
    logger.warning(f"指令面板文案超出 {limit} 单位，已裁剪：{text!r} -> {fitted!r}")
    return fitted


def build_panel_items(scope: str) -> list[dict[str, Any]]:
    """按场景生成面板元素列表（纯函数，便于离线测试）。"""
    items: list[dict[str, Any]] = []
    for spec in COMMAND_SPECS:
        if scope not in spec.scopes:
            continue
        items.append(
            {
                "type": "command",
                "name": fit_text(spec.name, MAX_NAME_UNITS),
                "desc": fit_text(spec.desc, MAX_DESC_UNITS),
                "only_admin": bool(spec.admin_only),
            }
        )
    return items


def find_panel_id(records: list[dict[str, Any]]) -> str | None:
    """在面板列表里找出本插件创建的 panel_id（按 remark 识别），没有则 None。"""
    for record in records:
        if not isinstance(record, dict):
            continue
        panel = record.get("panel")
        if isinstance(panel, dict) and panel.get("remark") == PANEL_REMARK:
            panel_id = record.get("panel_id")
            return str(panel_id) if panel_id else None
    return None


def _loads(content: Any) -> Any:
    if content is None or content == "":
        return None
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return None


class QQPanelClient:
    """指令面板接口客户端，复用适配器的鉴权与 HTTP 层。"""

    def __init__(self, bot: "Bot") -> None:
        self.bot = bot

    @property
    def api_base(self) -> URL:
        return self.bot.adapter.get_api_base()

    def _url(self, *paths: str) -> URL:
        return self.api_base.joinpath(*paths)

    async def _request(
        self,
        method: str,
        *paths: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        request = Request(method, self._url(*paths), params=params, json=json_body)
        request.headers.update(await self.bot.get_authorization_header())

        try:
            response = await self.bot.adapter.request(request)
        except Exception as exc:  # 网络层异常（NetworkError 等）
            raise QQPanelApiError(
                url=str(request.url),
                status=None,
                err_code=None,
                message=f"请求失败：{exc}",
            ) from exc

        data = _loads(response.content)
        trace_id = ""
        if isinstance(data, dict):
            trace_id = str(data.get("trace_id") or "")
        if not trace_id:
            trace_id = str(response.headers.get("X-Tps-trace-ID") or "")

        err_code = data.get("err_code") if isinstance(data, dict) else None
        if not (200 <= int(response.status_code) < 300) or err_code not in (None, 0):
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or "")
            raise QQPanelApiError(
                url=str(request.url),
                status=int(response.status_code),
                err_code=int(err_code) if isinstance(err_code, int) else None,
                message=message,
                trace_id=trace_id,
            )
        return data

    async def _request_with_retry(
        self,
        method: str,
        *paths: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        try:
            return await self._request(
                method, *paths, params=params, json_body=json_body
            )
        except QQPanelApiError as exc:
            if exc.err_code not in _RETRYABLE_ERR_CODES:
                raise
            logger.warning(
                f"指令面板操作进行中（err_code={exc.err_code}），"
                f"{_RETRY_DELAY_SECONDS:g}s 后重试：{exc}"
            )
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            return await self._request(
                method, *paths, params=params, json_body=json_body
            )

    async def list_panels(self, scope: str) -> list[dict[str, Any]]:
        """分页拉取指定场景的全部面板记录。"""
        records: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"scope": scope, "limit": _LIST_PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = await self._request(_LIST, "v2", "panels", params=params)
            if not isinstance(data, dict):
                logger.warning(f"指令面板列表响应结构异常（scope={scope}）：{data!r}")
                break
            page = data.get("records")
            if isinstance(page, list):
                records.extend(item for item in page if isinstance(item, dict))
            next_cursor = str(data.get("next_cursor") or "")
            if data.get("is_end") or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return records

    async def sync_scope(self, scope: str) -> dict[str, Any]:
        """创建或更新（upsert）指定场景的面板。"""
        panel_id = find_panel_id(await self.list_panels(scope))
        body = {
            "panel": {
                "items": build_panel_items(scope),
                "remark": PANEL_REMARK,
            }
        }

        if panel_id:
            data = await self._request_with_retry(
                _UPDATE, "v2", "panels", panel_id, json_body=body
            )
            version = data.get("version") if isinstance(data, dict) else None
            return {
                "scope": scope,
                "panel_id": panel_id,
                "version": version,
                "created": False,
            }

        payload = dict(body, scope=scope, target_type="all")
        data = await self._request_with_retry(_CREATE, "v2", "panels", json_body=payload)
        new_id = data.get("panel_id") if isinstance(data, dict) else None
        if not new_id:
            raise QQPanelApiError(
                url=str(self._url("v2", "panels")),
                status=200,
                err_code=None,
                message=f"创建指令面板成功但未返回 panel_id：{data!r}",
            )
        return {
            "scope": scope,
            "panel_id": str(new_id),
            "version": data.get("version") if isinstance(data, dict) else None,
            "created": True,
        }


async def sync_all(bot: "Bot", *, client: "QQPanelClient | None" = None) -> None:
    """把写死的命令帮助同步到全部场景的面板。

    单个场景失败不影响其他场景，也不会向上抛异常——面板同步失败绝不能影响
    机器人的正常运行，日志里能看到 err_code 与 trace_id 即可。
    """
    client = client or QQPanelClient(bot)

    for scope in PANEL_SCOPES:
        try:
            result = await client.sync_scope(scope)
        except QQPanelApiError as exc:
            logger.warning(f"同步QQ指令面板失败（scope={scope}）：{exc}")
        except Exception as exc:  # noqa: BLE001 - 同步失败绝不影响机器人运行
            logger.exception(f"同步QQ指令面板异常（scope={scope}）：{exc}")
        else:
            action = "创建" if result.get("created") else "更新"
            logger.info(
                f"QQ指令面板已{action}"
                f"（scope={scope}，panel_id={result.get('panel_id')}）"
            )
