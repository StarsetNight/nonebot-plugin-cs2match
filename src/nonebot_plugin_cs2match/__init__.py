# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

from typing import cast

from nonebot import logger, get_driver, get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import Message, GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config
from .tools import PandaScoreClient, MatchParser, typst_render
from .typst_template import help_text
from .rule import is_enabled

driver = get_driver()
global_config = driver.config
config = get_plugin_config(Config)

panda_client: PandaScoreClient | None = None

# 注册插件
__plugin_meta__ = PluginMetadata(
    name="CS2赛事助手",
    description="实时追踪 Counter-Strike 2 职业赛事，开赛自动提醒、关键赛况与大比分异动推送",
    usage=help_text,
    config=Config,
    extra={}
)

@driver.on_startup
async def check_token():
    global panda_client
    if config.pandascore_token is None:
        logger.warning("警告！pandascore_token未配置")
        return
    panda_client = PandaScoreClient(config.pandascore_token)

get_help = on_command("cs2help", aliases={"cs2帮助"}, priority=10, block=True)
list_matches = on_command("matches", aliases={"比赛列表"}, rule=is_enabled, priority=10, block=True)
check_match = on_command("match", aliases={"比分"}, rule=is_enabled, priority=10, block=True)
check_team = on_command("team", aliases={"查战队"}, rule=is_enabled, priority=10, block=True)
monitor_match = on_command("monitor", aliases={"监视"}, rule=is_enabled,
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

    await list_matches.finish(
        await typst_render(
            await MatchParser.prerender_list(matches)
        )
    )

