# -*- coding: utf-8 -*-
"""GitHub 专属凭据与命名生成器。"""
from __future__ import annotations

import random
import string

_GEEK_PREFIXES = [
    "kube", "cloud", "dev", "byte", "vector", "hyper", "flux",
    "pixel", "cyber", "kernel", "synth", "pulse", "mesh", "nexus",
    "echo", "zero", "rust", "sonic", "prism", "orbit", "vortex",
    "nova", "quantum", "tensor", "shadow", "apex", "spark"
]

_GEEK_WORDS = [
    "flow", "ops", "craft", "pilot", "node", "code", "link",
    "wave", "lens", "forge", "labs", "core", "run", "space", "beam",
    "base", "stack", "gate", "grid", "wire", "matrix"
]


def generate_geek_username(seed_str: str = "", attempt: int = 0) -> str:
    """遵循 GitHub 命名规则生成高可信度极客风格用户名。

    规则约束：
    1. 只能包含单字节字母、数字和单短横线。
    2. 不能以短横线开头或结尾。
    3. 不能出现连续两个短横线 (-- 是 GitHub 允许的，但单一横线视觉更自然)。
    4. 长度在 4-39 字符之间。
    5. 重试时追加微哈希递增保障唯一性。
    """
    prefix = random.choice(_GEEK_PREFIXES)
    word = random.choice(_GEEK_WORDS)
    suffix = "".join(random.choices(string.hexdigits.lower(), k=3 + (1 if attempt > 0 else 0)))
    if attempt > 1:
        suffix += str(random.randint(10, 99))
    return f"{prefix}-{word}-{suffix}"


def generate_strong_password(length: int = 16) -> str:
    """生成包含大小写、数字及安全特殊字符的高强度密码。

    满足 GitHub 至少 15 位长，或至少 8 位且包含数字和小写字母的安全标准。
    """
    specials = "!@#$%^&*"
    chars = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice(specials),
    ]
    all_pool = string.ascii_letters + string.digits + specials
    chars.extend(random.choices(all_pool, k=length - 4))
    random.shuffle(chars)
    return "".join(chars)
