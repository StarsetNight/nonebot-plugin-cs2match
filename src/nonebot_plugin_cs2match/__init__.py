# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from typing import cast, Any
from asyncio import create_task

from nonebot import logger, get_driver, get_plugin_config, on_command, require
from nonebot.adapters.onebot.v11 import Message, GROUP_ADMIN, GROUP_OWNER, Bot, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config
from .tools import PandaScoreClient, MonitorClient, MatchParser, typst_render
from .typst_template import help_text
from .rule import is_enabled
from .dynamic_config import DynamicConfigSystem, PriorityMode

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_plugin_data_file

driver = get_driver()
global_config = driver.config
config = get_plugin_config(Config)  # 取自config.py中的静态配置

panda_client: PandaScoreClient | None = None
dynamic_config: DynamicConfigSystem | None = None  # 取自插件内编写的DynamicConfigSystem
monitor_client: MonitorClient | None = None

# 注册插件
__plugin_meta__ = PluginMetadata(
    name="CS2赛事助手",
    description="实时追踪 Counter-Strike 2 职业赛事，开赛自动提醒、关键赛况与大比分异动推送",
    usage=help_text,
    config=Config,
    extra={}
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


get_help = on_command("cs2help", aliases={"cs2帮助"}, priority=10, block=True)
list_matches = on_command("matches", aliases={"比赛列表"}, rule=is_enabled, priority=10, block=True)
check_match = on_command("match", aliases={"比分"}, rule=is_enabled, priority=10, block=True)
monitor_match = on_command("monitor", aliases={"监视"}, rule=is_enabled,
                           permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN, priority=10, block=True)
whitelist_config = on_command("cs2whitelist", aliases={"白名单"}, rule=is_enabled,
                           permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN, priority=10, block=True)


@get_help.handle()
async def on_get_help():
    await get_help.finish(await typst_render(help_text, "help"))


@list_matches.handle()
async def on_list_matches(args: Message = CommandArg()):
    arg = args.extract_plain_text().strip()

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

    matches = await func()
    _config = cast(DynamicConfigSystem, dynamic_config)

    await list_matches.finish(
        await typst_render(
            MatchParser.prerender_list(matches, _config.config.priority_mode),
            cache_key
        )
    )


@check_match.handle()
async def on_check_match(args: Message = CommandArg()):
    slug = args.extract_plain_text().strip()

    if not slug:
        await check_match.finish("用法：match <slug>\n"
                                 "slug可在查询比赛列表的单个比赛左下角中找到。")

    client = cast(PandaScoreClient, panda_client)

    await check_match.send(f"正在查询比赛({slug})\n请稍候...")

    matches = (
        await client.list_running_matches()
        + await client.list_upcoming_matches()
        + await client.list_past_matches()
    )

    match = next(
        (m for m in matches if m.get("slug") == slug),
        None,
    )

    if match is None:
        await check_match.finish(f"未找到比赛：{slug}")

    match = cast(dict[str, Any], match)

    await check_match.finish(
        await typst_render(
            MatchParser.prerender_match(match),
            "get_match",
        )
    )


@monitor_match.handle()
async def on_monitor_match(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    global monitor_client

    slug = args.extract_plain_text().strip().lower()

    if not slug:
        await monitor_match.finish(
            "用法：monitor <slug>\n"
            "取消监听：monitor cancel"
        )

    if monitor_client is None:
        client = cast(
            PandaScoreClient,
            panda_client,
        )

        monitor_client = MonitorClient(
            client=client,
            bot=bot,
        )
        assert monitor_client is not None


    # 取消当前群监听
    if slug == "cancel":

        remove_slug = None

        for s, groups in monitor_client.monitors.items():
            if event.group_id in groups:
                groups.remove(event.group_id)

                if not groups:
                    remove_slug = s

                break

        if remove_slug:
            monitor_client.monitors.pop(remove_slug, None)
            monitor_client.matches.pop(remove_slug, None)


        await monitor_match.finish("已取消本群比赛监听。")

    client = cast(
        PandaScoreClient,
        panda_client,
    )

    matches = (
        await client.list_past_matches()
        + await client.list_running_matches()
        + await client.list_upcoming_matches()
    )

    match = next(
        (m for m in matches if m.get("slug", "").lower() == slug),
        None,
    )

    if match is None:
        await monitor_match.finish(f"未找到比赛：{slug}")

    match = cast(dict[str, Any], match)


    monitor_client.add_monitor(slug,event.group_id,)


    # 初始化快照
    monitor_client.matches.setdefault(slug,match,)


    if monitor_client.task is None:
        monitor_client.task = create_task(
            monitor_client.monitor_loop()
        )


    await monitor_match.finish(
        f"已开始监控比赛：{match.get('name', slug)}"
    )


@whitelist_config.handle()
async def on_whitelist_config(args: Message = CommandArg()):
    arg = args.extract_plain_text().strip().lower()
    if arg not in ["on", "off"]:
        await whitelist_config.finish("命令用法：whitelist <on/off>")
    _config = cast(DynamicConfigSystem, dynamic_config)
    _config.config.priority_mode = PriorityMode.WhitelistOnly if arg == "on" else PriorityMode.WhitelistFirst
    await _config.save()
    await whitelist_config.finish(f"仅白名单赛事模式被设置为{'开启' if arg == 'on' else '关闭'}。")


