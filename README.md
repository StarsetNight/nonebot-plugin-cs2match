# nonebot-plugin-cs2match

_✨ CS2赛事查询助手 ✨_


<a href="./LICENSE">
    <img src="https://img.shields.io/github/license/owner/nonebot-plugin-cs2match.svg" alt="license">
</a>
<a href="https://pypi.python.org/pypi/nonebot-plugin-cs2match">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-cs2match.svg" alt="pypi">
</a>
<img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="python">

## 📖 介绍

实时追踪 Counter-Strike 2 职业赛事，开赛自动提醒、关键赛况与大比分异动推送。

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-cs2match

</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details>
<summary>pip</summary>

    pip install nonebot-plugin-cs2match
</details>
<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-cs2match
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-cs2match
</details>
<details>
<summary>conda</summary>

    conda install nonebot-plugin-cs2match
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_cs2match"]

</details>

## ⚙️ 配置

在 nonebot2 项目的`.env`文件中添加下表中的必填配置

| 配置项 | 必填 | 默认值 |          说明           |
|:-----:|:----:|:----:|:---------------------:|
| PANDASCORE_TOKEN | 是 | 无 | PandaScore提供商获取的Token |

## 🎉 使用
### 指令表
|                指令                 |      权限      | 需要@ | 范围 |                  说明                  |
|:---------------------------------:|:------------:|:----:|:--:|:------------------------------------:|
|        `cs2help` / `cs2帮助`        |     所有人      | 否 | 任何 |               获取插件命令用法               |
| `matches [past/running/upcoming]` |     所有人      | 否 | 任何 |    比赛列表获取。`matches`可用`比赛列表`”`代替。     |
|           `match <id>`            |     所有人      | 否 | 任何 |      比赛大比分获取。`match`可用`比分`”`代替。      |
|            `team <id>`            |     所有人      | 否 | 任何 |      战队信息获取。`team`可用`查战队`”`代替。       |
|          `monitor <id>`           | 群管/SUPERUSER | 否 | 群聊 | 监视比赛开始、大比分变动、结束。`monitor`可用`监视`”`代替。 |
