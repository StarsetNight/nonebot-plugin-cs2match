# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

help_text = """#set text(font: ("Consolas", "SimHei"))

#set page(
  width: 400pt,
  height: auto,
  margin: 12pt,
)

#let primary = rgb("#3b82f6")
#let border = rgb("#e5e7eb")
#let bg = rgb("#f8fafc")

// ===== 顶部标题栏 =====
#box(
  width: 100%,
  fill: rgb("#0d005e"),
  inset: 12pt,
  radius: 6pt,
  [
    #text(
      size: 11pt,
      fill: white,
      weight: "bold"
    )[
      NoneBot CS2赛事查询帮助
    ]
  ]
)

#v(10pt)

// ===== 卡片组件 =====
#let card(title, desc) = {
  box(
    width: 100%,
    stroke: 0.5pt + border,
    radius: 8pt,
    inset: 10pt,
    fill: white,

    [
      // 命令标签（更明显 UI 化）
      #box(
        stroke: rgb("#bfdbfe"),
        fill: rgb("#eff6ff"),
        radius: 4pt,
        inset: 8pt,
        text(
          size: 9pt,
          fill: primary
        )[
          #title
        ]
      )

      #v(6pt)

      #text(size: 9pt, fill: rgb("#2a2a2a"))[
        #desc
      ]
    ]
  )
}

// ===== 内容区域 =====
#grid(
  columns: 1,
  row-gutter: 8pt,

  card(
    "cs2help / cs2帮助",
    "显示帮助。"
  ),

  card(
    "matches [past/running/upcoming] / 比赛列表",
    "比赛列表（过去 / 进行中 / 即将开始）。"
  ),

  card(
    "match <id> / 比分",
    "查看比赛大比分详情。"
  ),

  card(
    "team <id> / 查战队",
    "查看战队信息。"
  ),

  card(
    "monitor <id> / 监视",
    "监视比赛开始、比分变动、结束。"
  ),
  
  card(
    "cs2whitelist <on/off> / 白名单",
    "设置比赛列表是否仅显示白名单赛事系列。"
  ),
)
"""

list_match = """#set text(font: ("Consolas", "SimHei"))

#set page(
  width: 320pt,
  height: auto,
  margin: 12pt,
)

#let primary = rgb("#3b82f6")
#let border = rgb("#e5e7eb")
#let gray = rgb("#6b7280")

// =====================
// 顶部栏
// =====================
#box(
  width: 100%,
  fill: rgb("#3a67a7"),
  inset: 12pt,
  radius: 6pt,
  [
    #text(
      size: 11pt,
      fill: white,
      weight: "bold"
    )[
      Counter-Strike 2 赛事列表查询
    ]
  ]
)

#v(10pt)


// =====================
// 状态标签
// =====================
#let tag(label, color) = box(
  fill: color,
  radius: 5pt,
  inset: 4pt,
  text(size: 8pt, fill: white)[#label]
)

#let not_started = tag("未开始", rgb("#f59e0b"))
#let running = tag("进行中", rgb("#ef4444"))
#let finished = tag("已结束", rgb("#10b981"))
#let canceled = tag("取消", rgb("#6b7280"))
#let postponed = tag("延期", rgb("#3b82f6"))


// =====================
// 赛事卡片
// =====================
#let match_card(slug, time, team_a, score_a, score_b, team_b, status) = [
  #box(
    width: 100%,
    stroke: 0.5pt + border,
    radius: 8pt,
    inset: 10pt,
    fill: white,

    [

      // =====================
      // HEADER（赛事名 + 时间）
      // =====================
      #grid(
        columns: (1fr, auto),

        [
          #v(2pt)
          #text(
            size: 10pt,
            fill: rgb("#363636"),
            weight: "bold"
          )[#time]
        ],

        // =====================
        // STATUS
        // =====================
        [#status],
      )

      #v(6pt)

      // =====================
      // MATCH ROW（核心比赛信息）
      // =====================
      #grid(
        columns: (1fr, auto, 1fr),
        align: center,

        // 左队
        [
          #text(
            fill: rgb("#3b82f6"),
            weight: "bold"
          )[#team_a]
        ],

        // 比分
        [
          #text(
            size: 14pt,
            weight: "bold",
            fill: rgb("#250058")
          )[
            #score_a  -  #score_b
          ]
        ],

        // 右队
        [
          #text(
            fill: rgb("#ef4444"),
            weight: "bold"
          )[#team_b]
        ]
      )

      #v(6pt)

      #text(
        size: 6pt,
        fill: rgb("#a7a7a7")
      )[#slug]
    ]
  )

  #v(10pt)
]

#let series_card(title, body) = [
  #box(
    width: 100%,
    stroke: 0.7pt + rgb("#d1d5db"),
    radius: 10pt,
    fill: rgb("#f8fafc"),
    inset: 10pt,

    [
      #text(
        size: 11pt,
        weight: "bold",
        fill: primary,
      )[
        🏆 #title
      ]

      #v(8pt)

      #body
    ]
  )

  #v(12pt)
]

"""
