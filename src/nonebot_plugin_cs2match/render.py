# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from asyncio import to_thread
from hashlib import blake2s
from typing import Any, cast

import ayafileio
import typst

from nonebot import require, logger

# 必须先 require 再 import：直接 import 会让对应插件包"未作为插件加载"地进入
# sys.modules，之后插件入口里的 require(...) 会直接报错。
require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
from nonebot_plugin_alconna.uniseg import Image
from nonebot_plugin_localstore import get_plugin_cache_dir

from . import driver
from .cache import single_flight, RenderCache

RENDER_CACHE_DIR = get_plugin_cache_dir() / "render_cache"
RENDER_CACHE_DIR.mkdir(exist_ok=True)
STABLE_RENDER_CACHE_DIR = RENDER_CACHE_DIR / "stable_cache"
STABLE_RENDER_CACHE_DIR.mkdir(exist_ok=True)


render_cache: RenderCache | None = None


@driver.on_startup
async def _():
    global render_cache
    render_cache = await RenderCache.create(RENDER_CACHE_DIR / "ttl_render_cache", RENDER_CACHE_DIR / "metadata.db")
    assert render_cache is not None
    await render_cache.cleanup()


def typst_str(value: Any) -> str:
    """把任意值转成安全的 typst 字符串字面量，防止 API 数据破坏模板。"""
    return json.dumps(str(value), ensure_ascii=False)


@single_flight
async def typst_render_for_stable_cache(typst_content: str, index_key: str) -> Image:
    """
    专门用来解决help这种不容易变动的缓存
    话说这名字也太长了，谁想的
    """
    key = blake2s(typst_content.encode("utf-8"))
    file_name = f"{index_key}_{key.hexdigest()}.png"
    cache_file_path = STABLE_RENDER_CACHE_DIR / file_name

    if cache_file_path.exists() and cache_file_path.is_file():
        image_data = await ayafileio.read_bytes(cache_file_path)
        logger.debug(f"{index_key} 缓存命中")
        return Image(raw=image_data)

    all_expired_cache_file = [
        p for p in STABLE_RENDER_CACHE_DIR.iterdir()
        if p.is_file() and p.name.startswith(f"{index_key}_")
    ]

    if len(all_expired_cache_file) > 0:
        logger.debug(f"清理失效缓存 {len(all_expired_cache_file)} 个")

    for i in all_expired_cache_file:
        i.unlink()

    image_data = await to_thread(_typst_render, typst_content)

    await ayafileio.write_bytes(STABLE_RENDER_CACHE_DIR / file_name, image_data)

    logger.debug(f"{index_key} 缓存未命中")
    logger.debug(f"已创建 {STABLE_RENDER_CACHE_DIR / file_name} 缓存")

    return Image(raw=image_data)

@single_flight
async def typst_render(typst_content: str) -> Image:
    assert render_cache is not None

    cache_key = blake2s(typst_content.encode("utf-8")).digest()

    image_data = await render_cache.get_cache(cache_key)
    if image_data is None:
        image_data = await to_thread(_typst_render, typst_content)
        await render_cache.add_cache(cache_key, image_data)

    return Image(raw=image_data)

def _typst_render(typst_content: str) -> bytes:
    # 一般来说是不会输出多页的，所以干脆写个cast哄一下检查器了
    return cast(bytes, typst.compile(typst_content.encode(), format="png", ppi=144.0))
