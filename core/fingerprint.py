# -*- coding: utf-8 -*-
"""CloakBrowser 账号专属指纹种子派生与环境隔离工具库。

基于「确定性指纹 (Deterministic Fingerprint)」原理：
1. 为每个账号分配/派生终身恒定的 16 位 16 进制指纹种子（PRNG Seed）。
2. 在注册、养号、查活、Codex 授权等各个生命周期阶段，向 CloakBrowser 内核
   注入相同的 `--fingerprint={seed}` 参数，确保 Canvas、WebGL、AudioContext、
   字体特征等底层硬件指纹 100% 保持一致。
3. 纯内存/计算派生与元数据绑定，不生成膨胀的本地 Profile 文件夹，彻底规避文件锁与磁盘开销。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SEED_SALT = "cloak"


def derive_account_fingerprint_seed(email: str | None = None, salt: str = DEFAULT_SEED_SALT) -> str:
    """基于邮箱生成确定性指纹种子；若未指定邮箱则生成高熵随机种子。

    Args:
        email: 账号邮箱（大小写不敏感，会自动 trim）
        salt: 派生盐值，默认 "cloak"

    Returns:
        16 位 16 进制字符串，例如 "a3f89e210b45cd67"
    """
    clean_email = str(email or "").strip().lower()
    if clean_email:
        raw_text = f"{salt}:{clean_email}"
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    return secrets.token_hex(8)


def resolve_account_fingerprint_seed(
    account: dict[str, Any] | None = None,
    email: str | None = None,
    salt: str = DEFAULT_SEED_SALT,
) -> str:
    """从已存账号数据或邮箱中解析出专属的指纹种子。

    解析优先级：
    1. account["extra"]["fingerprint_seed"] 或 account["extra"]["cloakbrowser"]["fingerprint_seed"]
    2. account["fingerprint_seed"]
    3. account["profile_id"]（若形如 "cloak_xxxx"）
    4. 根据 email（或 account["email"]）使用确定性算法自动派生
    5. 若均无法解析，生成高熵随机种子

    Args:
        account: 账号字典或 ORM 字典
        email: 显式邮箱（若提供则优先用于推导）
        salt: 派生盐值

    Returns:
        16 位 16 进制指纹种子
    """
    if account and isinstance(account, dict):
        # 1. 直接字段
        direct_seed = str(account.get("fingerprint_seed") or "").strip()
        if direct_seed:
            return direct_seed

        # 2. 从 extra / extra_json 字典中查找
        extra_data = account.get("extra")
        if not isinstance(extra_data, dict):
            extra_raw = account.get("extra_json")
            if isinstance(extra_raw, str) and extra_raw.strip():
                try:
                    extra_data = json.loads(extra_raw)
                except Exception:
                    extra_data = {}
            elif isinstance(extra_raw, dict):
                extra_data = extra_raw
            else:
                extra_data = {}

        if isinstance(extra_data, dict):
            seed_in_extra = str(extra_data.get("fingerprint_seed") or "").strip()
            if seed_in_extra:
                return seed_in_extra
            cloak_meta = extra_data.get("cloakbrowser")
            if isinstance(cloak_meta, dict):
                seed_in_cloak = str(cloak_meta.get("fingerprint_seed") or "").strip()
                if seed_in_cloak:
                    return seed_in_cloak

        # 3. 检查 profile_id 是否包含 seed
        pid = str(account.get("profile_id") or "").strip()
        if pid.startswith("cloak_") and len(pid) > 6:
            candidate = pid[6:].strip()
            if candidate and candidate != "profile":
                return candidate

        # 4. 从账号内的 email 推导
        if not email:
            email = account.get("email")

    clean_email = str(email or "").strip().lower()
    if clean_email:
        return derive_account_fingerprint_seed(clean_email, salt=salt)

    return secrets.token_hex(8)
