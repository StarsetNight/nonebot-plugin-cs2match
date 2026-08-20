# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

# help_plain_text和help_text两边一定要同时改！

help_plain_text = """NoneBot CS2赛事查询帮助

/cs2help
显示此帮助信息。

/matches [past|running|upcoming]
查询CS2比赛列表，支持查看已结束、进行中和即将开始的比赛。

/match <slug或队名>
查询指定比赛的详细比分信息，支持直接输入战队名（同名多场时展示第一个并提示）。

/monitor <slug或队名>
追加比赛监听，自动推送比赛开始、比分变化和结束状态。
使用 /monitor cancel 可取消本群全部监听。

/cs2whitelist <on|off>
开启或关闭比赛列表白名单过滤。

/cs2uid
查看自己的用户ID与当前场景ID。"""

help_text = """#set text(font: ("Consolas", "SimHei", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei"))

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
    "match <slug/队名> / 比分",
    "查看比赛大比分详情，支持直接输入战队名（同名多场时展示第一个并提示）。"
  ),

  card(
    "monitor <slug/队名> / 监视",
    "监视比赛开始、比分变动、结束，参数为“cancel”时取消本群全部监听。"
  ),
  
  card(
    "cs2whitelist <on/off> / 白名单",
    "设置比赛列表是否仅显示白名单赛事系列。"
  ),

  card(
    "cs2uid / 我的id",
    "查看自己的用户ID与当前场景ID。"
  ),
)
"""

list_match = """#set text(font: ("Consolas", "SimHei", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei"))

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
#let unknown = tag("未知", rgb("#9ca3af"))


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
        #title
      ]

      #v(8pt)

      #body
    ]
  )

  #v(12pt)
]

"""

get_match = """#set text(font: ("Consolas", "SimHei", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei"))

#set page(
  width: 320pt,
  height: auto,
  margin: 12pt,
)

// =====================
// STATUS MAP
// =====================
#let status_map = (
  "not_started": (
    text: "未开始",
    color: rgb(245, 158, 11),
  ),
  "running": (
    text: "进行中",
    color: rgb(239, 68, 68),
  ),
  "finished": (
    text: "已结束",
    color: rgb(16, 185, 129),
  ),
  "canceled": (
    text: "已取消",
    color: rgb(107, 114, 128),
  ),
  "postponed": (
    text: "已延期",
    color: rgb(59, 130, 246),
  ),
  "unknown": (
    text: "未知",
    color: rgb(156, 163, 175),
  ),
)


// =====================
// MATCH DETAIL
// =====================
#let match_detail(m) = [

  #let s = status_map.at(
    m.status,
    default: status_map.at("unknown")
  )

  // =====================
  // MAIN CARD
  // =====================
  #box(
    width: 100%,
    stroke: 0.6pt + rgb(229, 231, 235),
    radius: 12pt,
    inset: 12pt,

    [

      // =====================
      // TOP INFO CARD
      // =====================
      #box(
        width: 100%,
        fill: rgb(248, 250, 252),
        stroke: 0.5pt + rgb(229, 231, 235),
        radius: 10pt,
        inset: 10pt,

        [

          #grid(
            columns: (1fr, auto),

            [
              #text(
                size: 11pt,
                weight: "bold",
              )[
                #m.name
              ]

              #v(3pt)

              #text(
                size: 8pt,
                fill: rgb(107,114,128),
              )[
                #m.league · #m.serie
              ]

              #v(3pt)

              #text(
                size: 8pt,
                fill: rgb(107,114,128),
              )[
                时间: #m.time
              ]
            ],

            [
              #text(
                size: 8pt,
                fill: s.color,
                weight: "bold",
              )[
                #s.text
              ]

              #v(4pt)

              #text(
                size: 8pt,
                fill: rgb(107,114,128),
              )[
                BO#m.bo
              ]
            ]
          )
        ]
      )


      #v(12pt)


      // =====================
      // TEAM SCORE
      // =====================
      #grid(
        columns: (1fr, auto, 1fr),
        align: center + horizon,

        [
          #box(
            width: 80pt,
            align(center + horizon)[
              #text(
                size: 12pt,
                weight: "bold",
                fill: rgb(37, 99, 235),
              )[
                #m.team_a
              ]
            ]
          )
        ],

        [
          #box(
            fill: rgb(17, 24, 39),
            radius: 8pt,
            inset: 10pt,
            align(center + horizon)[
              #text(
                size: 12pt,
                weight: "bold",
                fill: white,
              )[
                #m.score_a - #m.score_b
              ]
            ]
          )
        ],

        [
          #box(
            width: 80pt,
            align(center + horizon)[
              #text(
                size: 12pt,
                weight: "bold",
                fill: rgb(239, 68, 68),
              )[
                #m.team_b
              ]
            ]
          )
        ]
      )


      #v(12pt)


      // =====================
      // MAP LIST CARD
      // =====================
      #box(
        width: 100%,
        fill: rgb(250,250,250),
        stroke: 0.5pt + rgb(229,231,235),
        radius: 10pt,
        inset: 10pt,

        [

          #text(
            size: 9pt,
            weight: "bold",
          )[
            地图列表
          ]

          #v(6pt)


          #if m.games.len() == 0 [

            #text(
              size: 8pt,
              fill: rgb(156,163,175),
            )[
              暂无地图数据
            ]

          ]


          #for g in m.games [

            #let gs = status_map.at(
              g.status,
              default: status_map.at("unknown")
            )


            #grid(
              columns: (1fr, 1fr, 1fr),
              align: center,

              [
                #text(
                  size: 8pt,
                  fill: rgb(156,163,175),
                )[
                  地图 #g.position
                ]
              ],

              [
                #text(
                  size: 8pt,
                )[
                  #g.winner 胜出
                ]
              ],

              [
                #text(
                  size: 8pt,
                  fill: gs.color,
                )[
                  #gs.text
                ]
              ]
            )

            #v(4pt)
          ]
        ]
      )
    ]
  )
]
"""

push_comment = """#box(
  width: 100%,
  fill: rgb("#eff6ff"),
  stroke: 0.5pt + rgb("#93c5fd"),
  radius: 8pt,
  inset: 8pt,
)[
  #text(
    size: 9pt,
    fill: rgb("#2563eb"),
    weight: "bold",
  )[
    自动推送
  ]

  #v(3pt)

  #text(
    size: 8pt,
    fill: rgb("#64748b"),
  )[
    比赛状态发生变化，已自动发送最新赛况！
  ]
]

#v(10pt)"""
