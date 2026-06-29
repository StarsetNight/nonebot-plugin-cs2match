# Copyright (c) 2023 StarsetNight
# SPDX-License-Identifier: MIT

from pydantic import BaseModel


class Config(BaseModel):
    """Plugin Config Here"""
    pandascore_token: str | None = None
