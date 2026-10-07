# -*- coding: utf-8 -*-
"""Grok 账号自动化注册调度服务。

基于 CloakBrowser（Playwright）驱动，调用 GrokSignupFlow 与 GrokQualityChecker，
打通 TURB 统一邮箱池，实现“取号 -> 页面自动化 -> 过CF/填资料 -> 提取SSO -> 两级初生自检 -> 入库”。
"""
from __future__ import annotations

import json
import logging
import random
import string
import threading
import time
from typing import Any, Callable

from core import db
from core.cloakbrowser_driver import build_cloak_driver
from core.email_provider import acquire_email_after_input, wait_for_otp
from .flow import (
    GrokSignupFlow,
    GrokSignupError,
    GrokEmailDomainRejectedError,
    GrokAccountAlreadyExistsError,
    GrokSignInFlow,
    GrokLoginError,
    GrokAccountBannedError,
)
from .quality_checker import GrokQualityChecker, GrokQualityResult

logger = logging.getLogger(__name__)


def generate_strong_password(length: int = 14) -> str:
    """生成符合 Grok 规范的强密码（大小写字母+数字+特殊符号）。"""
    lower = random.choice(string.ascii_lowercase)
    upper = random.choice(string.ascii_uppercase)
    digit = random.choice(string.digits)
    punct = random.choice("@#$%^&*!")
    remaining = "".join(random.choices(string.ascii_letters + string.digits + "@#$%^&*!", k=length - 4))
    pwd_list = list(lower + upper + digit + punct + remaining)
    random.shuffle(pwd_list)
    return "".join(pwd_list)


def register_single_grok_account(
    *,
    proxy: str | None = None,
    country: str | None = None,
    max_email_retries: int = 40,
    run_quality_probe: bool = True,
    enable_device_oauth: bool = True,
    headless: bool | None = None,
    log_callback: Callable[[str], None] | None = None,
    stop_checker: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """执行单个 Grok 账号的端到端注册、OAuth凭据交换与初生自检流程。"""
    def log(msg: str):
        if log_callback:
            log_callback(f"[GrokReg] {msg}")
        logger.info(f"[GrokReg] {msg}")

    attempt = 0
    driver = None
    effective_proxy = proxy.strip() if (proxy and isinstance(proxy, str) and proxy.strip()) else None
    if not effective_proxy:
        try:
            from config.proxy import pick_proxy
            picked = pick_proxy(country=country, for_registration=True)
            if picked:
                if ":2260" in picked:
                    import socket
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.15)
                        if s.connect_ex(("127.0.0.1", 2260)) == 0:
                            effective_proxy = picked
                            log(f"按国家分流权重自动选用代理节点: {effective_proxy}")
                else:
                    effective_proxy = picked
                    log(f"按国家分流权重自动选用代理节点: {effective_proxy}")
        except Exception:
            effective_proxy = None

        if not effective_proxy:
            try:
                import socket
                for p in (7890, 7897):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.15)
                        if s.connect_ex(("127.0.0.1", p)) == 0:
                            effective_proxy = f"http://127.0.0.1:{p}"
                            log(f"自动选用本地就绪出口代理: {effective_proxy}")
                            break
            except Exception:
                pass

    # 代理隧道连通性守护：严格验证出口代理能否握手 accounts.x.ai:443
    if effective_proxy and ":2260" in effective_proxy:
        try:
            from core.continent_proxy_router import check_proxy_tunnel_alive
            if not check_proxy_tunnel_alive(effective_proxy, target_host="accounts.x.ai", target_port=443, timeout=3.5):
                log(f"代理 {effective_proxy} 隧道探测未就绪，重新换选同国新鲜会话...")
                for _attempt_t in range(4):
                    from config.proxy import pick_proxy
                    new_proxy = pick_proxy(country=country, for_registration=True)
                    if new_proxy and check_proxy_tunnel_alive(new_proxy, target_host="accounts.x.ai", target_port=443, timeout=3.5):
                        effective_proxy = new_proxy
                        log(f"已成功轮换至可用代理出口: {effective_proxy}")
                        break
        except Exception as _tunnel_err:
            log(f"代理隧道守护探测提示: {_tunnel_err}")

    try:
        log("正在启动 CloakBrowser 指纹浏览器实例...")
        driver, open_res = build_cloak_driver(proxy=effective_proxy, country=country, headless=headless)
        actual_proxy = getattr(driver, "_proxy_url", "") or (effective_proxy or "")
        if actual_proxy:
            log(f"当前实例出口代理: {actual_proxy}")
        page = getattr(driver, "page", None)
        if page is None:
            raise RuntimeError("Cloak driver 缺少 page 实例")

        flow = GrokSignupFlow(page, log_callback=log)

        while attempt < max_email_retries:
            if stop_checker and stop_checker():
                log("检测到外部停止信号，终止注册")
                return {"success": False, "error": "任务被手动终止"}

            attempt += 1
            log(f"正在从 TURB 邮箱池获取可用注册邮箱 (尝试 {attempt}/{max_email_retries})...")
            email = acquire_email_after_input(service="grok")
            if not email:
                raise RuntimeError("TURB 邮箱池已耗尽或未配置可用邮箱来源")

            log(f"已分配注册邮箱: {email}")

            try:
                # 1. 打开注册页
                flow.open_signup_page()
                # 2. 点击使用邮箱注册
                flow.click_email_signup_button()
                # 3. 填入邮箱并提交
                submit_ts = time.time()
                flow.fill_email_and_submit(email)
                # 4. 确认进入验证码界面
                flow.wait_for_otp_page(email=email)
                # 5. 等待收取验证码邮件
                log(f"正在等待收取发往 {email} 的验证码邮件...")
                otp_code = wait_for_otp(email, after_ts=submit_ts, max_wait=120)
                if not otp_code:
                    raise GrokSignupError(f"收取邮箱验证码超时: {email}")

                log(f"已成功收到验证码: {otp_code}")
                # 6. 回填验证码
                flow.fill_code_and_submit(otp_code, email=email)
                # 7. 生成密码并填写个人资料
                password = generate_strong_password()
                flow.fill_profile_and_submit(password=password, email=email)
                # 8. 等待并提取 SSO Cookie
                sso_token, sso_rw = flow.wait_for_sso_cookie(timeout_sec=55)

                log(f"Grok 注册成功！提取到 SSO Token 前缀: {sso_token[:15]}...")

                # 9. 执行官方 OAuth Device Code 授权 (换取 CPA / Grok2API 专属持久 Token)
                oauth_token = None
                cpa_record = {}
                refresh_token = ""
                access_token = ""
                token_expires_at = ""

                if enable_device_oauth:
                    try:
                        from .auth_exchange import request_device_code, poll_device_token, token_to_cpa_record
                        log("正在发起 OAuth Device Flow 授权流程...")
                        device_info = request_device_code(proxy=actual_proxy, log_callback=log)
                        if device_info and device_info.get("user_code"):
                            # 浏览器打开 Device 授权页并点击允许
                            approved = flow.authorize_device_in_browser(
                                user_code=device_info["user_code"],
                                open_url=device_info["open_url"],
                                timeout_sec=40,
                            )
                            if approved:
                                oauth_token = poll_device_token(
                                    device_code=device_info["device_code"],
                                    interval=device_info.get("interval", 5),
                                    expires_in=device_info.get("expires_in", 1800),
                                    proxy=actual_proxy,
                                    log_callback=log,
                                    stop_checker=stop_checker,
                                )
                                if oauth_token:
                                    access_token = oauth_token.get("access_token", "")
                                    refresh_token = oauth_token.get("refresh_token", "")
                                    cpa_record = token_to_cpa_record(oauth_token, email=email, sso=sso_token)
                                    token_expires_at = cpa_record.get("expired", "")
                                    log("OAuth Device 授权成功，已换取到持久 Refresh Token 与 CPA 记录！")
                    except Exception as oauth_exc:
                        log(f"OAuth Device 授权流程提示 (保持已获取的 SSO 凭据): {oauth_exc}")

                # 10. 执行初生质量自检（降智防护与多维度探针）
                checker = GrokQualityChecker(proxy=actual_proxy)
                quality_res: GrokQualityResult = checker.check(
                    sso_token,
                    cpa_record=cpa_record,
                    run_probe=run_quality_probe,
                )

                log(f"初生自检完成: 状态={quality_res.status}, TPS={quality_res.probe_tps}, ReasoningTokens={quality_res.probe_reasoning_tokens}")
                if quality_res.reasons:
                    log(f"自检详情: {'; '.join(quality_res.reasons)}")

                # 11. 写入数据库
                account_id = db.insert_grok_account(
                    email=email,
                    password=password,
                    sso_token=sso_token,
                    sso_rw=sso_rw,
                    refresh_token=refresh_token,
                    access_token=access_token,
                    token_expires_at=token_expires_at,
                    cpa_record=json.dumps(cpa_record, ensure_ascii=False) if cpa_record else "",
                    status=quality_res.status,
                    bot_flag_source=quality_res.bot_flag_source,
                    bot_flag_details=quality_res.bot_flag_details,
                    probe_tps=quality_res.probe_tps,
                    probe_reasoning_tokens=quality_res.probe_reasoning_tokens,
                    proxy_used=actual_proxy,
                    extra={
                        "reasons": quality_res.reasons,
                        "raw_user": quality_res.raw_user,
                        "has_oauth": bool(oauth_token),
                    },
                )
                log(f"Grok 账号已保存至数据库 (ID={account_id}, Status={quality_res.status})")

                # 记录 IP 配额使用成功
                try:
                    from core.ip_quota_manager import ip_quota_manager
                    ip_quota_manager.record_registration_success(actual_proxy, email=email, country=country)
                except Exception:
                    pass

                # 12. 触发 GrokIQ Outbox 异步入队
                try:
                    import uuid
                    from .grokiq_client import enqueue_grok_event
                    event_id = f"evt_{uuid.uuid4().hex[:16]}"
                    enqueue_grok_event(
                        account_id=account_id,
                        email=email,
                        sso=sso_token,
                        status=quality_res.status,
                        bfs=quality_res.bot_flag_source,
                        tps=quality_res.probe_tps,
                    )
                except Exception:
                    pass

                return {
                    "success": True,
                    "account_id": account_id,
                    "email": email,
                    "password": password,
                    "sso_token": sso_token,
                    "refresh_token": refresh_token,
                    "access_token": access_token,
                    "status": quality_res.status,
                    "quality": quality_res,
                    "has_oauth": bool(oauth_token),
                }

            except GrokEmailDomainRejectedError as exc:
                log(f"邮箱域名被拒绝: {exc}，将标记此邮箱并更换下一个...")
                try:
                    db.insert_grok_account(
                        email=email,
                        password="",
                        sso_token="",
                        status="domain_rejected",
                        extra={"note": str(exc)},
                    )
                except Exception:
                    pass
                continue
            except GrokAccountAlreadyExistsError as exc:
                log(f"邮箱已在 Grok 注册过 ({email}): {exc}，已记录到库中并自动更换下一个邮箱...")
                try:
                    db.insert_grok_account(
                        email=email,
                        password="",
                        sso_token="",
                        status="already_registered",
                        extra={"note": "注册时 Grok 提示该邮箱已被注册"},
                    )
                except Exception:
                    pass
                continue

        raise RuntimeError(f"连续 {max_email_retries} 次尝试由于域名或邮箱原因失败")

    finally:
        if driver is not None:
            try:
                log("正在关闭 CloakBrowser 浏览器并清理会话...")
                driver.quit()
            except Exception:
                pass


class GrokRegistrationManager:
    """Grok 批量注册并发调度管理器"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._is_running = False
                cls._instance._threads = []
                cls._instance._stop_event = threading.Event()
                cls._instance._data_lock = threading.RLock()
                cls._instance._logs = []
                cls._instance._log_listeners = []
                cls._instance._results = []
                cls._instance._stats = {"total": 0, "success": 0, "failed": 0, "degraded": 0}
            return cls._instance

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_status(self) -> dict[str, Any]:
        with self._data_lock:
            return {
                "is_running": self._is_running,
                "stats": dict(self._stats),
                "recent_logs": list(self._logs[-50:]),
                "results": list(self._results),
            }

    def append_log(self, msg: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        with self._data_lock:
            self._logs.append(entry)
            if len(self._logs) > 400:
                self._logs = self._logs[-400:]
            listeners = list(self._log_listeners)
        for listener in listeners:
            try:
                listener(entry)
            except Exception:
                pass

    def add_log_listener(self, listener: Callable[[str], None]) -> None:
        with self._data_lock:
            if listener not in self._log_listeners:
                self._log_listeners.append(listener)

    def remove_log_listener(self, listener: Callable[[str], None]) -> None:
        with self._data_lock:
            if listener in self._log_listeners:
                self._log_listeners.remove(listener)

    def clear_logs(self) -> None:
        with self._data_lock:
            self._logs.clear()

    def stop(self) -> None:
        if self._is_running:
            self.append_log("[Manager] 正在向所有活跃注册 Worker 发送终止指令...")
            self._stop_event.set()

    def start_batch(
        self,
        count: int = 5,
        concurrency: int = 1,
        proxy: str = "",
        countries: list[str] | None = None,
        sync_to_downstream: bool = True,
        run_quality_probe: bool = True,
        enable_device_oauth: bool = True,
        headless: bool | None = None,
    ) -> bool:
        with self._lock:
            if self._is_running:
                return False
            self._is_running = True
            self._stop_event.clear()
            with self._data_lock:
                self._stats = {"total": count, "success": 0, "failed": 0, "degraded": 0}
                self._logs.clear()
                self._results.clear()

        # 计算国家轮换池
        DEFAULT_ROTATION = ["US", "CA", "DE", "FR", "NL", "JP", "SG"]
        if countries:
            rotation_countries = [c.strip().upper() for c in countries if c.strip()]
        else:
            try:
                from config.proxy import get_available_countries
                configured = get_available_countries()
                rotation_countries = [c for c in DEFAULT_ROTATION if c in configured] or ["US"]
            except Exception:
                rotation_countries = DEFAULT_ROTATION
        if not rotation_countries:
            rotation_countries = ["US"]

        safe_concurrency = max(1, min(int(concurrency or 1), 10, count))
        self.append_log(
            f"[Manager] 批量 Grok 并发注册调度器已接收任务: 目标={count}个, 并发Worker={safe_concurrency}, "
            f"国家轮换池={', '.join(rotation_countries)}, 探针自检={'开启' if run_quality_probe else '关闭'}, "
            f"OAuth授权={'开启' if enable_device_oauth else '关闭'}, 下游同步={'开启' if sync_to_downstream else '关闭'}"
        )
        self.append_log(f"[Manager] 正在初始化并发协调器与启动 Worker 线程池...")

        def _coordinator():
            job_counter_lock = threading.Lock()
            current_job = 0
            workers = []

            def _worker_thread(worker_id: int):
                nonlocal current_job
                while not self._stop_event.is_set():
                    with job_counter_lock:
                        if current_job >= count:
                            break
                        current_job += 1
                        job_num = current_job

                    country_for_job = rotation_countries[(job_num - 1) % len(rotation_countries)]
                    effective_proxy = proxy.strip() if (proxy and proxy.strip()) else None
                    if not effective_proxy:
                        try:
                            from config.proxy import pick_proxy
                            effective_proxy = pick_proxy(country=country_for_job, for_registration=True)
                        except Exception:
                            effective_proxy = None

                    self.append_log(
                        f"[Worker-{worker_id}] 开始执行第 {job_num}/{count} 个 Grok 账号注册任务 "
                        f"(目标国家: {country_for_job}, 代理: {effective_proxy or '自动'})..."
                    )
                    try:
                        res = register_single_grok_account(
                            proxy=effective_proxy,
                            country=country_for_job,
                            run_quality_probe=run_quality_probe,
                            enable_device_oauth=enable_device_oauth,
                            headless=headless,
                            log_callback=lambda m: self.append_log(f"[Worker-{worker_id}] {m}"),
                            stop_checker=lambda: self._stop_event.is_set(),
                        )
                        res["target_country"] = country_for_job
                        res["proxy_used"] = effective_proxy

                        if res.get("success"):
                            with self._data_lock:
                                self._stats["success"] += 1
                                if res.get("status") == "degraded":
                                    self._stats["degraded"] += 1
                            self.append_log(
                                f"[Worker-{worker_id}] 账号 {res.get('email')} 注册成功并入库 "
                                f"(国家: {country_for_job}, 状态: {res.get('status')})"
                            )

                            if sync_to_downstream:
                                try:
                                    from core.gptgrok2api_sync import push_grok_accounts_to_gptgrok2api
                                    sync_payload = [{
                                        "id": res.get("account_id"),
                                        "email": res.get("email"),
                                        "password": res.get("password"),
                                        "sso_token": res.get("sso_token"),
                                        "refresh_token": res.get("refresh_token"),
                                        "access_token": res.get("access_token"),
                                        "status": res.get("status"),
                                        "country": country_for_job,
                                        "proxy_used": effective_proxy,
                                    }]
                                    sync_res = push_grok_accounts_to_gptgrok2api(sync_payload)
                                    res["sync_result"] = sync_res
                                    self.append_log(
                                        f"[Worker-{worker_id}] [SYNC] 下游 gptGrok2api 同步结果: "
                                        f"ok={sync_res.get('ok')}, added={sync_res.get('data', {}).get('added', 0)}"
                                    )
                                except Exception as sync_err:
                                    res["sync_error"] = str(sync_err)
                                    self.append_log(f"[Worker-{worker_id}] [SYNC ERROR] 下游同步异常: {sync_err}")
                        else:
                            with self._data_lock:
                                self._stats["failed"] += 1
                            self.append_log(f"[Worker-{worker_id}] 注册任务未完成: {res.get('error')}")

                        with self._data_lock:
                            self._results.append(res)

                    except Exception as exc:
                        import traceback
                        tb_lines = traceback.format_exc().strip().splitlines()
                        err_summary = tb_lines[-1] if tb_lines else str(exc)
                        with self._data_lock:
                            self._stats["failed"] += 1
                            self._results.append({
                                "success": False,
                                "error": err_summary,
                                "target_country": country_for_job,
                                "proxy_used": effective_proxy,
                            })
                        self.append_log(f"[Worker-{worker_id}] 注册执行异常: {err_summary}")

                    # 线程间轻微错峰休眠
                    time.sleep(1)

            for wid in range(1, safe_concurrency + 1):
                t = threading.Thread(target=_worker_thread, args=(wid,), daemon=True, name=f"grok-reg-worker-{wid}")
                workers.append(t)
                t.start()
                if wid < safe_concurrency:
                    time.sleep(1)

            for t in workers:
                t.join()

            self._is_running = False
            self.append_log(
                f"[Manager] Grok 批量注册任务已全部结束: 成功={self._stats['success']}, "
                f"降智={self._stats['degraded']}, 失败={self._stats['failed']}"
            )

        coord = threading.Thread(target=_coordinator, daemon=True, name="grok-reg-coordinator")
        coord.start()
        return True


# 全局单例
grok_reg_manager = GrokRegistrationManager()


def recover_single_grok_account(
    email: str,
    *,
    proxy: str | None = None,
    country: str | None = None,
    run_quality_probe: bool = True,
    enable_device_oauth: bool = True,
    headless: bool | None = None,
    log_callback: Callable[[str], None] | None = None,
    stop_checker: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """对已存在的/已注册的 Grok 账号执行免密验证码登录与凭据恢复。"""
    def log(msg: str):
        if log_callback:
            log_callback(f"[GrokRecover] {msg}")
        logger.info(f"[GrokRecover] {msg}")

    normalized_email = str(email or "").strip()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError(f"无效的恢复邮箱: {email}")

    driver = None
    effective_proxy = proxy.strip() if (proxy and isinstance(proxy, str) and proxy.strip()) else None
    if not effective_proxy:
        try:
            from config.proxy import pick_proxy
            picked = pick_proxy(country=country, for_registration=True)
            if picked:
                effective_proxy = picked
                log(f"自动选用代理节点: {effective_proxy}")
        except Exception:
            effective_proxy = None

    # 代理隧道连通性守护
    if effective_proxy and ":2260" in effective_proxy:
        try:
            from core.continent_proxy_router import check_proxy_tunnel_alive
            if not check_proxy_tunnel_alive(effective_proxy, target_host="accounts.x.ai", target_port=443, timeout=3.5):
                log(f"代理 {effective_proxy} 隧道探测未就绪，重新换选可用节点...")
                for _ in range(4):
                    from config.proxy import pick_proxy
                    new_proxy = pick_proxy(country=country, for_registration=True)
                    if new_proxy and check_proxy_tunnel_alive(new_proxy, target_host="accounts.x.ai", target_port=443, timeout=3.5):
                        effective_proxy = new_proxy
                        log(f"已成功轮换至可用代理出口: {effective_proxy}")
                        break
        except Exception as _tunnel_err:
            log(f"代理隧道守护探测提示: {_tunnel_err}")

    try:
        log(f"正在启动 CloakBrowser 恢复实例: {normalized_email} ...")
        driver, _ = build_cloak_driver(proxy=effective_proxy, country=country, headless=headless)
        actual_proxy = getattr(driver, "_proxy_url", "") or (effective_proxy or "")
        page = getattr(driver, "page", None)
        if page is None:
            raise RuntimeError("Cloak driver 缺少 page 实例")

        flow = GrokSignInFlow(page, log_callback=log)

        # 1. 打开登录页
        flow.open_signin_page()
        # 2. 点击「使用邮箱登录」
        flow.click_email_signin_button()
        # 3. 填入邮箱并点击「Next」
        flow.fill_email_and_next(normalized_email)
        # 4. 点击「Email sign-in code」
        send_ts = flow.request_email_signin_code()
        # 5. 等待收取验证码
        log(f"正在等待收取发往 {normalized_email} 的登录验证码...")
        otp_code = wait_for_otp(normalized_email, after_ts=send_ts, max_wait=90)
        if not otp_code:
            raise GrokLoginError(f"收取邮箱登录验证码超时: {normalized_email}")

        log(f"已成功获取登录验证码: {otp_code}，正在回填并提交...")
        # 6. 回填验证码并提交
        flow.submit_signin_code(otp_code)
        # 7. 等待并提取 SSO Cookie
        sso_token, sso_rw = flow.wait_for_sso_cookie(timeout_sec=55)
        log(f"Grok 免密登录恢复成功！提取到 SSO Token 前缀: {sso_token[:15]}...")

        # 8. 执行官方 OAuth Device Code 授权
        oauth_token = None
        cpa_record = {}
        refresh_token = ""
        access_token = ""
        token_expires_at = ""

        if enable_device_oauth:
            try:
                from .auth_exchange import request_device_code, poll_device_token, token_to_cpa_record
                log("正在发起 OAuth Device Flow 授权流程...")
                device_info = request_device_code(proxy=actual_proxy, log_callback=log)
                if device_info and device_info.get("user_code"):
                    approved = flow.authorize_device_in_browser(
                        user_code=device_info["user_code"],
                        open_url=device_info["open_url"],
                        timeout_sec=40,
                    )
                    if approved:
                        oauth_token = poll_device_token(
                            device_code=device_info["device_code"],
                            interval=device_info.get("interval", 5),
                            expires_in=device_info.get("expires_in", 1800),
                            proxy=actual_proxy,
                            log_callback=log,
                            stop_checker=stop_checker,
                        )
                        if oauth_token:
                            access_token = oauth_token.get("access_token", "")
                            refresh_token = oauth_token.get("refresh_token", "")
                            cpa_record = token_to_cpa_record(oauth_token, email=normalized_email, sso=sso_token)
                            token_expires_at = cpa_record.get("expired", "")
                            log("OAuth Device 授权成功，已换取到持久 Refresh Token 与 CPA 记录！")
            except Exception as oauth_exc:
                log(f"OAuth Device 授权提示 (保留已获取的 SSO 凭据): {oauth_exc}")

        # 9. 执行质量自检
        checker = GrokQualityChecker(proxy=actual_proxy)
        quality_res: GrokQualityResult = checker.check(
            sso_token,
            cpa_record=cpa_record,
            run_probe=run_quality_probe,
        )

        log(f"自检完成: 状态={quality_res.status}, TPS={quality_res.probe_tps}")

        # 10. 保存至数据库
        existing_acc = db.get_grok_account_by_email(normalized_email)
        pwd = existing_acc.get("password", "") if existing_acc else ""
        if not pwd:
            pwd = generate_strong_password()

        account_id = db.insert_grok_account(
            email=normalized_email,
            password=pwd,
            sso_token=sso_token,
            sso_rw=sso_rw,
            refresh_token=refresh_token,
            access_token=access_token,
            token_expires_at=token_expires_at,
            cpa_record=json.dumps(cpa_record, ensure_ascii=False) if cpa_record else "",
            status=quality_res.status,
            bot_flag_source=quality_res.bot_flag_source,
            bot_flag_details=quality_res.bot_flag_details,
            probe_tps=quality_res.probe_tps,
            probe_reasoning_tokens=quality_res.probe_reasoning_tokens,
            proxy_used=actual_proxy,
            extra={
                "reasons": quality_res.reasons,
                "raw_user": quality_res.raw_user,
                "has_oauth": bool(oauth_token),
                "recovered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        log(f"Grok 账号已恢复并更新至数据库 (ID={account_id}, Status={quality_res.status})")

        # 记录 IP 成功
        try:
            from core.ip_quota_manager import ip_quota_manager
            ip_quota_manager.record_registration_success(actual_proxy, email=normalized_email, country=country)
        except Exception:
            pass

        return {
            "success": True,
            "account_id": account_id,
            "email": normalized_email,
            "status": quality_res.status,
            "sso_token": sso_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "proxy_used": actual_proxy,
        }

    except GrokAccountBannedError as exc:
        log(f"[!] 账号已被封禁或停用: {exc}")
        db.update_grok_account_status_by_email(normalized_email, "banned", remark=str(exc))
        return {"success": False, "email": normalized_email, "status": "banned", "error": str(exc)}
    except Exception as exc:
        log(f"[!] 恢复流程异常: {exc}")
        # 如果是已知无法登录或受阻
        err_msg = str(exc)
        if any(w in err_msg.lower() for w in ["banned", "suspended", "deactivated", "disabled"]):
            db.update_grok_account_status_by_email(normalized_email, "banned", remark=err_msg)
            return {"success": False, "email": normalized_email, "status": "banned", "error": err_msg}
        else:
            db.update_grok_account_status_by_email(normalized_email, "recovery_failed", remark=err_msg)
            return {"success": False, "email": normalized_email, "status": "failed", "error": err_msg}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

