# -*- coding: utf-8 -*-
"""
core/vault/identity_vault.py — 全平台账号身份档案库 (Identity Vault)
核心目标：
1. 解决注册后账号后续使用时的「IP 区域国家一致性 + 硬件指纹环境一致性 + Cookie/Token 完整还原」；
2. 规避因后续使用时 IP 跨国跳跃或硬件指纹突变引起的二次风控封号；
3. 为 AI Agent 提供标准化、机器可读的资产档案检索与恢复接口。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.db.base import _sqlite_conn

logger = logging.getLogger("turb.vault.identity")

_DEFAULT_SCREEN = [1920, 1080]


@dataclass
class IdentitySnapshot:
    """不可变身份快照模型，封装账号绑定的网络归属、硬件指纹与凭据全集。"""
    vault_id: str
    platform: str
    account_id: str  # email or username
    target_country: str = "US"
    driver_type: str = "camoufox"  # camoufox / patchright / bitbrowser / protocol
    fingerprint_seed: int = 0
    password: str = ""
    last_exit_ip: str = ""
    fingerprint_profile: Dict[str, Any] = field(default_factory=dict)
    cookies: List[Dict[str, Any]] | Dict[str, str] = field(default_factory=dict)
    tokens: Dict[str, str] = field(default_factory=dict)
    status: str = "active"  # active / suspended / cooldown
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IdentityVault:
    """全平台身份档案管理器。"""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self._conn = conn or _sqlite_conn()
        self._ensure_table()

    def _ensure_table(self) -> None:
        """确保 identity_vaults 数据表存在。"""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS identity_vaults (
                vault_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                target_country TEXT NOT NULL DEFAULT '',
                driver_type TEXT NOT NULL DEFAULT '',
                fingerprint_seed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_identity_platform_account
            ON identity_vaults (platform, account_id);
        """)
        self._conn.commit()

    def save_identity(self, snapshot: IdentitySnapshot) -> str:
        """持久化保存或更新身份档案。"""
        now_iso = datetime.now().isoformat()
        if not snapshot.created_at:
            snapshot.created_at = now_iso
        snapshot.updated_at = now_iso

        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        self._conn.execute("""
            INSERT INTO identity_vaults (
                vault_id, platform, account_id, target_country,
                driver_type, fingerprint_seed, status, created_at, updated_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vault_id) DO UPDATE SET
                target_country=excluded.target_country,
                driver_type=excluded.driver_type,
                fingerprint_seed=excluded.fingerprint_seed,
                status=excluded.status,
                updated_at=excluded.updated_at,
                payload=excluded.payload
        """, (
            snapshot.vault_id,
            snapshot.platform,
            snapshot.account_id,
            snapshot.target_country,
            snapshot.driver_type,
            snapshot.fingerprint_seed,
            snapshot.status,
            snapshot.created_at,
            snapshot.updated_at,
            payload,
        ))
        self._conn.commit()
        logger.info(f"[IdentityVault] 已固化档案: {snapshot.vault_id} ({snapshot.platform} / {snapshot.account_id} / {snapshot.target_country})")
        return snapshot.vault_id

    def get_identity(self, vault_id: str) -> Optional[IdentitySnapshot]:
        """通过 vault_id 查询单条身份档案。"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT payload FROM identity_vaults WHERE vault_id = ?", (vault_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return IdentitySnapshot(**data)

    def find_identity(self, platform: str, account_id: str) -> Optional[IdentitySnapshot]:
        """按平台与账号主键查找最新档案。"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT payload FROM identity_vaults WHERE platform = ? AND account_id = ? ORDER BY updated_at DESC LIMIT 1",
            (platform, account_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return IdentitySnapshot(**data)

    def list_identities(
        self,
        platform: Optional[str] = None,
        country: Optional[str] = None,
        status: Optional[str] = "active",
        limit: int = 100,
    ) -> List[IdentitySnapshot]:
        """按维度筛选检索账号档案列表。"""
        sql = "SELECT payload FROM identity_vaults WHERE 1=1"
        params: List[Any] = []
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        if country:
            sql += " AND target_country = ?"
            params.append(country)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        results = []
        for (payload,) in cursor.fetchall():
            try:
                results.append(IdentitySnapshot(**json.loads(payload)))
            except Exception as e:
                logger.warning(f"[IdentityVault] 解析 payload 异常: {e}")
        return results

    def restore_session_context(
        self,
        vault_id: str,
        force_fresh_proxy: bool = False,
    ) -> Dict[str, Any]:
        """
        恢复该账号的「出口国家一致性 + 硬件指纹一致性」会话上下文。
        返回值可直接传给 BrowserKit 或 DualSession 进行无风控复用。
        """
        snapshot = self.get_identity(vault_id)
        if not snapshot:
            raise KeyError(f"未找到指定的身份档案: {vault_id}")

        country = snapshot.target_country or "US"
        proxy_url = None

        # 1. 尝试从粘性网关分配与原档案相同国家的出口代理
        try:
            from core.continent_proxy_router import allocate_sticky_node
            proxy_url = allocate_sticky_node(
                target_country=country,
                force_fresh=force_fresh_proxy,
            )
        except Exception:
            pass

        # 若本地 Resin 动态网关可用，附带国家参数
        if not proxy_url:
            import os
            resin_host = os.environ.get("RESIN_GATEWAY", "127.0.0.1:2260")
            proxy_url = f"http://{resin_host}"

        # 2. 组装确定性硬件指纹参数
        fp = snapshot.fingerprint_profile or {}
        seed = snapshot.fingerprint_seed or int(time.time())

        return {
            "vault_id": snapshot.vault_id,
            "platform": snapshot.platform,
            "account_id": snapshot.account_id,
            "target_country": country,
            "proxy": proxy_url,
            "driver_type": snapshot.driver_type,
            "fingerprint_seed": seed,
            "user_agent": fp.get("user_agent", ""),
            "cookies": snapshot.cookies,
            "tokens": snapshot.tokens,
            "status": snapshot.status,
            "created_at": snapshot.created_at,
        }


# 全局单例
_default_vault: Optional[IdentityVault] = None


def get_identity_vault() -> IdentityVault:
    global _default_vault
    if _default_vault is None:
        _default_vault = IdentityVault()
    return _default_vault
