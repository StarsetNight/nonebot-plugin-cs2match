# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

from datetime import datetime

def format_iso(iso: str) -> str:
    try:
        if not iso:
            return "时间未知"

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))

        # 自动读取系统时区
        local_tz = datetime.now().astimezone().tzinfo

        dt_local = dt.astimezone(local_tz)

        return dt_local.strftime("%m-%d %H:%M")

    except ValueError:
        return "时间未知"
