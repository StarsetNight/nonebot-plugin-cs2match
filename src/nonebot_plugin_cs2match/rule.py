# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

import nonebot

from .config import Config

config = nonebot.get_plugin_config(Config)


async def is_enabled():
    """
    :return: 插件是否被启用
    """
    return config.pandascore_token is not None

