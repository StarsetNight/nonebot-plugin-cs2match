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
    ]  # 赛事系列，优先级，匹配赛事名称（小写）
    client_timeout: int = 10
    cache_ttl: float = 60
    cache_max_size: int = 64
    match_page_size: int = 100
    # 连续多少轮"确证比赛已不存在"（单场比赛接口返回404）后自动取消监视。
    # 列表接口查不到并不代表比赛消失（分页窗口会滑动），因此只有404才计数。
    max_misses: int = 3
