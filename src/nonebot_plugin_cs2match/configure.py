from __future__ import annotations
from enum import Enum
from pathlib import Path

import ayafileio
from pydantic import BaseModel

class PriorityMode(str, Enum):
    WhitelistOnly = "whitelist-only"
    WhitelistFirst = "whitelist-first"

class InProcessConfigure(BaseModel):
    priority_mode: PriorityMode = PriorityMode.WhitelistOnly

class Configure:
    def __init__(self, config: InProcessConfigure, path: Path):
        self.config = config
        self.path = path

    @classmethod
    async def from_path(cls, path: Path) -> Configure:
        async with ayafileio.open(path, "r", encoding="utf-8") as f:
            data = await f.readall()
        config = InProcessConfigure.model_validate_json(data)
        return Configure(config, path)

    @classmethod
    async def new(cls, path: Path) -> Configure:
        config = InProcessConfigure()
        async with ayafileio.open(path, "w", encoding="utf-8") as f:
            await f.write(config.model_dump_json())
        return Configure(config, path)

    async def save(self):
        async with ayafileio.open(self.path, "w", encoding="utf-8") as f:
            await f.write(self.config.model_dump_json())