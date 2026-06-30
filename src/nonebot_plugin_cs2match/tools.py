# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

from typing import Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from binascii import crc32

from aiohttp import ClientSession
from ayafileio import open
from typst import compile

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot import require

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_plugin_cache_file

from . import typst_template

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


async def typst_render(typst_content: str) -> MessageSegment:
    cache_index = crc32(typst_content.encode("utf-8"))

    cache_file_path = get_plugin_cache_file(f"{cache_index:08X}.png")

    if cache_file_path.exists():
        cache_file = open(cache_file_path, "rb")
        cache_data = await cache_file.readall()
        await cache_file.close()
        return MessageSegment.image(cache_data)

    file_data = compile(typst_content.encode(), format="png", ppi=144.0)
    cache_file = open(cache_file_path, "wb")
    await cache_file.write(file_data)
    await cache_file.close()

    return MessageSegment.image(file_data)


class MatchParser:
    @staticmethod
    async def parse(match: dict[str, Any]) -> dict[str, Any]:
        # 基础信息
        serie = match.get("serie", {}).get("full_name", "Unknown Match")
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
            "serie": serie,
            "slug": slug,
            "time": format_iso(time),
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score_a,
            "score_b": score_b,
            "status": status,
        }

    @classmethod
    async def prerender_list(cls, matches):
        series = defaultdict(list)

        for match in matches:
            serie = (match.get("serie") or {}).get("full_name", "未知赛事")
            series[serie].append(match)

        content = typst_template.list_match

        for serie_name, serie_matches in series.items():
            content += f'#series_card("{serie_name}", [\n'

            serie_matches.sort(key=lambda x: x["scheduled_at"])

            for match in serie_matches:
                match_json = await cls.parse(match)

                content += (
                    f'#match_card('
                    f'"{match_json["slug"]}",'
                    f'"{match_json["time"]}",'
                    f'"{match_json["team_a"]}",'
                    f'{match_json["score_a"]},'
                    f'{match_json["score_b"]},'
                    f'"{match_json["team_b"]}",'
                    f'{match_json["status"]}'
                    f')\n'
                )

            content += '])\n\n'

        return content


class PandaScoreClient:
    def __init__(self, token: str) -> None:
        self.base = "https://api.pandascore.co"
        self.headers = {
            "Authorization": f"Bearer {token}"
        }
        self.session: ClientSession | None = None

    async def _get(self, path, params=None) -> Any:
        if not self.session:
            self.session = ClientSession()
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
