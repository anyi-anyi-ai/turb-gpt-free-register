# -*- coding: utf-8 -*-
"""
core/agent_bridge.py — Agent 原生调度与流式监控桥接中枢 (Agent Bridge)
为 AI Agent (如 Antigravity, AutoGen, CrewAI 等) 提供：
1. 机器可读的 NDJSON (Newline Delimited JSON) 逐行实时事件流；
2. 结构化自愈错误码 (Actionable Error Protocol)；
3. 全局生命周期事件分发订阅机制。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("turb.agent_bridge")


@dataclass
class ActionableError:
    """结构化自愈错误契约，为 Agent 提供即刻决策依据与重试动作建议。"""
    error_code: str  # 错误大类代号
    message: str     # 人类可读描述
    recovery_action: str  # 动作指示: rotate_country_proxy / switch_to_camoufox / retry_mailbox / captcha_fallback
    suggested_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentStreamBroadcaster:
    """全局流式事件发射器，支持向标准输出广播 NDJSON 事件，并通知已注册的回调。"""

    def __init__(self):
        self._streaming_enabled: bool = False
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

    def enable_streaming(self, enabled: bool = True) -> None:
        self._streaming_enabled = enabled

    def is_streaming(self) -> bool:
        return self._streaming_enabled

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def emit(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        """发射结构化事件。"""
        event = {
            "timestamp": int(time.time()),
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event_type,
            **payload,
        }

        # 1. 若处于 --stream 模式，向 stdout 打印纯单行 NDJSON
        if self._streaming_enabled:
            try:
                line = json.dumps(event, ensure_ascii=False)
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except Exception:
                pass

        # 2. 触发内存注册的 Python 回调（用于 SDK 异步监听）
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception as e:
                logger.debug(f"[AgentBridge] 监听器执行异常: {e}")

        return event

    def emit_error(
        self,
        error_code: str,
        message: str,
        recovery_action: str,
        platform: str = "",
        email: str = "",
        suggested_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """便捷派发标准化可自愈错误事件。"""
        action_error = ActionableError(
            error_code=error_code,
            message=message,
            recovery_action=recovery_action,
            suggested_params=suggested_params or {},
        )
        return self.emit(
            "job_failed",
            platform=platform,
            email=email,
            actionable_error=action_error.to_dict(),
        )


# 全局单例
stream_broadcaster = AgentStreamBroadcaster()
