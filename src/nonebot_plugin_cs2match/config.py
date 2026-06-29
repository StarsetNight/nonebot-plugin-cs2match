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
        ("xpl", 60, ["xpl"]),
        ("cac", 50, ["cac"]),
        ("pnl", 40, ["pnl"]),
    ]  # 赛事系列，优先级，匹配赛事名称（小写）
