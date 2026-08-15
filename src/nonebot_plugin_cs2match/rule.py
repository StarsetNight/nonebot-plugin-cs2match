# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from . import config


async def is_enabled():
    """
    :return: 插件是否被启用
    """
    return config.pandascore_token is not None

