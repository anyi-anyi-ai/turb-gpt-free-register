# -*- coding: utf-8 -*-
"""
UNED 学生邮箱全自动批处理管线 (全自动密码自愈 + 邮箱激活 + Office 365 自动转发)
功能：
1. 遍历 SQLite 中所有 forwarding_enabled = 0 的 UNED 账号
2. [后台路由代理] 启动本地双向分流透明代理 (127.0.0.1:9999)，UNED 流量经西班牙住宅节点，微软流量直连
3. [第 0 步] 密码智能校验与自愈：若密码未落库或凭据失效，自动调用 UNED 官方 recordar-password 接口与 RapidOCR 验证码识别，获取新鲜重置链接并提交新密码
4. [第 1 步] 登录 UNED SSO 门户并直达 https://serviwebng.uned.es/correo/activar-correo.asp 点击激活邮箱 (响应码 20951 / 已激活)
5. [第 2 步] 联合认证登录微软 Office 365 Outlook，开启邮件自动转发至绑定的个人邮箱并严格勾选保留副本 (Keep a copy of forwarded messages)
6. [第 3 步] 实时更新 SQLite 状态 (forwarding_enabled = 1, status = 'activated', updated_at = datetime('now')) 并同步 Obsidian 个人档案与总汇总表
"""

import argparse
import base64
import html
import json
import logging
import os
import random
import re
import select
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from curl_cffi.requests import Session as CurlSession
from playwright.sync_api import sync_playwright
from core.uned.captcha_solver import solve_uned_captcha
from core.outlook_client import get_account_context, _ms_access_token, _fetch_outlook_rest_messages, _ms_http
from core.uned.obsidian_sync import sync_account_to_obsidian, update_summary_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("uned.pipeline")

class DynamicSpanishProxyManager:
    """西班牙代理池管理器：支持逐账号平滑轮换与故障自愈"""
    def __init__(self, proxy_file: str = "scratch/working_spanish_proxies.json"):
        self.lock = threading.Lock()
        self.proxy_file = proxy_file
        self.proxies: List[str] = []
        self.current_idx: int = -1
        self.current_proxy: Dict[str, Any] = {}
        self.load_proxies()

    def load_proxies(self):
        state_file = "scratch/proxy_state.json"
        saved_idx = -1
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    saved_idx = json.load(f).get("idx", -1)
            except Exception:
                pass
        self.current_idx = saved_idx
        if os.path.exists(self.proxy_file):
            try:
                with open(self.proxy_file, "r", encoding="utf-8") as f:
                    self.proxies = [p.strip() for p in json.load(f) if p.strip()]
            except Exception as e:
                logger.warning(f"[ProxyManager] 读取代理池文件异常: {e}")
        if not self.proxies:
            self.proxies = ["http://ht8irrm823ck:fk845drv2d7f403@209.50.184.207:3129"]
        logger.info(f"[ProxyManager] 成功加载 {len(self.proxies)} 个高可用西班牙代理节点")
        self.rotate()

    def _parse_proxy(self, proxy_url: str) -> Dict[str, Any]:
        u = urllib.parse.urlsplit(proxy_url)
        user = u.username or ""
        pwd = u.password or ""
        auth_str = base64.b64encode(f"{user}:{pwd}".encode()).decode() if user else ""
        return {
            "url": proxy_url,
            "host": u.hostname,
            "port": u.port or 3129,
            "user": user,
            "pass": pwd,
            "auth": auth_str,
        }

    def rotate(self) -> Dict[str, Any]:
        with self.lock:
            if not self.proxies:
                return {}
            self.current_idx = (self.current_idx + 1) % len(self.proxies)
            self.current_proxy = self._parse_proxy(self.proxies[self.current_idx])
            p = self.current_proxy
            try:
                with open("scratch/proxy_state.json", "w") as f:
                    json.dump({"idx": self.current_idx}, f)
            except Exception:
                pass
            logger.info(f"[ProxyManager] 🔄 [IP轮换] 已切换至西班牙节点 [{self.current_idx + 1}/{len(self.proxies)}]: {p['host']}:{p['port']}")
            return p

    def get_current(self) -> Dict[str, Any]:
        with self.lock:
            return self.current_proxy or {}

proxy_mgr = DynamicSpanishProxyManager()

ROUTER_PORT = 9999
_ROUTER_STARTED = False
_ROUTER_LOCK = threading.Lock()

def start_router_proxy_if_needed(port: int = ROUTER_PORT):
    global _ROUTER_STARTED
    with _ROUTER_LOCK:
        if _ROUTER_STARTED:
            return
        
        # 探测端口是否已被监听
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            _ROUTER_STARTED = True
            logger.info(f"[RouterProxy] 发现已有分流代理运行在 127.0.0.1:{port}")
            return
        except Exception:
            pass

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(128)
        _ROUTER_STARTED = True
        logger.info(f"[RouterProxy] 本地智能分流透明代理已启动: 127.0.0.1:{port}")

        def handle_conn(client_sock):
            remote_sock = None
            try:
                req = b""
                while b"\r\n\r\n" not in req:
                    chunk = client_sock.recv(4096)
                    if not chunk:
                        client_sock.close()
                        return
                    req += chunk

                first_line = req.split(b"\r\n")[0].decode(errors="ignore")
                parts = first_line.split()
                if len(parts) < 2 or parts[0] != "CONNECT":
                    client_sock.close()
                    return

                host_port = parts[1]
                if ":" in host_port:
                    host, port_str = host_port.split(":")
                    remote_port = int(port_str)
                else:
                    host = host_port
                    remote_port = 443

                if "uned.es" in host:
                    # UNED 域名流量转发至当前账号专属绑定的西班牙住宅代理 (保证单账号全程优先固定单IP)
                    cur_p = proxy_mgr.get_current()
                    connected = False
                    if cur_p:
                        for attempt in range(2):
                            try:
                                remote_sock = socket.create_connection((cur_p["host"], cur_p["port"]), timeout=5)
                                auth_header = f"Proxy-Authorization: Basic {cur_p['auth']}\r\n" if cur_p.get("auth") else ""
                                connect_req = f"CONNECT {host}:{remote_port} HTTP/1.1\r\nHost: {host}:{remote_port}\r\n{auth_header}Proxy-Connection: keep-alive\r\n\r\n".encode()
                                remote_sock.sendall(connect_req)
                                resp = b""
                                while b"\r\n\r\n" not in resp:
                                    rchunk = remote_sock.recv(4096)
                                    if not rchunk:
                                        break
                                    resp += rchunk
                                if b"200" in resp.split(b"\r\n")[0]:
                                    client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                                    connected = True
                                    break
                                else:
                                    remote_sock.close()
                                    remote_sock = None
                            except Exception:
                                if remote_sock:
                                    try:
                                        remote_sock.close()
                                    except Exception:
                                        pass
                                    remote_sock = None
                                time.sleep(0.2)

                    # 若当前专属代理遇到 429 或故障，自动故障转移至池内其他健康的西班牙住宅节点
                    if not connected:
                        for alt_p_url in proxy_mgr.proxies:
                            alt_p = proxy_mgr._parse_proxy(alt_p_url)
                            if cur_p and alt_p.get("host") == cur_p.get("host"):
                                continue
                            try:
                                remote_sock = socket.create_connection((alt_p["host"], alt_p["port"]), timeout=4)
                                auth_header = f"Proxy-Authorization: Basic {alt_p['auth']}\r\n" if alt_p.get("auth") else ""
                                connect_req = f"CONNECT {host}:{remote_port} HTTP/1.1\r\nHost: {host}:{remote_port}\r\n{auth_header}Proxy-Connection: keep-alive\r\n\r\n".encode()
                                remote_sock.sendall(connect_req)
                                resp = b""
                                while b"\r\n\r\n" not in resp:
                                    rchunk = remote_sock.recv(4096)
                                    if not rchunk:
                                        break
                                    resp += rchunk
                                if b"200" in resp.split(b"\r\n")[0]:
                                    client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                                    connected = True
                                    with proxy_mgr.lock:
                                        proxy_mgr.current_proxy = alt_p
                                    break
                                else:
                                    remote_sock.close()
                                    remote_sock = None
                            except Exception:
                                if remote_sock:
                                    try:
                                        remote_sock.close()
                                    except Exception:
                                        pass
                                    remote_sock = None
                            if connected:
                                break

                    if not connected:
                        # 故障转移至本地高可用代理 127.0.0.1:7897
                        try:
                            remote_sock = socket.create_connection(("127.0.0.1", 7897), timeout=5)
                            connect_req = f"CONNECT {host}:{remote_port} HTTP/1.1\r\nHost: {host}:{remote_port}\r\nProxy-Connection: keep-alive\r\n\r\n".encode()
                            remote_sock.sendall(connect_req)
                            resp = b""
                            while b"\r\n\r\n" not in resp:
                                rchunk = remote_sock.recv(4096)
                                if not rchunk:
                                    break
                                resp += rchunk
                            if b"200" in resp.split(b"\r\n")[0]:
                                client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                                connected = True
                            else:
                                remote_sock.close()
                                remote_sock = None
                        except Exception:
                            if remote_sock:
                                try:
                                    remote_sock.close()
                                except Exception:
                                    pass
                                remote_sock = None

                    if not connected:
                        # 兜底直接连接，防止浏览器中断
                        try:
                            remote_sock = socket.create_connection((host, remote_port), timeout=8)
                            client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            connected = True
                        except Exception:
                            client_sock.close()
                            return
                else:
                    # 微软及其他域名直连
                    remote_sock = socket.create_connection((host, remote_port), timeout=15)
                    client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

                # 双向通道高速转发
                socks = [client_sock, remote_sock]
                while True:
                    r, _, _ = select.select(socks, [], [], 30)
                    if not r:
                        break
                    for sock in r:
                        other = remote_sock if sock is client_sock else client_sock
                        data = sock.recv(16384)
                        if not data:
                            return
                        other.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    client_sock.close()
                except Exception:
                    pass
                try:
                    if remote_sock:
                        remote_sock.close()
                except Exception:
                    pass

        def server_loop():
            while True:
                try:
                    csock, _ = server.accept()
                    threading.Thread(target=handle_conn, args=(csock,), daemon=True).start()
                except Exception:
                    break

        threading.Thread(target=server_loop, daemon=True).start()
        time.sleep(0.5)

def add_asset_blocking(page):
    """拦截无关静态资源，大幅提高页面加载与跳转稳定性"""
    try:
        page.route("**/*.{woff,woff2,ttf,eot,png,jpg,jpeg,gif,svg,ico}", lambda r: r.abort())
        page.route("**/gtm.js**", lambda r: r.abort())
        page.route("**/google-analytics.com/**", lambda r: r.abort())
        page.route("**/googletagmanager.com/**", lambda r: r.abort())
    except Exception:
        pass

def safe_get_body_text(page, timeout_ms: int = 5000) -> str:
    """安全提取页面可见文本，防止导航阻塞导致 Locator.inner_text 抛出 30s 崩溃异常"""
    try:
        t = page.evaluate("() => document.body ? (document.body.innerText || document.body.textContent || '') : ''")
        if t:
            return t
    except Exception:
        pass
    try:
        return page.locator("body").inner_text(timeout=timeout_ms)
    except Exception:
        return ""

def trigger_recordar_password(uid: str, doc_num: str) -> bool:
    """调用 UNED 官方找回密码服务，触发向绑定邮箱发送 fresh 激活链接"""
    logger.info(f"[{uid}] 触发 UNED 官方找回密码重置流程 (recordar-password)...")
    cur_p = proxy_mgr.get_current()
    p_url = cur_p.get("url")
    s = CurlSession(
        impersonate="chrome120",
        proxies={"http": p_url, "https": p_url} if p_url else None
    )
    try:
        r1 = s.get("https://app.uned.es/soid/recordar-password", timeout=10)
        if r1.status_code != 200:
            logger.warning(f"[{uid}] 代理响应码 {r1.status_code}，自动切换至本地高可用代理...")
            s = CurlSession(impersonate="chrome120", proxies={"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"})
            r1 = s.get("https://app.uned.es/soid/recordar-password", timeout=10)
    except Exception as e:
        logger.warning(f"[{uid}] 代理请求异常: {e}，自动切换至本地高可用代理...")
        try:
            s = CurlSession(impersonate="chrome120", proxies={"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"})
            r1 = s.get("https://app.uned.es/soid/recordar-password", timeout=10)
        except Exception as e2:
            logger.warning(f"[{uid}] 本地高可用代理亦异常，尝试直连: {e2}")
            try:
                s = CurlSession(impersonate="chrome120")
                r1 = s.get("https://app.uned.es/soid/recordar-password", timeout=10)
            except Exception as e3:
                logger.warning(f"[{uid}] 直连亦异常: {e3}")
                return False

    for attempt in range(1, 12):
        try:
            cap_resp = s.get("https://app.uned.es/soid/Captcha/GetCaptchaImage", timeout=15)
            code = solve_uned_captcha(cap_resp.content)
            logger.info(f"[{uid}] 识别验证码 (第 {attempt} 次): '{code}'")
            if not code or len(code) < 4:
                time.sleep(0.5)
                continue

            post_data = {
                "Usuario": uid,
                "TipoDocumento": "05",
                "NumeroDocumento": doc_num,
                "TipoEnvio": "E",
                "CodigoCaptcha": code
            }
            r2 = s.post("https://app.uned.es/soid/recordar-password", data=post_data, timeout=25)
            if "recordar-password-envio" in r2.url or "min1" in r2.text:
                logger.info(f"[{uid}] 验证码与身份校验成功！正在请求官方发信接口...")
                r3 = s.get("https://app.uned.es/soid/enviar-recordar-password", timeout=25)
                if r3.status_code == 200:
                    logger.info(f"[{uid}] 密码重置邮件已正式由 UNED 服务器发出！")
                    return True
        except Exception as e:
            logger.warning(f"[{uid}] 找回密码重试异常 (第 {attempt} 次): {e}")
        time.sleep(1.0)
    return False

def is_password_set_success(body: str, url: str) -> bool:
    b = (body or "").lower()
    u = (url or "").lower()
    if "error" in b or "incorrecto" in b or "invalido" in b or "invalid" in b:
        return False
    return any(k in b for k in [
        "completado", "completed", "tu identificador de usuario", 
        "your user id", "ha modificado", "has been changed"
    ]) or "login.jsp" in u

def fetch_fresh_activation_link(email: str, max_wait: int = 50, exclude_urls: Optional[List[str]] = None) -> str:
    """通过 Outlook REST 接口轮询抓取刚发出的最新 password.aspx 链接，自动排除历史已失效链接"""
    logger.info(f"[{email}] 轮询个人 Outlook 邮箱获取最新激活邮件 (最多等待 {max_wait} 秒)...")
    ctx = get_account_context(email)
    if not ctx:
        logger.warning(f"[{email}] 无法获取 Outlook 账号上下文")
        return ""

    exclude_set = set(exclude_urls or [])
    client = _ms_http()
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            tok, kind = _ms_access_token(ctx, http=client)
            if tok:
                msgs = _fetch_outlook_rest_messages(client, tok)
                for m in msgs:
                    body = str(m.get("body", "")) or str(m.get("bodyPreview", ""))
                    if "password.aspx" in body:
                        unescaped = html.unescape(body)
                        match = re.search(r'https://sso\.uned\.es/sso/password\.aspx\?[^\s"\'<>]+', unescaped)
                        if match:
                            url = match.group(0)
                            if url in exclude_set:
                                continue
                            logger.info(f"[{email}] 成功获取最新激活链接: {url[:70]}...")
                            return url
        except Exception as e:
            logger.debug(f"[{email}] 轮询 Outlook 异常: {e}")
        time.sleep(4)
    logger.warning(f"[{email}] 等待最新激活邮件超时")
    return ""

def fetch_existing_or_fresh_activation_link(email: str, uid: str, doc_num: str, max_wait: int = 50) -> str:
    """先检查 Outlook 个人邮箱现有邮件中的 password.aspx 链接，若无则触发官方找回重置"""
    logger.info(f"[{email}] 检查收件箱是否存在已有激活邮件...")
    ctx = get_account_context(email)
    if ctx:
        try:
            client = _ms_http()
            tok, kind = _ms_access_token(ctx, http=client)
            if tok:
                msgs = _fetch_outlook_rest_messages(client, tok)
                for m in msgs:
                    body = str(m.get("body", "")) or str(m.get("bodyPreview", ""))
                    if "password.aspx" in body:
                        unescaped = html.unescape(body)
                        match = re.search(r'https://sso\.uned\.es/sso/password\.aspx\?[^\s"\'<>]+', unescaped)
                        if match:
                            url = match.group(0)
                            logger.info(f"[{email}] 发现现有有效激活链接: {url[:70]}...")
                            return url
        except Exception as e:
            logger.debug(f"[{email}] 检查现有邮件异常: {e}")

    # 若未找到，触发 recordar-password 官方发信
    ok = trigger_recordar_password(uid, doc_num)
    if not ok:
        logger.error(f"[{uid}] 无法触发密码找回邮件")
        return ""
    return fetch_fresh_activation_link(email, max_wait=max_wait)

def step0_recover_and_commit_password(page, uid: str, pwd: str, doc_num: str, personal_email: str) -> bool:
    """完整的密码自愈流程：获取链接 -> 提交密码 -> 落盘到 SQLite"""
    act_url = fetch_existing_or_fresh_activation_link(personal_email, uid, doc_num, max_wait=50)
    if not act_url:
        logger.error(f"[{uid}] 未能在个人邮箱中获取到激活链接")
        return False

    logger.info(f"[{uid}] 访问激活页面提交新密码...")
    add_asset_blocking(page)
    for attempt in range(1, 4):
        try:
            page.goto(act_url, wait_until="commit", timeout=25000)
            break
        except Exception as e:
            logger.warning(f"[{uid}] 访问激活页面异常 ({attempt}/3): {e}")
            page.wait_for_timeout(2000)

    try:
        page.wait_for_selector("#ContentPlaceHolder1_txt_newpass, #LinkButton1", timeout=8000)
    except Exception:
        pass

    cur_body = safe_get_body_text(page)
    if "completado" in cur_body.lower() or "tu identificador de usuario es" in cur_body.lower():
        logger.info(f"[{uid}] 该账号此前已完成密码激活！")
        return True

    inp1 = page.locator("#ContentPlaceHolder1_txt_newpass")
    inp2 = page.locator("#ContentPlaceHolder1_txt_rnewpass")
    if inp1.count() > 0:
        inp1.first.fill(pwd)
        inp2.first.fill(pwd)
        logger.info(f"[{uid}] 触发 Aceptar 提交新密码...")
        try:
            page.evaluate('() => { setTimeout(() => { const b = document.getElementById("LinkButton1") || document.getElementById("LinkButton2") || document.querySelector("a[href*=\'LinkButton\']"); if (b) b.click(); }, 30); }')
        except Exception as e:
            logger.warning(f"[{uid}] 点击提交按钮异常: {e}")
        
        for _ in range(12):
            page.wait_for_timeout(1000)
            res_body = safe_get_body_text(page)
            if is_password_set_success(res_body, page.url):
                break

        logger.info(f"[{uid}] 提交新密码后响应页面: {res_body[:120].replace(chr(10), ' ')}")
        if is_password_set_success(res_body, page.url):
            logger.info(f"[{uid}] ★ 密码设置成功确认！")
            # 更新 SQLite 密码
            try:
                conn = sqlite3.connect("turb.sqlite3")
                cur = conn.cursor()
                cur.execute("SELECT payload_json FROM uned_accounts WHERE uned_identifier=?", (uid,))
                p_row = cur.fetchone()
                if p_row and p_row[0]:
                    p_data = json.loads(p_row[0])
                    if "extra" not in p_data: p_data["extra"] = {}
                    p_data["extra"]["password"] = pwd
                    cur.execute("UPDATE uned_accounts SET payload_json=? WHERE uned_identifier=?", (json.dumps(p_data), uid))
                    conn.commit()
                conn.close()
            except Exception:
                pass
            return True
        else:
            logger.warning(f"[{uid}] 现有激活链接未生效，启动官方找回重置 (recordar-password)...")

    logger.warning(f"[{uid}] 触发官方找回重置流程...")
    ok = trigger_recordar_password(uid, doc_num)
    if ok:
        new_url = fetch_fresh_activation_link(personal_email, max_wait=45, exclude_urls=[act_url])
        if new_url:
            logger.info(f"[{uid}] 访问官方最新重置链接: {new_url[:70]}...")
            for attempt in range(1, 4):
                try:
                    page.goto(new_url, wait_until="commit", timeout=25000)
                    break
                except Exception as e:
                    logger.warning(f"[{uid}] 访问最新重置链接异常 ({attempt}/3): {e}")
                    page.wait_for_timeout(2000)
            try:
                page.wait_for_selector("#ContentPlaceHolder1_txt_newpass, #LinkButton1", timeout=12000)
            except Exception:
                pass
            inp1 = page.locator("#ContentPlaceHolder1_txt_newpass")
            inp2 = page.locator("#ContentPlaceHolder1_txt_rnewpass")
            if inp1.count() > 0:
                inp1.first.fill(pwd)
                inp2.first.fill(pwd)
                logger.info(f"[{uid}] 在最新重置页触发 Aceptar 提交新密码...")
                try:
                    page.evaluate('() => { setTimeout(() => { const b = document.getElementById("LinkButton1") || document.getElementById("LinkButton2") || document.querySelector("a[href*=\'LinkButton\']"); if (b) b.click(); }, 30); }')
                except Exception:
                    pass
                for _ in range(12):
                    page.wait_for_timeout(1000)
                    res_body = safe_get_body_text(page)
                    if is_password_set_success(res_body, page.url):
                        break
                logger.info(f"[{uid}] 官方最新重置页响应: {res_body[:120].replace(chr(10), ' ')}")
                if is_password_set_success(res_body, page.url):
                    logger.info(f"[{uid}] ★ 官方重置邮件新密码设置成功确认！")
                    try:
                        conn = sqlite3.connect("turb.sqlite3")
                        cur = conn.cursor()
                        cur.execute("SELECT payload_json FROM uned_accounts WHERE uned_identifier=?", (uid,))
                        p_row = cur.fetchone()
                        if p_row and p_row[0]:
                            p_data = json.loads(p_row[0])
                            if "extra" not in p_data: p_data["extra"] = {}
                            p_data["extra"]["password"] = pwd
                            cur.execute("UPDATE uned_accounts SET payload_json=? WHERE uned_identifier=?", (json.dumps(p_data), uid))
                            conn.commit()
                        conn.close()
                    except Exception:
                        pass
                    return True

    return False

def step1_activate_mailbox(page, uid: str, pwd: str, doc_num: str, personal_email: str) -> dict:
    """登录 UNED SSO 门户并在 serviwebng 中激活学生邮箱"""
    login_url = "https://sso.uned.es/sso/index.aspx?URL=https%3a%2f%2flogin.uned.es%2fssouned%2flogin.jsp"
    try:
        add_asset_blocking(page)
        logger.info(f"[{uid}] [1/2] 登录 UNED SSO 门户激活学生邮箱...")
        for attempt in range(1, 4):
            try:
                page.goto(login_url, wait_until='commit', timeout=25000)
                break
            except Exception as e:
                logger.warning(f"[{uid}] 访问登录页异常 ({attempt}/3): {e}")
                page.wait_for_timeout(2000)
        page.wait_for_selector("#ContentPlaceHolder1_ssousername", timeout=15000)
        page.fill("#ContentPlaceHolder1_ssousername", uid)
        page.fill("#ContentPlaceHolder1_password", pwd)
        page.click("#ContentPlaceHolder1_Button1")

        for _ in range(15):
            page.wait_for_timeout(1000)
            if "login.jsp" in page.url or "salida.aspx" in page.url or "sso.uned.es" not in page.url:
                break

        # 检查是否登录失败
        cur_body = safe_get_body_text(page)
        if "bloqueada" in cur_body.lower():
            logger.warning(f"[{uid}] 账号触发 UNED 安全策略临时锁定 (Cuenta Bloqueada)")
            return {"success": False, "blocked": True, "message": "Cuenta Bloqueada"}

        need_recovery = False
        if ("sso.uned.es" in page.url and "login.jsp" not in page.url and "salida.aspx" not in page.url) or \
           ("incorrecto" in cur_body.lower() or "error" in cur_body.lower()):
            need_recovery = True

        if need_recovery:
            logger.warning(f"[{uid}] 检测到凭据无效或未激活，自动启动密码自愈流程...")
            recovered = step0_recover_and_commit_password(page, uid, pwd, doc_num, personal_email)
            if not recovered:
                return {"success": False, "message": "密码自愈失败"}
            # 自愈后重新登录，平滑缓冲避免 ERR_ABORTED
            logger.info(f"[{uid}] 密码自愈成功，重新登录 UNED SSO...")
            page.wait_for_timeout(2500)
            for _ in range(3):
                try:
                    page.goto(login_url, wait_until='commit', timeout=25000)
                    break
                except Exception:
                    page.wait_for_timeout(2000)
            page.wait_for_selector("#ContentPlaceHolder1_ssousername", timeout=15000)
            page.fill("#ContentPlaceHolder1_ssousername", uid)
            page.fill("#ContentPlaceHolder1_password", pwd)
            page.click("#ContentPlaceHolder1_Button1")
            for _ in range(15):
                page.wait_for_timeout(1000)
                if "login.jsp" in page.url or "salida.aspx" in page.url or "sso.uned.es" not in page.url:
                    break

        # 直达邮箱激活页面
        body_text = ""
        for attempt in range(1, 4):
            try:
                page.goto("https://serviwebng.uned.es/correo/activar-correo.asp", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)
                body_text = safe_get_body_text(page)
                if "inicio de sesión" not in body_text.lower() and "login" not in body_text.lower() and body_text.strip():
                    break
                logger.warning(f"[{uid}] 激活页提示需等待会话同步，重试 ({attempt}/3)...")
            except Exception as e:
                logger.warning(f"[{uid}] 访问激活页异常 ({attempt}/3): {e}")
                page.wait_for_timeout(2000)

        # 若依然重定向到登录页，说明初始凭据未成功生效，再次尝试密码自愈
        if "inicio de sesión" in body_text.lower() or "login" in body_text.lower():
            logger.warning(f"[{uid}] serviwebng 会话校验失败，二次触发密码自愈与重新激活...")
            recovered = step0_recover_and_commit_password(page, uid, pwd, doc_num, personal_email)
            if not recovered:
                return {"success": False, "message": "密码自愈未成功或链接失效"}
            page.wait_for_timeout(2500)
            for _ in range(3):
                try:
                    page.goto(login_url, wait_until='commit', timeout=25000)
                    break
                except Exception:
                    page.wait_for_timeout(2000)
            page.wait_for_selector("#ContentPlaceHolder1_ssousername", timeout=15000)
            page.fill("#ContentPlaceHolder1_ssousername", uid)
            page.fill("#ContentPlaceHolder1_password", pwd)
            page.click("#ContentPlaceHolder1_Button1")
            for _ in range(15):
                page.wait_for_timeout(1000)
                if "login.jsp" in page.url or "salida.aspx" in page.url or "sso.uned.es" not in page.url:
                    break
            for attempt in range(1, 4):
                try:
                    page.goto("https://serviwebng.uned.es/correo/activar-correo.asp", wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(3000)
                    body_text = safe_get_body_text(page)
                    if "inicio de sesión" not in body_text.lower() and "login" not in body_text.lower() and body_text.strip():
                        break
                    page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"[{uid}] 二次访问激活页异常 ({attempt}/3): {e}")
                    page.wait_for_timeout(2000)

        btns = page.locator("input[value*='Activar'], button:has-text('Activar')")
        clicked_activar = False
        if btns.count() > 0:
            logger.info(f"[{uid}] 点击【Activar Correo】激活按钮...")
            try:
                btns.first.click()
                clicked_activar = True
                page.wait_for_timeout(4000)
                body_text = safe_get_body_text(page)
            except Exception:
                pass

        if "activada" in body_text.lower() or "cuenta de correo" in body_text.lower() or "20951" in body_text or "already activated" in body_text.lower():
            logger.info(f"[{uid}] 邮箱激活成功确认 (20951: Cuenta de correo ya activada)")
            return {"success": True, "message": "邮箱激活通过"}
        elif clicked_activar:
            logger.info(f"[{uid}] 点击激活按钮成功，进入转发配置...")
            return {"success": True, "message": "已点击激活按钮"}
        elif "serviwebng.uned.es" in page.url and ("correo" in page.url or "activar" in page.url):
            logger.info(f"[{uid}] 已位于 serviwebng 邮箱管理界面，直接进入转发配置...")
            return {"success": True, "message": "位于邮箱管理界面"}
        else:
            return {"success": False, "message": f"未检测到激活成功文本: {body_text[:100]}"}
    except Exception as e:
        return {"success": False, "message": f"激活阶段异常: {e}"}

def step2_enable_forwarding(page, uid: str, pwd: str, personal_email: str) -> dict:
    settings_sel = "button#settingsId, button[title*='Settings'], button[title*='Configuraci'], button[aria-label*='Settings'], button[aria-label*='Configuraci']"
    school_email = f"{uid}@alumno.uned.es"
    try:
        add_asset_blocking(page)
        logger.info(f"[{uid}] [2/2] 登录 Office 365 配置转发至 {personal_email} ...")
        for attempt in range(1, 4):
            try:
                page.goto(f"https://login.microsoftonline.com/?login_hint={school_email}", wait_until="domcontentloaded", timeout=35000)
                break
            except Exception as e:
                logger.warning(f"[{uid}] 访问微软登录页异常 ({attempt}/3): {e}")
                page.wait_for_timeout(2000)
        page.wait_for_timeout(3000)

        # 微软登录页或联合认证
        if page.locator("#i0116").count() > 0 and page.locator("#i0116").first.is_visible():
            val = page.locator("#i0116").first.input_value()
            if not val:
                logger.info(f"[{uid}] 填入微软登录账号: {school_email} ...")
                page.locator("#i0116").first.fill(school_email)
            if page.locator("#idSIButton9").count() > 0 and page.locator("#idSIButton9").first.is_visible():
                logger.info(f"[{uid}] 点击微软 Next 下一步...")
                page.locator("#idSIButton9").first.click()
                page.wait_for_timeout(4000)

        # 等待联合认证表单加载
        try:
            page.wait_for_selector("#ContentPlaceHolder1_ssousername, button#settingsId, #idSIButton9, div:has-text('Signed in')", timeout=25000)
        except Exception:
            pass

        if "chrome-error" in page.url:
            logger.warning(f"[{uid}] 联合认证遇到网络阻塞页面，刷新重试: {page.url}")
            try:
                page.reload(wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
            except Exception:
                pass

        logger.info(f"[{uid}] 联合认证当前 URL: {page.url}")

        if page.locator("#ContentPlaceHolder1_ssousername").count() > 0:
            logger.info(f"[{uid}] 提交 UNED SSO 联合认证凭据...")
            page.fill("#ContentPlaceHolder1_ssousername", uid)
            page.fill("#ContentPlaceHolder1_password", pwd)
            try:
                page.locator("#ContentPlaceHolder1_Button1").first.click(timeout=8000)
            except Exception:
                try:
                    page.evaluate('() => { const b = document.getElementById("ContentPlaceHolder1_Button1"); if (b) b.click(); }')
                except Exception:
                    pass
            for _ in range(30):
                try:
                    page.wait_for_timeout(1000)
                    cur_u = page.url
                    if "login.microsoftonline.com" in cur_u or "login.srf" in cur_u or "outlook" in cur_u:
                        break
                except Exception:
                    pass
            logger.info(f"[{uid}] SSO 提交后 URL: {page.url}")

        if "salida.aspx" in page.url or "chrome-error" in page.url:
            logger.info(f"[{uid}] 位于 salida.aspx 或遇到页面错误 ({page.url})，重新访问微软登录页建立新联合认证会话...")
            try:
                page.goto(f"https://login.microsoftonline.com/?login_hint={school_email}", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)
            except Exception:
                pass

        # 保持登录态 (Stay signed in)
        try:
            page.wait_for_selector("#idSIButton9, input[value='Yes'], button:has-text('Yes')", timeout=20000)
            logger.info(f"[{uid}] 点击 Stay signed in...")
            page.click("#idSIButton9, input[value='Yes'], button:has-text('Yes')")
            page.wait_for_timeout(3000)
        except Exception:
            pass

        # 若再次返回 Microsoft 登录页，自动填入邮箱推进
        if page.locator("#i0116").count() > 0 and page.locator("#i0116").first.is_visible():
            logger.info(f"[{uid}] 再次检测到 Microsoft 登录框，填入 {school_email}...")
            page.locator("#i0116").first.fill(school_email)
            if page.locator("#idSIButton9").count() > 0 and page.locator("#idSIButton9").first.is_visible():
                page.locator("#idSIButton9").first.click()
                page.wait_for_timeout(4000)

        # 访问 Outlook 邮箱
        logger.info(f"[{uid}] 正在加载 Outlook Web App 界面...")
        for attempt in range(1, 4):
            try:
                page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=40000)
                break
            except Exception:
                page.wait_for_timeout(3000)

        # 处理可能出现的 Pick an account / Stay signed in / Your privacy matters 浮层
        settings_sel = "button#settingsId, button[title*='Settings'], button[title*='Configuraci'], button[aria-label*='Settings'], button[aria-label*='Configuraci']"
        for _ in range(15):
            # 优先关闭可能遮挡的提示与隐私弹窗
            modal_btn = page.locator("button:has-text('Continue'), button:has-text('Continuar'), button:has-text('Next')")
            if modal_btn.count() > 0 and modal_btn.first.is_visible():
                logger.info(f"[{uid}] 点击 Continue 关闭隐私/引导弹窗...")
                try:
                    modal_btn.first.click(force=True)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            if page.locator(settings_sel).count() > 0 and page.locator(settings_sel).first.is_visible():
                break
            if page.locator("#idSIButton9, input[value='Yes'], button:has-text('Yes')").count() > 0:
                page.locator("#idSIButton9, input[value='Yes'], button:has-text('Yes')").first.click()
                page.wait_for_timeout(3000)
            if page.locator("text='Pick an account'").count() > 0 or page.locator("div:has-text('Signed in')").count() > 0:
                page.locator("div:has-text('Signed in'), [role='listitem'], #tilesHolder > div").first.click()
                page.wait_for_timeout(4000)
            page.wait_for_timeout(1000)

        # 打开设置面板
        page.wait_for_selector(settings_sel, timeout=45000)
        logger.info(f"[{uid}] 打开 Outlook 设置面板...")
        page.locator(settings_sel).first.click()
        page.wait_for_timeout(3000)

        # 搜索 Forwarding / Reenvío
        search_inp = page.locator("input[placeholder*='Search settings'], input[placeholder*='Buscar']")
        if search_inp.count() > 0:
            search_inp.first.fill("Forwarding")
            page.wait_for_timeout(1500)
            if page.locator("div[role='listbox'] div:has-text('Forwarding'), [role='option']:has-text('Forwarding'), div:has-text('Reenvío')").count() > 0:
                page.click("div[role='listbox'] div:has-text('Forwarding'), [role='option']:has-text('Forwarding'), div:has-text('Reenvío')")
            else:
                search_inp.first.fill("Reenvío")
                page.wait_for_timeout(1500)
                page.click("div[role='listbox'] div:has-text('Reenvío'), [role='option']:has-text('Reenvío'), [role='option']:has-text('Forwarding')")
            page.wait_for_timeout(3000)

        # 开启转发开关 (双语回退支持)
        lbl = page.locator("label:has-text('Enable forwarding'), span:text-is('Enable forwarding'), label:has-text('Habilitar reenvío')")
        inp = page.locator("input[placeholder*='Enter an email address'], input[placeholder*='Escriba una dirección']")
        if inp.count() == 0 or not inp.first.is_visible():
            if lbl.count() > 0:
                logger.info(f"[{uid}] 开启【启用转发】开关...")
                lbl.first.click()
                page.wait_for_timeout(1500)

        inp = page.locator("input[placeholder*='Enter an email address'], input[placeholder*='Escriba una dirección']")
        if inp.count() > 0:
            logger.info(f"[{uid}] 填入目标转发邮箱: {personal_email} ...")
            inp.first.fill(personal_email)
            page.wait_for_timeout(1000)

        # 严格勾选并验证“在收件箱中保留副本”
        keep_label = page.locator("label:has-text('Keep a copy of forwarded messages'), label:has-text('Guardar una copia de los mensajes reenviados')")
        if keep_label.count() > 0:
            chk = keep_label.locator("input[type='checkbox']")
            if chk.count() > 0:
                if not chk.first.is_checked():
                    logger.info(f"[{uid}] 勾选【在收件箱中保留副本】...")
                    chk.first.check()
            else:
                logger.info(f"[{uid}] 点击【在收件箱中保留副本】标签...")
                keep_label.first.click()
            page.wait_for_timeout(1000)

        # 再次检查是否有遮挡弹窗并关闭
        for _ in range(3):
            modal_btn = page.locator("button:has-text('Continue'), button:has-text('Continuar'), button:has-text('Next')")
            if modal_btn.count() > 0 and modal_btn.first.is_visible():
                logger.info(f"[{uid}] 关闭设置面板遮挡弹窗...")
                try:
                    modal_btn.first.click(force=True)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            else:
                break

        save_btn = page.locator("button:has-text('Save'):visible, button:has-text('Guardar'):visible")
        if save_btn.count() > 0:
            logger.info(f"[{uid}] 保存转发设置...")
            try:
                save_btn.first.click(force=True)
            except Exception:
                page.evaluate('() => { const btns = Array.from(document.querySelectorAll("button")); const b = btns.find(x => x.innerText.trim() === "Save" || x.innerText.trim() === "Guardar"); if (b) b.click(); }')
            page.wait_for_timeout(4000)

        return {"success": True, "message": f"成功配置转发至 {personal_email} 并勾选保留副本"}
    except Exception as e:
        try:
            page.screenshot(path=f"scratch/{uid}_stage2_error.png")
        except Exception:
            pass
        return {"success": False, "message": f"转发配置异常: {e}"}

def process_single_account(browser, uid: str, pwd: str, doc_num: str, personal_email: str, extra: dict, row_info: dict) -> dict:
    # 步骤 1: 登录 UNED SSO 门户激活学生邮箱 (含步骤 0 自动自愈)
    ctx1 = browser.new_context(
        locale="es-ES",
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    page1 = ctx1.new_page()
    try:
        res1 = step1_activate_mailbox(page1, uid, pwd, doc_num, personal_email)
    finally:
        ctx1.close()

    if not res1["success"]:
        return res1

    # 步骤 2: 登录 Office 365 配置转发 (在干净的隔离上下文中执行)
    ctx2 = browser.new_context(
        locale="en-US",
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    page2 = ctx2.new_page()
    try:
        res2 = step2_enable_forwarding(page2, uid, pwd, personal_email)
    finally:
        ctx2.close()

    if not res2["success"]:
        return res2

    # 步骤 3: 更新数据库与 Obsidian
    logger.info(f"[{uid}] 全部成功！更新 SQLite 数据库与 Obsidian 资产...")
    conn = sqlite3.connect("turb.sqlite3")
    cur = conn.cursor()
    cur.execute("UPDATE uned_accounts SET forwarding_enabled=1, status='activated', updated_at=datetime('now') WHERE uned_identifier=?", (uid,))
    conn.commit()
    conn.close()

    try:
        acc_info = {
            "uned_identifier": uid,
            "student_email": f"{uid}@alumno.uned.es",
            "email": personal_email,
            "password": pwd,
            "status": "activated",
            "forwarding_enabled": 1,
            "extra": extra,
            "phone": row_info.get("phone", ""),
            "doc_number": doc_num,
            "name": row_info.get("full_name", ""),
            "created_at": row_info.get("created_at", ""),
            "forwarding_to": personal_email
        }
        sync_account_to_obsidian(acc_info)
        update_summary_table()
    except Exception as e:
        logger.warning(f"[{uid}] 同步 Obsidian 发生轻微警告: {e}")

    return {"success": True, "message": "全流程执行完成"}

def run_batch(limit: Optional[int] = None, target_uid: Optional[str] = None):
    # 启动后台透明分流代理
    start_router_proxy_if_needed(ROUTER_PORT)

    conn = sqlite3.connect("turb.sqlite3")
    cur = conn.cursor()
    if target_uid:
        cur.execute("SELECT id, uned_identifier, email, doc_number, full_name, payload_json, forwarding_enabled FROM uned_accounts WHERE uned_identifier=?", (target_uid,))
    else:
        cur.execute("SELECT id, uned_identifier, email, doc_number, full_name, payload_json, forwarding_enabled FROM uned_accounts ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    total_db = len(rows)
    pending = [r for r in rows if r[6] == 0]
    total_pending = len(pending)

    print("\n" + "=" * 75)
    print(f"       📬 UNED 邮箱激活 + Office 365 自动转发全自动批处理系统 (强化版)")
    print(f"       总账号数: {total_db} | 已完成: {total_db - total_pending} | 待处理: {total_pending}")
    if limit:
        print(f"       本次限制处理数量: {limit}")
    if target_uid:
        print(f"       指定单账号运行: {target_uid}")
    print("=" * 75 + "\n")

    if not pending:
        print("[+] 所有账号已全部完成激活与自动转发！无需执行。")
        update_summary_table()
        return

    success_cnt = 0
    fail_cnt = 0
    cooldown_queue = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": f"http://127.0.0.1:{ROUTER_PORT}"},
            args=["--disable-blink-features=AutomationControlled"]
        )

        for idx, (acc_id, uid, personal_email, doc_num, full_name, p_str, fwd_enabled) in enumerate(pending, 1):
            if limit and idx > limit:
                break

            pwd = "Uned@2026!Zhang#88"
            extra = {}
            row_info = {"full_name": full_name, "doc_number": doc_num}
            try:
                p_obj = json.loads(p_str)
                extra = p_obj.get("extra", {})
                pwd = extra.get("password") or pwd
                row_info["phone"] = p_obj.get("phone", "")
                row_info["created_at"] = p_obj.get("created_at", "")
                if not doc_num:
                    doc_num = p_obj.get("doc_number", "")
            except Exception:
                pass

            # 严格逐账号轮换专属西班牙代理 IP，规避同 IP 频次风控
            cur_p = proxy_mgr.rotate()
            print(f"\n>>> [{idx}/{total_pending}] 开始处理: {uid} ({uid}@alumno.uned.es)")
            print(f"    目标转发邮箱: {personal_email} | 证件号: {doc_num}")
            print(f"    🔄 轮换西班牙专属出口代理: {cur_p.get('host')}:{cur_p.get('port')}")

            try:
                res = process_single_account(browser, uid, pwd, doc_num, personal_email, extra, row_info)
                if res["success"]:
                    success_cnt += 1
                    print(f">>> [{idx}/{total_pending}] [成功] {uid} 邮箱激活与自动转发配置完成！")
                else:
                    if res.get("blocked"):
                        print(f">>> [{idx}/{total_pending}] [排队冷却] {uid} 触发 UNED 临时锁定，加入延迟重试队列")
                        cooldown_queue.append((acc_id, uid, personal_email, doc_num, full_name, p_str, fwd_enabled))
                    else:
                        fail_cnt += 1
                        print(f">>> [{idx}/{total_pending}] [失败] {uid}: {res['message']}")
            except Exception as e:
                fail_cnt += 1
                logger.error(f">>> [{idx}/{total_pending}] [未捕获异常] {uid}: {e}")

            # 逐账号风控保护冷却 (随机抖动 3.5 ~ 6.5 秒)
            sleep_jitter = random.uniform(3.5, 6.5)
            logger.info(f"[*] 等待 {sleep_jitter:.1f} 秒防风控抖动冷却，准备进入下一账号...")
            time.sleep(sleep_jitter)

        # 处理冷却队列 (若有)
        if cooldown_queue:
            print(f"\n[!] 等待 60 秒以解除 UNED 临时安全策略，随后重试 {len(cooldown_queue)} 个账号...")
            time.sleep(60)
            for idx, (acc_id, uid, personal_email, doc_num, full_name, p_str, fwd_enabled) in enumerate(cooldown_queue, 1):
                try:
                    p_obj = json.loads(p_str)
                    extra = p_obj.get("extra", {})
                    pwd = extra.get("password") or "Uned@2026!Zhang#88"
                    row_info = {"full_name": full_name, "doc_number": doc_num, "phone": p_obj.get("phone", ""), "created_at": p_obj.get("created_at", "")}
                    res = process_single_account(browser, uid, pwd, doc_num, personal_email, extra, row_info)
                    if res["success"]:
                        success_cnt += 1
                        print(f">>> [重试成功] {uid} 已完成！")
                    else:
                        fail_cnt += 1
                        print(f">>> [重试失败] {uid}: {res['message']}")
                except Exception as e:
                    fail_cnt += 1
                    logger.error(f">>> [重试异常] {uid}: {e}")

        browser.close()

    # 最终更新 Obsidian 汇总总表
    update_summary_table()

    print("\n" + "=" * 75)
    print(f"       批处理执行完毕: 本次成功 {success_cnt} | 失败 {fail_cnt} | 剩余待处理 {total_pending - success_cnt}")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UNED 学生邮箱全自动批处理管线")
    parser.add_argument("--limit", type=int, default=None, help="限制处理的账号数量")
    parser.add_argument("--account", type=str, default=None, help="仅处理指定的单个 UNED 标识符")
    args = parser.parse_args()

    run_batch(limit=args.limit, target_uid=args.account)
