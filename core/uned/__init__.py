"""
UNED (西班牙国立远程教育大学) 自动化注册独立模块
提供完整的单账号注册、批量无缝挂机注册、验证码自动识别、身份库管理
"""

from core.uned.registrar import UnedRegistrar, UnedRegistrationResult
from core.uned.captcha_solver import solve_uned_captcha
from core.uned.identities import load_or_scan_identities, pick_identity

__all__ = [
    "UnedRegistrar",
    "UnedRegistrationResult",
    "solve_uned_captcha",
    "load_or_scan_identities",
    "pick_identity"
]
