# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

import nonebot

global_config = nonebot.get_driver().config


async def is_enabled():
    """
    :return: 插件是否被启用
    """
    return nonebot.get_plugin("nonebot_plugin_cs2match").metadata.extra["Enabled"]

