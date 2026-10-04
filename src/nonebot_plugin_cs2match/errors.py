# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

class PandaScoreError(Exception):
    """PandaScore 接口调用失败。"""


class PandaScoreNotFound(PandaScoreError):
    """请求的比赛在 PandaScore 上确实不存在（HTTP 404）。

    这是"监视目标真的消失了"的唯一可信信号。
    """

    def __init__(self, url: str) -> None:
        super().__init__(f"HTTP 404 Not Found：{url}")
        self.url = url


class PandaScoreApiError(PandaScoreError):
    """非 2xx 响应，或响应结构不是预期的列表。"""

    def __init__(self, url: str, status: int | None, detail: str = "") -> None:
        self.url = url
        self.status = status
        self.detail = detail[:200]
        super().__init__(f"HTTP {status} {url}：{self.detail}")
