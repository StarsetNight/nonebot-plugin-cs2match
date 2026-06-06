# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

import os
import pillowmd
from dotenv import load_dotenv
from io import BytesIO

from nonebot import logger
from nonebot import get_driver, get_plugin_config
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config
from .tools import PandaScoreClient, MatchParser
from .help import help_text
from .rule import is_enabled

driver = get_driver()
global_config = driver.config
config = get_plugin_config(Config)

load_dotenv()

style = pillowmd.MdStyle()

if PANDASCORE_TOKEN := os.getenv("PANDASCORE_TOKEN"):
    panda_client = PandaScoreClient(PANDASCORE_TOKEN)
    available = True
else:
    logger.warning("未检测到环境变量 PANDASCORE_TOKEN，CS2 数据查询功能将不可用或受限。")
    logger.info("请前往 PandaScore 官网注册获取 Token，并在项目根目录 .env 文件中配置："
                "PANDASCORE_TOKEN=<你的Token>")
    available = False


# 注册插件
__plugin_meta__ = PluginMetadata(
    name="CS2赛事助手",
    description="实时追踪 Counter-Strike 2 职业赛事，开赛自动提醒、关键赛况与大比分异动推送",
    usage=help_text,
    config=Config,
    extra={
        "Enabled": available,
    },
)

get_help = on_command("cs2help", aliases={"cs2帮助"}, priority=10, block=True)
list_matches = on_command("matches", aliases={"比赛列表"}, rule=is_enabled, priority=10, block=True)


@get_help.handle()
async def on_get_help():
    io = BytesIO()
    (await style.AioRender(help_text, "CS2 帮助")).image.save(io, format="png")
    await get_help.finish(MessageSegment.image(io))


@list_matches.handle()
async def on_list_matches(args: Message = CommandArg()):
    arg = args.extract_plain_text().strip()

    func_map = {
        "past": panda_client.list_past_matches,
        "running": panda_client.list_running_matches,
        "upcoming": panda_client.list_upcoming_matches,
    }

    await list_matches.send(f"正在查询比赛列表，请稍候...")

    func = func_map.get(arg, panda_client.list_matches)
    matches = await func()
    match_content = ""
    for match in reversed(matches):
        match_json = await MatchParser.parse(match)
        match_content += (f"### {match_json['name']}\n\n"
                          f"{match_json['slug']}"
                          f"**时间:** `{match_json['time']}`  \n"
                          f"**比分:** {match_json['team_a']} `{match_json['score_a']} - {match_json['score_b']}` {match_json['team_b']}  \n"
                          f"**状态:** {match_json['status']}\n"
                          "------\n")
    io = BytesIO()
    (await style.AioRender(match_content, "CS2 比赛列表")).image.save(io, format="png")
    await list_matches.finish(MessageSegment.image(io))

