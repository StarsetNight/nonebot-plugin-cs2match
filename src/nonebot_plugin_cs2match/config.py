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
    render_cache_cleanup_interval: int = 60  # 清理的间隔，单位分钟
    render_cache_renewal_duration: int = 30  # 缓存命中后的续期时长，单位分钟

    # /matches 每张图片最多渲染多少场比赛。
    # QQ 群聊图片是"内联 base64 上传"，图片过大会被平台拒绝（40093011：上传文件大小超过限制）；
    # 实测约 13.6KiB/场（ppi=144），20 场约 273KiB，留有充足余量。
    matches_per_image: int = 20
    # /matches 最多发几张图。QQ 规定"每个消息最多回复 5 次"，
    # 现有一条"正在查询比赛列表，请稍候..."已占 1 次，故默认 3 张（合计 4 条，留 1 条余量）。
    matches_max_images: int = 3