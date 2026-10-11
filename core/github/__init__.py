# -*- coding: utf-8 -*-
"""
GitHub 独立领域服务包 (core.github)。

导出核心接口：
- init_github_table: 初始化数据库表
- load_pending_accounts: 加载待注册邮箱
- save_github_account: 持久化账号
- register_github_account_session: 单号注册会话编排
- probe_github_account_quality: 质量自检探针
- generate_geek_username: 极客用户名生成
- generate_strong_password: 强密码生成
- fetch_github_launch_code: 验证码收信
"""
from __future__ import annotations

from .credentials import generate_geek_username, generate_strong_password
from .db import init_github_table, load_pending_accounts, save_github_account
from .flow import (
    fill_signup_form,
    wait_for_arkose_and_takeover,
    inject_verification_code,
    skip_onboarding_guide,
)
from .otp import extract_github_code_from_message, fetch_github_launch_code
from .probe import probe_github_account_quality
from .service import register_github_account_session

__all__ = [
    "init_github_table",
    "load_pending_accounts",
    "save_github_account",
    "register_github_account_session",
    "probe_github_account_quality",
    "generate_geek_username",
    "generate_strong_password",
    "fetch_github_launch_code",
    "extract_github_code_from_message",
    "fill_signup_form",
    "wait_for_arkose_and_takeover",
    "inject_verification_code",
    "skip_onboarding_guide",
]
