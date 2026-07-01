# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

from pydantic import BaseModel


class Config(BaseModel):
    """Plugin Config Here"""
    pandascore_token: str | None = None
    serie_rules: list[tuple[str, int, list[str]]] = [
        ("major", 100, ["major"]),
        ("blast", 90, ["blast"]),
        ("iem", 80, ["iem"]),
        ("esl", 80, ["esl"]),
        ("pgl", 70, ["pgl"]),
        ("cac", 60, ["cac"]),
        ("pnl", 50, ["pnl"]),
    ]  # 赛事系列，优先级，匹配赛事名称（小写）
    priority_mode: str = "whitelist_only"  # whitelist_only 仅渲染serie_rules | whitelist_first serie_rules优先渲染
    # TODO priority_mode想要支持运行时修改并可覆写，应该得另建数据存储
