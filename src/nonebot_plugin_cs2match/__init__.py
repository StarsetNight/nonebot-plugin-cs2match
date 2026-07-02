# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

from typing import cast

from nonebot import logger, get_driver, get_plugin_config, on_command, require
from nonebot.adapters.onebot.v11 import Message, GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config
from .tools import PandaScoreClient, MatchParser, typst_render
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
check_team = on_command("team", aliases={"查战队"}, rule=is_enabled, priority=10, block=True)
monitor_match = on_command("monitor", aliases={"监视"}, rule=is_enabled,
                           permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN, priority=10, block=True)
whitelist_config = on_command("cs2whitelist", aliases={"白名单"}, rule=is_enabled,
                           permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN, priority=10, block=True)


@get_help.handle()
async def on_get_help():
    await get_help.finish(await typst_render(help_text))


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

    func = func_map.get(arg, client.list_matches)
    matches = await func()
    _config = cast(DynamicConfigSystem, dynamic_config)

    await list_matches.finish(
        await typst_render(
            MatchParser.prerender_list(matches, _config.config.priority_mode)
        )
    )


@whitelist_config.handle()
async def on_whitelist_config(args: Message = CommandArg()):
    arg = args.extract_plain_text().strip().lower()
    if arg not in ["on", "off"]:
        await whitelist_config.finish("命令用法：whitelist <on/off>")
    _config = cast(DynamicConfigSystem, dynamic_config)
    _config.config.priority_mode = PriorityMode.WhitelistOnly if arg == "on" else PriorityMode.WhitelistFirst
    await whitelist_config.finish(f"仅白名单赛事模式被设置为{'开启' if arg == 'on' else '关闭'}。")


