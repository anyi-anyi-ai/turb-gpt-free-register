# -*- coding: utf-8 -*-
"""
core/flows/context.py — 注册流水线统一上下文与结果契约。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class FlowContext:
    """注册流水线运行时上下文，携带单次注册任务的全流程状态。"""
    platform: str
    email: str = ""
    password: str = ""
    proxy: Optional[str] = None
    headless: bool = True
    db_path: Optional[Path | str] = None
    driver: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    extra_payload: Dict[str, Any] = field(default_factory=dict)
    start_time: float = 0.0


@dataclass
class RegistrationResult:
    """注册流水线执行结果标准契约。"""
    ok: bool
    email: str
    platform: str
    access_token: str = ""
    refresh_token: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    credits: int = 0
    status: str = "active"
    error: Optional[str] = None
    synced_to_gateway: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
