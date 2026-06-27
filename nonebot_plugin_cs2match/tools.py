# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

import aiohttp
import typst
from io import BytesIO
from typing import Any
from datetime import datetime, timezone, timedelta

from nonebot.adapters.onebot.v11 import MessageSegment

def format_iso(iso: str) -> str:
    try:
        if not iso:
            return "时间未知"

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))

        dt_sh = dt.astimezone(
            timezone(timedelta(hours=8))
        )

        return dt_sh.strftime("%m-%d %H:%M")

    except ValueError:
        return "时间未知"


async def typst_render(typst_content: str) -> MessageSegment.image:
    # TODO 缓存处理
    return MessageSegment.image(BytesIO(typst.compile(typst_content.encode(), format="png", ppi=144.0)))


class MatchParser:
    @staticmethod
    async def parse(match: dict[str, Any]) -> dict[str, Any]:
        # 基础信息
        name = match.get("serie", {}).get("full_name", "Unknown Match")
        slug = match.get("slug", "unknown")
        time = match.get("scheduled_at") or match.get("begin_at") or "unknown time"
        status = match.get("status", "unknown")

        # 队伍
        opponents = match.get("opponents", [])
        if len(opponents) >= 2:
            team_a = opponents[0]["opponent"]["name"]
            team_b = opponents[1]["opponent"]["name"]
        else:
            team_a = "TBD"
            team_b = "TBD"

        # 比分（bo match）
        score_map: dict[int, int] = {}
        for r in match.get("results", []):
            tid = r.get("team_id")
            score_map[tid] = r.get("score", 0)

        # 按顺序映射
        score_a = 0
        score_b = 0

        if len(opponents) >= 2:
            a_id = opponents[0]["opponent"]["id"]
            b_id = opponents[1]["opponent"]["id"]

            score_a = score_map.get(a_id)
            score_b = score_map.get(b_id)

        return {
            "name": name,
            "slug": slug,
            "time": format_iso(time),
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score_a,
            "score_b": score_b,
            "status": status,
        }


class PandaScoreClient:
    def __init__(self, token: str) -> None:
        self.base = "https://api.pandascore.co"
        self.headers = {
            "Authorization": f"Bearer {token}"
        }
        self.session: aiohttp.ClientSession | None = None

    async def _get(self, path, params=None) -> Any:
        if not self.session:
            self.session = aiohttp.ClientSession()
        url = f"{self.base}{path}"
        async with self.session.get(url, headers=self.headers, params=params) as resp:
            return await resp.json()

    async def list_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches")
            if m.get("videogame", {}).get("id") == 3
        ]

    async def list_past_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/past")
            if m.get("videogame", {}).get("id") == 3
        ]

    async def list_running_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/running")
            if m.get("videogame", {}).get("id") == 3
        ]

    async def list_upcoming_matches(self) -> list[dict[str, Any]]:
        return [
            m for m in await self._get("/matches/upcoming")
            if m.get("videogame", {}).get("id") == 3
        ]

    async def get_match(self, match_id: str) -> dict[str, Any]:
        return await self._get(f"/matches/{match_id}")

    async def get_match_score(self, match_id: str) -> dict[str, int] | None:
        match = await self.get_match(match_id)

        results = match.get("results", [])
        opponents = match.get("opponents", [])

        if len(opponents) < 2:
            return None

        team_map = {
            opponents[0]["opponent"]["id"]: opponents[0]["opponent"]["name"],
            opponents[1]["opponent"]["id"]: opponents[1]["opponent"]["name"]
        }

        score = {}
        for r in results:
            score[team_map.get(r["team_id"], str(r["team_id"]))] = r["score"]

        return score

    async def get_teams(self, match_id: str) -> list[dict[str, Any]]:
        match = await self.get_match(match_id)

        opponents = match.get("opponents", [])

        teams = []
        for o in opponents:
            t = o["opponent"]
            teams.append({
                "id": t["id"],
                "name": t["name"],
                "acronym": t.get("acronym"),
                "country": t.get("location"),
                "image": t.get("image_url")
            })

        return teams
