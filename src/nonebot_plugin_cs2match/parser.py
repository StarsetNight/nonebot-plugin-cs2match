# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from nonebot import get_plugin_config

from . import template
from .config import Config
from .render import typst_str
from .tools import format_iso

config = get_plugin_config(Config)  # 取自 config.py 中的静态配置

KNOWN_STATUS = {"not_started", "running", "finished", "canceled", "postponed"}
TERMINAL_STATUS = {"finished", "canceled"}

# 监视挑选顺序：正在打的最紧急，其次未开始，延期/未知状态最后。
# 只在 skip_terminal 的队名后备分支里用作排序键（同一状态保持原列表顺序）。
STATUS_PRIORITY = {"running": 0, "not_started": 1, "postponed": 2}


def safe_status(status: str) -> str:
    """把比赛状态归一化到模板中已定义的状态标识符。"""
    return status if status in KNOWN_STATUS else "unknown"


def find_match(
    matches: list[dict[str, Any]],
    query: str,
    *,
    slug_case_sensitive: bool = True,
    skip_terminal: bool = False,
) -> tuple[dict[str, Any] | None, str, int]:
    """在比赛列表中定位比赛。

    优先按 slug 精确定位（沿用各调用方原有的大小写语义）；未命中时后备为
    按战队名查找：将双方队名与查询串做大小写不敏感的整名精确匹配。

    :param matches: 比赛列表（如 PandaScoreClient.list_matches 的结果）
    :param query: 用户输入的查询串（建议已去除首尾空白）
    :param slug_case_sensitive: slug 匹配是否区分大小写
        （on_check_match 沿用 True，on_monitor_match 沿用 False）
    :param skip_terminal: 是否略过已结束/已取消的比赛。
        `/monitor` 必须传 True：list_matches 返回的是 past+running+upcoming，
        已结束的 past 排在最前面，不过滤的话按战队名搜索几乎必然命中一场
        已经打完的比赛，从而无法监视真正还能打的那场。
        `/match`（比分查询）保持 False——查已结束比赛的比分是正常需求。
    :return: (match, matched_by, team_hit_count)
        - match: 命中的比赛；未命中为 None
        - matched_by: "slug" | "team" | ""（未命中）
        - team_hit_count: 队名命中的比赛数（仅队名后备命中时才有意义；
          大于 1 时调用方应提示"匹配到多个，取第一个"）。
          skip_terminal=True 时只统计可监视（非终态）的比赛。
    """
    candidates = (
        [m for m in matches if m.get("status", "unknown") not in TERMINAL_STATUS]
        if skip_terminal
        else matches
    )

    # 1) slug 精确定位，沿用原有语义
    if slug_case_sensitive:
        hit = next((m for m in candidates if m.get("slug") == query), None)
    else:
        hit = next((m for m in candidates if m.get("slug", "").lower() == query), None)

    if hit is not None:
        return hit, "slug", 0

    # 2) 队名后备：大小写不敏感的整名精确匹配，命中多个时取列表第一个
    query_lower = query.lower()
    team_hits = [
        m for m in candidates
        if any(
            (o.get("opponent") or {}).get("name", "").lower() == query_lower
            for o in m.get("opponents") or []
        )
    ]

    if team_hits:
        if skip_terminal:
            # 过滤后 past 里可能只剩延期场次，别让它排在一场正在打的比赛前面
            team_hits.sort(
                key=lambda m: STATUS_PRIORITY.get(str(m.get("status")), 99)
            )
        return team_hits[0], "team", len(team_hits)

    return None, "", 0


class MatchParser:
    @staticmethod
    def parse(match: dict[str, Any]) -> dict[str, Any]:
        # 基础信息
        serie = (match.get("serie") or {}).get("full_name", "Unknown Match")
        slug = match.get("slug", "unknown")
        match_time = match.get("scheduled_at") or match.get("begin_at") or "unknown time"
        status = safe_status(match.get("status", "unknown"))

        # 队伍
        opponents = match.get("opponents", [])
        if len(opponents) >= 2:
            team_a = opponents[0].get("opponent", {}).get("name", "TBD")
            team_b = opponents[1].get("opponent", {}).get("name", "TBD")
        else:
            team_a = "TBD"
            team_b = "TBD"

        # 比分（bo match）
        score_map: dict[int, int] = {}
        for r in match.get("results") or []:
            tid = r.get("team_id")
            if tid is not None:
                score_map[tid] = r.get("score", 0)

        # 按顺序映射
        score_a = 0
        score_b = 0

        if len(opponents) >= 2:
            a_id = opponents[0].get("opponent", {}).get("id")
            b_id = opponents[1].get("opponent", {}).get("id")

            score_a = score_map.get(a_id, 0) if a_id is not None else 0
            score_b = score_map.get(b_id, 0) if b_id is not None else 0

        return {
            "serie": serie,
            "slug": slug,
            "time": format_iso(match_time),
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score_a,
            "score_b": score_b,
            "status": status,
        }

    @staticmethod
    def team_names(match: dict[str, Any]) -> tuple[str, str]:
        """提取对阵双方队名（缺少对手信息时对应位置返回"未知"）。"""
        opponents = match.get("opponents") or []

        team_a = (
            opponents[0]
            .get("opponent", {})
            .get("name", "未知")
            if len(opponents) >= 2
            else "未知"
        )

        team_b = (
            opponents[1]
            .get("opponent", {})
            .get("name", "未知")
            if len(opponents) >= 2
            else "未知"
        )

        return team_a, team_b

    @classmethod
    def order_matches(cls, matches: list[dict[str, Any]], priority_mode: str) -> list[dict[str, Any]]:
        """按"赛事优先级降序 + 赛事内开赛时间升序"整理出渲染与分页共用的有序列表。

        ``whitelist-only`` 模式下先过滤掉非白名单赛事。
        """
        series: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for match in matches:
            serie = (match.get("serie") or {}).get("full_name", "未知赛事")
            if (
                priority_mode == "whitelist-only"
                and cls.serie_priority(serie) == 0
            ):
                continue
            series[serie].append(match)

        sorted_series = sorted(
            series.items(),
            key=lambda s: cls.serie_priority(s[0]),
            reverse=True,
        )

        ordered: list[dict[str, Any]] = []
        for _, serie_matches in sorted_series:
            ordered.extend(sorted(serie_matches, key=lambda x: x.get("scheduled_at") or ""))

        return ordered

    @classmethod
    def prerender_list(cls, matches: list[dict[str, Any]], priority_mode: str) -> str:
        """渲染整份列表。

        保持原签名与原输出（既有渲染缓存按内容哈希命中，不能让它失效）。
        """
        return cls.prerender_list_page(cls.order_matches(matches, priority_mode))

    @classmethod
    def prerender_list_page(cls, ordered_matches: list[dict[str, Any]], omitted: int = 0) -> str:
        """渲染一页比赛。

        :param ordered_matches: 需为 ``order_matches`` / ``paginate_matches`` 产出的有序列表
        :param omitted: 因分页上限被省略的场次数，大于 0 时在图片末尾提示
        """
        series: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for match in ordered_matches:
            series[(match.get("serie") or {}).get("full_name", "未知赛事")].append(match)

        sorted_series = sorted(
            series.items(),
            key=lambda s: cls.serie_priority(s[0]),
            reverse=True,
        )

        content = template.list_match

        for serie_name, serie_matches in sorted_series:
            content += f'#series_card({typst_str(serie_name)}, [\n'

            for match in serie_matches:
                match_json = cls.parse(match)

                content += (
                    f'#match_card('
                    f'{typst_str(match_json["slug"])},'
                    f'{typst_str(match_json["time"])},'
                    f'{typst_str(match_json["team_a"])},'
                    f'{match_json["score_a"]},'
                    f'{match_json["score_b"]},'
                    f'{typst_str(match_json["team_b"])},'
                    f'{match_json["status"]}'
                    f')\n'
                )

            content += '])\n\n'

        if omitted > 0:
            # 被截断的提示直接画进最后一张图：QQ 被动回复有次数上限，省一条消息
            content += (
                '#v(10pt)\n'
                '#text(size: 8pt, fill: rgb("#6b7280"))['
                f'比赛较多，已省略 {omitted} 场，可用 /matches past|running|upcoming 缩小范围'
                ']\n'
            )

        return content

    @classmethod
    def paginate_matches(
        cls,
        matches: list[dict[str, Any]],
        priority_mode: str,
        per_image: int,
        max_images: int,
    ) -> tuple[list[list[dict[str, Any]]], int]:
        """把比赛列表切页，供逐张渲染发送（QQ 群图片内联上传有大小上限）。

        :param per_image: 每张图最多多少场比赛；``<= 0`` 表示不限制（退回单张长图）
        :param max_images: 最多渲染多少张图；``<= 0`` 表示不限制
        :return: (页列表, 被省略的场次数)；空列表返回 ``([], 0)``
        """
        ordered = cls.order_matches(matches, priority_mode)
        if not ordered:
            return [], 0

        size = per_image if per_image > 0 else len(ordered)
        pages = [ordered[i : i + size] for i in range(0, len(ordered), size)]

        omitted = 0
        if max_images > 0 and len(pages) > max_images:
            omitted = len(ordered) - sum(len(page) for page in pages[:max_images])
            pages = pages[:max_images]

        return pages, omitted

    @classmethod
    def prerender_match(cls, match: dict[str, Any], comment: str = "") -> str:
        opponents = match.get("opponents") or []

        team_a, team_b = cls.team_names(match)

        # 如果只能获取到一边选手的信息，那还有什么意义呢？
        if len(opponents) >= 2:
            a_id = opponents[0].get("opponent", {}).get("id")
            b_id = opponents[1].get("opponent", {}).get("id")
        else:
            a_id = b_id = None

        # 与 MatchParser.parse 保持一致：按 team_id 映射比分，
        # 不依赖 results 数组与 opponents 数组的索引顺序
        score_map: dict[int, int] = {}
        for r in match.get("results") or []:
            tid = r.get("team_id")
            if tid is not None:
                score_map[tid] = r.get("score") or 0

        if a_id is not None and b_id is not None:
            a_id = cast(int, a_id)
            b_id = cast(int, b_id)
            score_a = score_map.get(a_id, 0)
            score_b = score_map.get(b_id, 0)
        else:
            score_a = score_b = 0

        games = []

        for game in match.get("games") or []:
            winner_id = (
                    game.get("winner") or {}
            ).get("id")

            if winner_id == a_id:
                winner = team_a

            elif winner_id == b_id:
                winner = team_b

            else:
                winner = "未知"

            games.append(
                f"""
                    (
                        position: {int(game.get("position") or 0)},
                        winner: {typst_str(winner)},
                        status: {typst_str(safe_status(game.get("status", "unknown")))},
                    ),
                    """
            )

        games_text = "\n".join(games)

        return f"""{template.get_match}
        #let match = (
            name: {typst_str(match.get("name", "未知比赛"))},
            league: {typst_str((match.get("league") or {}).get("name", "未知赛事"))},
            serie: {typst_str((match.get("serie") or {}).get("full_name", "未知系列"))},
            team_a: {typst_str(team_a)},
            team_b: {typst_str(team_b)},
            score_a: {score_a},
            score_b: {score_b},
            status: {typst_str(safe_status(match.get("status", "unknown")))},
            time: {typst_str(format_iso(match.get("scheduled_at") or match.get("begin_at") or "未知时间"))},
            bo: {int(match.get("number_of_games") or 0)},
            games: (
                {games_text}
            ),
        )
        
        {comment}

        #match_detail(match)
        """

    @classmethod
    def classify_serie(cls, name: str) -> str:
        if not name:
            return "other"

        n = name.lower()

        for key, _, keywords in config.serie_rules:
            if any(k in n for k in keywords):
                return key

        return "other"

    @classmethod
    def serie_priority(cls, name: str) -> int:
        key = cls.classify_serie(name)

        for k, priority, _ in config.serie_rules:
            if k == key:
                return priority

        return 0
