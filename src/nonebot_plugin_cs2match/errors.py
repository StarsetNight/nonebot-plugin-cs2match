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


class QQPanelError(Exception):
    """QQ 开放平台「指令面板」接口调用失败。"""


class QQPanelApiError(QQPanelError):
    """指令面板接口返回了错误：非 2xx 响应，或响应体里 err_code 非 0。

    err_code 取官方文档中的错误码（如 40030006 面板不存在、40030013 超出数量
    限制、11253/11254 未获得接口权限或被封禁），message 与 trace_id 用于排查。
    """

    def __init__(
        self,
        url: str,
        status: int | None = None,
        err_code: int | None = None,
        message: str = "",
        trace_id: str = "",
    ) -> None:
        self.url = url
        self.status = status
        self.err_code = err_code
        self.message = message[:200]
        self.trace_id = trace_id
        detail = self.message or "未知错误"
        super().__init__(f"HTTP {status} {url}：[{err_code}] {detail}")
