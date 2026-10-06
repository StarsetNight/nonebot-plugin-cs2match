# Copyright (c) 2026 StarsetNight, XuanRikka
# SPDX-License-Identifier: MIT

from __future__ import annotations
from enum import Enum
from pathlib import Path

import ayafileio
from pydantic import BaseModel

class PriorityMode(str, Enum):
    WhitelistOnly = "whitelist-only"
    WhitelistFirst = "whitelist-first"

class DynamicConfig(BaseModel):
    priority_mode: PriorityMode = PriorityMode.WhitelistOnly

class DynamicConfigSystem:
    def __init__(self, config: DynamicConfig, path: Path):
        self.config = config
        self.path = path

    @classmethod
    async def from_path(cls, path: Path) -> DynamicConfigSystem:
        data = await ayafileio.read_text(path, encoding="utf-8")
        config = DynamicConfig.model_validate_json(data)
        return DynamicConfigSystem(config, path)

    @classmethod
    async def new(cls, path: Path) -> DynamicConfigSystem:
        config = DynamicConfig()
        await ayafileio.write_text(path, config.model_dump_json(), encoding="utf-8")
        return DynamicConfigSystem(config, path)

    async def save(self):
        await ayafileio.write_text(self.path, self.config.model_dump_json(), encoding="utf-8")