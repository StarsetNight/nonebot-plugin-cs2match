# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from typing import Any, cast

from arclet.alconna import Alconna, Args, AllParam
from nonebot import logger, get_driver, get_plugin_config, require
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config
# init的配置读取必须先于其他要使用init中配置的导入模块，否则会缺配置读不了
driver = get_driver()
global_config = driver.config
config = get_plugin_config(Config)  # 取自config.py中的静态配置
from .api import PandaScoreClient, check_proactive_msg_permission
from .monitor import MonitorClient
from .parser import MatchParser, find_match
from .render import typst_render, typst_render_for_stable_cache
from .template import help_plain_text, help_text
from .rule import is_enabled
from .dynamic_config import DynamicConfigSystem, PriorityMode
from . import panel

require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")
from nonebot.adapters import Bot
from nonebot_plugin_alconna import Query, on_alconna, UniMessage
from nonebot_plugin_localstore import get_plugin_data_file
from nonebot_plugin_uninfo import Uninfo, ADMIN, SceneType

panda_client: PandaScoreClient | None = None
dynamic_config: DynamicConfigSystem | None = None  # 取自插件内编写的DynamicConfigSystem
# self_id -> 该Bot实例的比赛监视任务（按Bot隔离，避免多Bot串台/互相阻塞）
monitor_clients: dict[str, MonitorClient] = {}


def get_monitor_client(self_id: str, client: PandaScoreClient) -> MonitorClient:
    """取得指定Bot实例的监视客户端（不存在则新建）。

    同时兼具自愈：监视任务若已异常退出，这里会顺带重启它，
    避免"任务静默死亡后再也没有监视"。
    """
    monitor = monitor_clients.get(self_id)
    if monitor is None:
        monitor = MonitorClient(client=client, self_id=self_id)
        monitor_clients[self_id] = monitor
    else:
        monitor.start()
    return monitor

# 注册插件
__plugin_meta__ = PluginMetadata(
    name="CS2赛事助手",
    description="实时追踪 Counter-Strike 2 职业赛事，开赛自动提醒、关键赛况与大比分异动推送",
    usage=help_plain_text,
    type="application",
    homepage="https://github.com/StarsetNight/nonebot-plugin-cs2match",
    config=Config,
    supported_adapters={"~onebot.v11", "~qq"},
    extra={"author": "StarsetNight <starsetnight@outlook.com>"}
)

@driver.on_startup
async def on_startup_check():
    global panda_client, dynamic_config
    if config.pandascore_token is None:
        logger.warning("pandascore_token未设置，CS2数据查询功能将不可用或受限。")
        logger.info("请前往PandaScore官网注册获取Token，并在插件目录config.py中配置："
                    "pandascore_token: str | None = <你的Token>")
        return
    panda_client = PandaScoreClient(config.pandascore_token)

    config_path = get_plugin_data_file("config.json")
    if not config_path.exists():
        dynamic_config = await DynamicConfigSystem.new(config_path)
    else:
        try:
            dynamic_config = await DynamicConfigSystem.from_path(config_path)
        except Exception as e:
            logger.warning(f"配置文件损坏（{e}），已回退重建默认配置：{config_path}")
            dynamic_config = await DynamicConfigSystem.new(config_path)


@driver.on_shutdown
async def on_shutdown_cleanup():
    """关闭插件持有的连接与后台任务。"""
    global panda_client
    try:
        for monitor in list(monitor_clients.values()):
            await monitor.stop()
        monitor_clients.clear()
        if panda_client is not None:
            await panda_client.close()
            panda_client = None
    except Exception as e:
        logger.exception(f"插件资源清理失败：{e}")


@driver.on_bot_connect
async def on_bot_connect_sync_command_panel(bot: Bot):
    """QQ 机器人连接后把写死的命令帮助同步到「指令面板」（幂等 upsert）。

    面板内容是 panel.py 里写死的常量，没有运行时修改入口；同步失败只记日志，
    绝不影响机器人运行。
    """
    if not panel.is_qq_bot(bot):
        return
    await panel.sync_all(bot)


# 注意：nonebot_plugin_alconna 默认不把 NoneBot 的 COMMAND_START 当作 alconna 命令前缀
# （alconna_use_command_start 默认 False），必须逐个传 use_cmd_start=True，
# 插件才会跟随宿主配置。本项目 command_start={"", "/"}，因此 /cs2help 与 cs2help 都可用。
get_help = on_alconna(
    Alconna("cs2help"),
    aliases=("cs2帮助",),
    use_cmd_start=True,
    priority=10, block=True,
)
list_matches = on_alconna(
    Alconna("matches", Args["mode?", str]),
    aliases=("比赛列表",),
    rule=is_enabled,
    use_cmd_start=True,
    priority=10, block=True,
)
check_match = on_alconna(
    Alconna("match", Args["slug", AllParam]),
    aliases=("比分",),
    rule=is_enabled,
    use_cmd_start=True,
    priority=10, block=True,
)
monitor_match = on_alconna(
    Alconna("monitor", Args["slug", AllParam]),
    aliases=("监视",),
    rule=is_enabled,
    permission=SUPERUSER | ADMIN(),
    use_cmd_start=True,
    priority=10, block=True,
)
whitelist_config = on_alconna(
    Alconna("cs2whitelist", Args["state", str]),
    aliases=("白名单",),
    rule=is_enabled,
    permission=SUPERUSER | ADMIN(),
    use_cmd_start=True,
    priority=10, block=True,
)
get_my_id = on_alconna(
    Alconna("cs2uid"),
    aliases=("我的id",),
    use_cmd_start=True,
    priority=10, block=True,
)


@get_help.handle()
async def on_get_help():
    await get_help.finish(await typst_render_for_stable_cache(help_text, "help"))


@list_matches.handle()
async def on_list_matches(mode: Query[str] = Query("mode", default="")):
    arg = mode.result.strip()

    client = cast(PandaScoreClient, panda_client)

    func_map = {
        "past": client.list_past_matches,
        "running": client.list_running_matches,
        "upcoming": client.list_upcoming_matches,
    }

    await list_matches.send("正在查询比赛列表，请稍候...")

    func = func_map.get(arg, None)

    if func is None:
        func = client.list_matches

    try:
        matches = await func()
    except Exception as e:
        await list_matches.finish(f"由于API调度故障，请求失败：{e}")
        matches = []  # 哄类型检查器
    _config = cast(DynamicConfigSystem, dynamic_config)

    # 列表过长时渲染成一张巨图会被 QQ 拒收（40093011 上传文件大小超过限制），
    # 所以按"每张图 N 场、最多 M 张"切页，逐页渲染发送；
    # 被省略的场次数直接画进最后一张图（QQ 被动回复有次数上限，省一条消息）。
    pages, omitted = MatchParser.paginate_matches(
        matches,
        _config.config.priority_mode,
        config.matches_per_image,
        config.matches_max_images,
    )
    if not pages:
        pages = [[]]  # 保持旧行为：空列表也渲染一张只有标题的图

    last_index = len(pages) - 1
    try:
        images = [
            await typst_render(
                MatchParser.prerender_list_page(
                    page, omitted if index == last_index else 0
                )
            )
            for index, page in enumerate(pages)
        ]
    except Exception as e:
        logger.opt(exception=e).error(f"比赛列表渲染失败：{e}")
        await list_matches.finish(f"比赛列表渲染失败：{e}")
        images = []  # 哄检查器

    # 中间页用 send，最后一页用 finish。
    # 注意 finish 不能放进这个 try：FinishedException 也是 Exception，
    # 被吞掉会导致重复发送、收尾异常。
    for index, image in enumerate(images[:-1]):
        try:
            await list_matches.send(image)
        except Exception as e:
            logger.opt(exception=e).error(
                f"比赛列表第 {index + 1}/{len(images)} 页发送失败：{e}"
            )
            await list_matches.finish(
                "比赛列表图片发送失败（列表较长时可能触发平台上传限制），"
                "请用 /matches past|running|upcoming 缩小范围后重试。"
            )
    await list_matches.finish(images[-1])


@check_match.handle()
async def on_check_match(slug: UniMessage):
    slug = slug.extract_plain_text().strip()

    if not slug:
        await check_match.finish("用法：match <slug/队名>\n"
                                 "slug可在查询比赛列表的单个比赛左下角中找到。")

    client = cast(PandaScoreClient, panda_client)

    await check_match.send(f"正在查询比赛({slug})\n请稍候...")

    try:
        matches = await client.list_matches()
    except Exception as e:
        await check_match.finish(f"由于API调度故障，请求失败：{e}")
        matches = []  # 哄类型检查器

    match, matched_by, team_hits = find_match(matches, slug, slug_case_sensitive=False)

    if match is None:
        await check_match.finish(f"未找到比赛：{slug}")

    match = cast(dict[str, Any], match)

    if matched_by == "team" and team_hits > 1:
        team_a, team_b = MatchParser.team_names(match)
        await check_match.send(
            f"⚠️ 未找到 slug「{slug}」，按战队名匹配到 {team_hits} 场比赛，"
            f"此处展示第一个：{team_a} vs {team_b}"
        )

    await check_match.finish(
        await typst_render(
            MatchParser.prerender_match(match)
        )
    )


@monitor_match.handle()
async def on_monitor_match(bot: Bot, session: Uninfo, slug: UniMessage):
    slug = slug.extract_plain_text().strip().lower()

    if not slug:
        await monitor_match.finish(
            "用法：monitor <slug/队名>\n"
            "取消监视：monitor cancel"
        )

    if session.scene.type != SceneType.GROUP:
        await monitor_match.finish("该命令仅限群聊使用。")

    client = cast(PandaScoreClient, panda_client)  # 获取API的HTTP客户端

    # 取消当前群全部比赛监视（没有监视任务时无需凭空创建一个）
    # 注意：取消监视不该被下面的主动消息权限检查拦住，所以放在检查之前
    if slug == "cancel":
        existing = monitor_clients.get(session.self_id)
        if existing is not None:
            existing.remove_monitor(group_id=session.scene.id)
        await monitor_match.finish("已取消本群全部比赛监视。")

    # 监视推送是"主动消息"：先确认本群允许机器人主动推送，不允许就不加订阅。
    # 查询不到（该接口仅白名单机器人可用）时按"无法判定"放行，由发送侧兜底。
    if await check_proactive_msg_permission(bot, session.scene.id) is False:
        await monitor_match.finish(
            "本群未开启「接收机器人主动推送」，无法添加比赛监视。\n"
            "请群主在群设置中允许机器人的主动消息后重试。"
        )

    try:
        matches = await client.list_matches()
    except Exception as e:
        await monitor_match.finish(f"由于API调度故障，请求失败：{e}")
        matches = []  # 哄类型检查器

    # 监视只挑还能打的比赛：list_matches 返回的是 past+running+upcoming，
    # 已结束的 past 排在最前面，不过滤的话按战队名搜索几乎必然命中一场打完的比赛。
    match, matched_by, team_hits = find_match(
        matches, slug, slug_case_sensitive=False, skip_terminal=True
    )

    if match is None:
        # 一场可监视的都没有时，再确认一次是不是这场已经结束/取消了，
        # 好给出"已结束"而不是笼统的"未找到"（纯内存扫描，不额外消耗API额度）
        stale, _, _ = find_match(matches, slug, slug_case_sensitive=False)
        if stale is not None:
            stale_name = stale.get("name") or stale.get("slug") or slug
            await monitor_match.finish(
                f"未找到可监视的比赛：{slug}\n"
                f"匹配到的「{stale_name}」已结束或已取消，无法监视。"
            )
        await monitor_match.finish(f"未找到比赛：{slug}")
    match = cast(dict[str, Any], match)

    # 统一以比赛真实 slug 作为监视键：队名后备命中时尤其关键，
    # 否则监视轮询按 slug 查找将永远找不到目标。
    # 注意，这里写的是get("slug") or slug而不是get("slug", slug)，因为这样能防止slug对应的值是None的情况
    monitor_slug = str(match.get("slug") or slug).lower()

    if matched_by == "team" and team_hits > 1:
        team_a, team_b = MatchParser.team_names(match)
        await monitor_match.send(
            f"⚠️ 未找到 slug「{slug}」，按战队名匹配到 {team_hits} 场可监视的比赛"
            f"（已略过已结束/已取消的），"
            f"将监视第一个：{team_a} vs {team_b}（slug: {monitor_slug}）"
        )

    monitor = get_monitor_client(session.self_id, client)

    # 记录比赛ID：列表接口窗口之外的比赛，监视轮询会用它直查，确证比赛是否还存在
    match_id = match.get("id")

    monitor.add_monitor(
        monitor_slug,
        session.scene.id,
        match_id if isinstance(match_id, int) else None,
    )
    await monitor_match.finish(f"已开始监视比赛：{match.get('name', monitor_slug)}")


@whitelist_config.handle()
async def on_whitelist_config(state: Query[str] = Query("state", default="")):
    arg = state.result.strip().lower()
    if arg not in ["on", "off"]:
        await whitelist_config.finish("命令用法：whitelist <on/off>")
    _config = cast(DynamicConfigSystem, dynamic_config)
    _config.config.priority_mode = PriorityMode.WhitelistOnly if arg == "on" else PriorityMode.WhitelistFirst
    await _config.save()
    await whitelist_config.finish(f"仅白名单赛事模式被设置为{'开启' if arg == 'on' else '关闭'}。")


@get_my_id.handle()
async def on_get_my_id(session: Uninfo):
    await get_my_id.finish(
        f"你的用户ID：{session.user.id}\n"
        f"当前场景ID：{session.scene.id}\n"
    )

