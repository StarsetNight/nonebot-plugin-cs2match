# nonebot-plugin-cs2match

_✨ CS2赛事查询 ✨_


<a href="./LICENSE">
    <img src="https://img.shields.io/github/license/StarsetNight/nonebot-plugin-cs2match.svg" alt="license">
</a>
<a href="https://pypi.python.org/pypi/nonebot-plugin-cs2match">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-cs2match.svg" alt="pypi">
</a>
<img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">

## 📖 介绍

实时追踪 Counter-Strike 2 职业赛事，进行开赛自动提醒与大比分异动推送。

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

下表中的配置可通过插件的`config.py`或 NoneBot 的`.env`提供

|       配置项        | 必配置 | 默认值 |                       说明                       |
|:----------------:|:---:|:---:|:----------------------------------------------:|
| pandascore_token |  是  |  无  |             PandaScore提供商获取的Token              |
|   serie_rules    |  否  | 见文件 |           按照优先级给赛事系列排序（同时也是赛事系列白名单）            |
|  client_timeout  |  否  | 10  |              API调用客户端最大超时限制，单位为秒               |
|    cache_ttl     |  否  | 60  |         api请求临时缓存存活时间及比赛监视检测间隔时间，单位为秒          |
|  cache_max_size  |  否  | 64  |                 临时渲染缓存队列中最多存储数                 |
| match_page_size  |  否  | 100 |         比赛列表接口每页条数（PandaScore 上限为 100）         |
|    max_misses    |  否  |  3  | 连续多少轮“确证比赛已不存在”后自动取消监视。仅统计列表接口与单场比赛接口都查无此比赛的轮次 |

以上配置既可以在插件的`config.py`中修改，也可以通过 NoneBot 的`.env`提供（如`CACHE_TTL=30`）。

### 监视机制说明

- 监视目标的存活不依赖比赛列表的“第一页窗口”：列表里找不到时会按比赛 ID 直查单场比赛接口，
  只有接口明确返回 404（比赛确实不存在）才计入 `MAX_MISSES`；接口报错、限流、窗口滑动都不会导致监视被取消。
- 若比赛列表接口调用失败，本轮不判定任何目标消失，也不会误取消监视。
- 监视任务按 Bot 实例（`self_id`）隔离；单个 Bot 掉线只影响它自己，且任务异常退出后会记录原因并在下次
  `/monitor` 时自动重启，不会再出现“监视静默停止”。

部分运行时配置（DynamicConfig）仅开放运行时修改，详情查阅[指令表](#指令表)

## 🎉 使用
### 指令表
|                指令                 |      权限      | 需要@ | 范围 |                            说明                             |
|:---------------------------------:|:------------:|:---:|:--:|:---------------------------------------------------------:|
|        `cs2help` / `cs2帮助`        |     所有人      |  否  | 任何 |                         获取插件命令用法                          |
| `matches [past/running/upcoming]` |     所有人      |  否  | 任何 |                比赛列表获取。`matches`可用`比赛列表`代替。                |
|         `match <slug/队名>`         |     所有人      |  否  | 任何 |     比赛大比分获取，支持直接输入战队名（同名多场时展示第一个并提示）。`match`可用`比分`代替。     |
|        `monitor <slug/队名>`        | 群管/SUPERUSER |  否  | 群聊 | 追加监视比赛开始、大比分变动、结束，参数为“cancel”时取消本群全部监听。`monitor`可用`监视`代替。 |
|      `cs2whitelist <on/off>`      | 群管/SUPERUSER |  否  | 任何 |        设置比赛列表是否仅显示白名单赛事系列。`cs2whitelist`可用`白名单`代替。        |
|             `cs2uid`              |     所有人      |  否  | 任何 |                     查看自己的用户ID与当前场景ID。                     |
