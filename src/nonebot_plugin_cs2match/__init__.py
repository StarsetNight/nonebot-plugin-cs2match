# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from typing import Any, cast

from arclet.alconna import Alconna, Args, AllParam
from nonebot import logger, get_driver, get_plugin_config, require
from nonebot.permission import SUPERUSER, Permission
from nonebot.plugin import PluginMetadata

from .config import Config
# init的配置读取必须先于其他要使用init中配置的导入模块，否则会缺配置读不了
driver = get_driver()
global_config = driver.config
config = get_plugin_config(Config)  # 取自config.py中的静态配置
from .tools import PandaScoreClient, MonitorClient, MatchParser, typst_render, find_match
from .template import help_plain_text, help_text
from .rule import is_enabled
from .dynamic_config import DynamicConfigSystem, PriorityMode

require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")
from nonebot_plugin_alconna import Query, on_alconna, UniMessage
from nonebot_plugin_localstore import get_plugin_data_file
from nonebot_plugin_uninfo import Uninfo, ADMIN, SceneType

panda_client: PandaScoreClient | None = None
dynamic_config: DynamicConfigSystem | None = None  # 取自插件内编写的DynamicConfigSystem
monitor_client: MonitorClient | None = None

# 注册插件
__plugin_meta__ = PluginMetadata(
    name="CS2赛事助手",
    description="实时追踪 Counter-Strike 2 职业赛事，开赛自动提醒、关键赛况与大比分异动推送",
    usage=help_plain_text,
    type="application",
    homepage="https://github.com/StarsetNight/nonebot-plugin-cs2match",
    config=Config,
    supported_adapters={"~onebot.v11", "~qq"},
    extra={"author": "StarsetNight <starsetnight@outlook.com>"}
)

@driver.on_startup
async def on_startup_check():
    global panda_client, dynamic_config
    if config.pandascore_token is None:
        logger.warning("pandascore_token未设置，CS2数据查询功能将不可用或受限。")
        logger.info("请前往PandaScore官网注册获取Token，并在插件目录config.py中配置："
                    "pandascore_token: str | None = <你的Token>")
        return
    panda_client = PandaScoreClient(config.pandascore_token)

    config_path = get_plugin_data_file("config.json")
    if not config_path.exists():
        dynamic_config = await DynamicConfigSystem.new(config_path)
    else:
        dynamic_config = await DynamicConfigSystem.from_path(config_path)


@driver.on_shutdown
async def on_shutdown_cleanup():
    """关闭插件持有的连接与后台任务。"""
    global monitor_client, panda_client
    try:
        if monitor_client is not None:
            await monitor_client.stop()
            monitor_client = None
        if panda_client is not None:
            await panda_client.close()
            panda_client = None
    except Exception as e:
        logger.exception(f"插件资源清理失败：{e}")


get_help = on_alconna(
    Alconna("cs2help"),
    aliases=("cs2帮助",),
    priority=10, block=True,
)
list_matches = on_alconna(
    Alconna("matches", Args["mode?", str]),
    aliases=("比赛列表",),
    rule=is_enabled,
    priority=10, block=True,
)
check_match = on_alconna(
    Alconna("match", Args["slug", AllParam]),
    aliases=("比分",),
    rule=is_enabled,
    priority=10, block=True,
)
monitor_match = on_alconna(
    Alconna("monitor", Args["slug", AllParam]),
    aliases=("监视",),
    rule=is_enabled,
    permission=SUPERUSER | ADMIN(),
    priority=10, block=True,
)
whitelist_config = on_alconna(
    Alconna("cs2whitelist", Args["state", str]),
    aliases=("白名单",),
    rule=is_enabled,
    permission=SUPERUSER | ADMIN(),
    priority=10, block=True,
)
get_my_id = on_alconna(
    Alconna("cs2uid"),
    aliases=("我的id",),
    priority=10, block=True,
)


@get_help.handle()
async def on_get_help():
    await get_help.finish(await typst_render(help_text, "help"))


@list_matches.handle()
async def on_list_matches(mode: Query[str] = Query("mode", default="")):
    arg = mode.result.strip()

    client = cast(PandaScoreClient, panda_client)

    func_map = {
        "past": client.list_past_matches,
        "running": client.list_running_matches,
        "upcoming": client.list_upcoming_matches,
    }

    await list_matches.send("正在查询比赛列表，请稍候...")

    func = func_map.get(arg, None)

    if func is None:
        func = client.list_matches
        cache_key = "list_matches"
    else:
        cache_key = arg

    try:
        matches = await func()
    except Exception as e:
        await list_matches.finish(f"由于API调度故障，请求失败：{e}")
        matches = []  # 哄类型检查器
    _config = cast(DynamicConfigSystem, dynamic_config)

    await list_matches.finish(
        await typst_render(
            MatchParser.prerender_list(matches, _config.config.priority_mode),
            cache_key
        )
    )


@check_match.handle()
async def on_check_match(slug: UniMessage):
    slug = slug.extract_plain_text().strip()

    if not slug:
        await check_match.finish("用法：match <slug>\n"
                                 "slug可在查询比赛列表的单个比赛左下角中找到。")

    client = cast(PandaScoreClient, panda_client)

    await check_match.send(f"正在查询比赛({slug})\n请稍候...")

    try:
        matches = await client.list_matches()
    except Exception as e:
        await check_match.finish(f"由于API调度故障，请求失败：{e}")
        matches = []  # 哄类型检查器

    match, matched_by, team_hits = find_match(matches, slug)

    if match is None:
        await check_match.finish(f"未找到比赛：{slug}")

    match = cast(dict[str, Any], match)

    if matched_by == "team" and team_hits > 1:
        team_a, team_b = MatchParser.team_names(match)
        await check_match.send(
            f"⚠️ 未找到 slug「{slug}」，按战队名匹配到 {team_hits} 场比赛，"
            f"此处展示第一个：{team_a} vs {team_b}"
        )

    await check_match.finish(
        await typst_render(
            MatchParser.prerender_match(match),
            "get_match",
        )
    )


@monitor_match.handle()
async def on_monitor_match(session: Uninfo, slug: UniMessage):
    global monitor_client

    slug = slug.extract_plain_text().strip().lower()

    if not slug:
        await monitor_match.finish(
            "用法：monitor <slug>\n"
            "取消监视：monitor cancel"
        )

    if session.scene.type != SceneType.GROUP:
        await monitor_match.finish("该命令仅限群聊使用。")

    client = cast(PandaScoreClient, panda_client)  # 获取API的HTTP客户端

    if monitor_client is None:
        monitor_client = MonitorClient(client=client, self_id=session.self_id)
    assert monitor_client is not None

    # 取消当前群比赛监视
    if slug == "cancel":
        monitor_client.remove_monitor(group_id=session.scene.id)
        await monitor_match.finish("已取消本群比赛监视。")

    try:
        matches = await client.list_matches()
    except Exception as e:
        await monitor_match.finish(f"由于API调度故障，请求失败：{e}")
        matches = []  # 哄类型检查器

    match, matched_by, team_hits = find_match(matches, slug, slug_case_sensitive=False)

    if match is None:
        await monitor_match.finish(f"未找到比赛：{slug}")
    match = cast(dict[str, Any], match)

    if match.get("status", "unknown") in {"finished", "canceled"}:
        await monitor_match.finish(f"比赛已结束/取消，不可监视：{slug}")

    # 统一以比赛真实 slug 作为监视键：队名后备命中时尤其关键，
    # 否则监视轮询按 slug 查找将永远找不到目标
    monitor_slug = str(match.get("slug", slug)).lower()

    if matched_by == "team" and team_hits > 1:
        team_a, team_b = MatchParser.team_names(match)
        await monitor_match.send(
            f"⚠️ 未找到 slug「{slug}」，按战队名匹配到 {team_hits} 场比赛，"
            f"将监视第一个：{team_a} vs {team_b}（slug: {monitor_slug}）"
        )

    monitor_client.add_monitor(monitor_slug, session.scene.id)
    await monitor_match.finish(f"已开始监视比赛：{match.get('name', monitor_slug)}")


@whitelist_config.handle()
async def on_whitelist_config(state: Query[str] = Query("state", default="")):
    arg = state.result.strip().lower()
    if arg not in ["on", "off"]:
        await whitelist_config.finish("命令用法：whitelist <on/off>")
    _config = cast(DynamicConfigSystem, dynamic_config)
    _config.config.priority_mode = PriorityMode.WhitelistOnly if arg == "on" else PriorityMode.WhitelistFirst
    await _config.save()
    await whitelist_config.finish(f"仅白名单赛事模式被设置为{'开启' if arg == 'on' else '关闭'}。")


@get_my_id.handle()
async def on_get_my_id(session: Uninfo):
    await get_my_id.finish(
        f"你的用户ID：{session.user.id}\n"
        f"当前场景ID：{session.scene.id}\n"
    )

