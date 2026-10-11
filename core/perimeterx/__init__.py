# -*- coding: utf-8 -*-
"""PerimeterX / HUMAN Anti-Bot Bypass & Clearance Engine

深度迁移与集成：
1. references/captcha-solver/perimeterx (Site-agnostic Core + Renderers)
2. SHA-256 hashcash PoW in Web Worker + sensor-VM biomechanics scoring
3. Clearance Cookie Bundle Harvesting (_px3, _pxvid, _pxde, pxcts) & Replay Contract
4. 生物微颤 (Micro-tremor) 物理按压与现场即时解密
"""

from .detector import detect_perimeterx_gate, is_perimeterx_visible
from .outlook_solver import (
    solve_perimeterx_gate,
    harvest_perimeterx_cookies,
    PerimeterXSolution,
)

__all__ = [
    "detect_perimeterx_gate",
    "is_perimeterx_visible",
    "solve_perimeterx_gate",
    "harvest_perimeterx_cookies",
    "PerimeterXSolution",
]
