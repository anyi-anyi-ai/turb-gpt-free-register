# -*- coding: utf-8 -*-
"""
双模通用链式代理与高粘性出口管理器 (Chained & Residential Proxy Manager)

核心架构与机制：
1. 链式代理架构：
   [指纹浏览器/注册机客户端]
             ↓ HTTP CONNECT / SOCKS (127.0.0.1:28888)
   [本地透明链式桥接代理]
             ↓ 第一跳：高速中转跳板 (Resin FAST_UPSTREAM 绑定的 88.88 节点池)
   [88.88 极速中转网关]
             ↓ 第二跳：专享出口 (7天 Dedicated 专享节点池 / 第三方动态住宅 API)
   [目标服务 (ChatGPT / Grok / OpenAI / Cloudflare)]

2. 零外部桌面软件依赖：
   全程使用标准 Python 原生全双工双层 HTTP CONNECT 隧道，无需在宿主机额外运行任何中转客户端软件。

3. 7天专享 Dedicated 节点池自愈与轮换：
   - 优先加载用户最新专享代理源 (proxies_output_20261005.txt, all_accounts_proxies_20261004.txt, proxies_americas.txt 等)；
   - 提取时自动通过 88.88 中转进行毫秒级端到端握手探测，遇 429、死节点或超时自动秒级淘汰换新；
   - 单 IP 注册达到配额 (MAX_ACCOUNTS_PER_IP) 后平滑切换至下一独立专享出口，实现高粘性且防批量封禁。

4. 自动容灾降级：
   支持 dedicated（纯专享链式）、api（第三方住宅 API）、auto（优先专享池，次选 API）及 false（直连 Resin 粘性代理池）。
"""
import base64
import json
import logging
import os
import random
import re
import select
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("dynamic_residential")


class DynamicResidentialProxyManager:
    _instance = None
    _singleton_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._lock = threading.RLock()
        self._server_sock = None
        self._server_thread = None
        self._running = False

        # 当前锁定的出口状态
        self._current_proxy_host = None
        self._current_proxy_port = None
        self._current_proxy_auth = None
        self._current_region = None
        self._current_egress_ip = None
        self._accounts_on_current_ip = 0
        self._current_ip_acquired_at = 0.0
        self._region_index = 0
        self._upstream_rr_counter = 0

        # Dedicated 专享节点池
        self._dedicated_pools = {}          # {region: [proxy_url, ...]}
        self._dedicated_cursors = {}        # {region: int}
        self._blacklisted_egress = set()    # 记录坏节点 (429 / 连接失败)

        self._initialized = True

    # ----------------- 配置读取 -----------------
    @property
    def enabled(self) -> bool:
        from config import proxy as proxy_cfg
        val = getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_ENABLED", False)
        if isinstance(val, bool):
            return val
        s = str(val or "").strip().lower()
        return s in ("true", "1", "yes", "on", "auto", "forced", "dedicated", "api")

    @property
    def mode(self) -> str:
        """运行模式：dedicated | api | auto | forced"""
        from config import proxy as proxy_cfg
        val = str(getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_ENABLED", "dedicated")).strip().lower()
        if val in ("dedicated", "true", "1", "yes", "on"):
            return "dedicated"
        if val == "api":
            return "api"
        if val == "forced":
            return "forced"
        return "auto"

    @property
    def api_url(self) -> str:
        from config import proxy as proxy_cfg
        return getattr(
            proxy_cfg,
            "DYNAMIC_RESIDENTIAL_API_URL",
            "http://usapi.udealproxy.com:7777/iplist?custom=18940&password=wooNaS&num=1&re_type=1&lb=1",
        )

    @property
    def regions(self) -> list[str]:
        from config import proxy as proxy_cfg
        raw = getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_REGIONS", ["US", "JP"])
        if isinstance(raw, str):
            raw = [r.strip().upper() for r in raw.replace(",", " ").split() if r.strip()]
        expanded = []
        for item in (raw or ["US", "JP"]):
            if ":" in item:
                parts = item.split(":", 1)
                reg = parts[0].strip().upper()
                try:
                    weight = max(1, int(parts[1].strip()))
                except ValueError:
                    weight = 1
                expanded.extend([reg] * weight)
            else:
                expanded.append(item.strip().upper())
        return expanded or ["US", "JP"]

    @property
    def max_accounts_per_ip(self) -> int:
        from config import proxy as proxy_cfg
        return int(getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_MAX_ACCOUNTS_PER_IP", 2))

    @property
    def local_host(self) -> str:
        from config import proxy as proxy_cfg
        return str(getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_LOCAL_HOST", "127.0.0.1"))

    @property
    def local_port(self) -> int:
        from config import proxy as proxy_cfg
        return int(getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_LOCAL_PORT", 28888))

    @property
    def upstream_proxy(self) -> str:
        from config import proxy as proxy_cfg
        explicit = getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_UPSTREAM_PROXY", None)
        if explicit and str(explicit).strip():
            return str(explicit).strip()
        try:
            from core.relay.manager import get_active_upstream_relay
            return get_active_upstream_relay()
        except Exception:
            return "http://FAST_UPSTREAM:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"

    @property
    def max_lifetime_seconds(self) -> float:
        from config import proxy as proxy_cfg
        return float(getattr(proxy_cfg, "DYNAMIC_RESIDENTIAL_MAX_LIFETIME_SECONDS", 900.0))

    # ----------------- 本地链式服务监听 -----------------
    def ensure_relay_server(self) -> None:
        """确保本地桥接服务正在监听。"""
        with self._lock:
            if self._running and self._server_sock is not None:
                return

            try:
                self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_sock.bind((self.local_host, self.local_port))
                self._server_sock.listen(128)
                self._server_sock.settimeout(1.0)
                self._running = True

                self._server_thread = threading.Thread(
                    target=self._run_server_loop,
                    name="DynamicResidentialRelay",
                    daemon=True,
                )
                self._server_thread.start()
                logger.info(
                    "[链式代理] 本地链式中转桥已就绪: http://%s:%d (第一跳: %s)",
                    self.local_host, self.local_port, self.upstream_proxy,
                )
            except Exception as e:
                logger.error("[链式代理] 启动本地中转桥失败: %s", e)
                raise

    def _run_server_loop(self) -> None:
        while self._running:
            try:
                client_sock, client_addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except Exception:
                if not self._running:
                    break
                continue

            t = threading.Thread(
                target=self._handle_client_connection,
                args=(client_sock, client_addr),
                daemon=True,
            )
            t.start()

    def _parse_upstream(self) -> tuple[str, int, str | None]:
        url = urllib.parse.urlsplit(self.upstream_proxy)
        host = url.hostname or "127.0.0.1"
        port = url.port or (443 if url.scheme == "https" else 80)
        auth = None
        if url.username or url.password:
            user = url.username or ""
            pwd = url.password or ""
            auth = "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()
        return host, port, auth

    def _connect_to_upstream(self, target_host: str, target_port: int, timeout: float = 12.0) -> socket.socket:
        up_host, up_port, up_auth = self._parse_upstream()
        sock = socket.create_connection((up_host, up_port), timeout=timeout)
        headers = [
            f"CONNECT {target_host}:{target_port} HTTP/1.1",
            f"Host: {target_host}:{target_port}",
            "Proxy-Connection: Keep-Alive",
        ]
        if up_auth:
            headers.append(f"Proxy-Authorization: {up_auth}")
        req_data = ("\r\n".join(headers) + "\r\n\r\n").encode("latin-1")
        sock.sendall(req_data)

        # 读取上游第一跳返回状态行
        resp_bytes = b""
        sock.settimeout(timeout)
        while b"\r\n\r\n" not in resp_bytes:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp_bytes += chunk
            if len(resp_bytes) > 65536:
                break

        header_part = resp_bytes.decode("latin-1", errors="ignore")
        status_line = header_part.splitlines()[0] if header_part else ""
        if " 200 " not in status_line and not status_line.startswith("HTTP/1.1 200"):
            sock.close()
            raise RuntimeError(f"中转跳板连接出口失败 ({target_host}:{target_port}): {status_line}")

        return sock

    def _handle_client_connection(self, client_sock: socket.socket, client_addr: tuple) -> None:
        try:
            client_sock.settimeout(15.0)
            initial_data = b""
            while b"\r\n\r\n" not in initial_data:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                initial_data += chunk
                if len(initial_data) > 65536:
                    break

            if not initial_data:
                client_sock.close()
                return

            req_str = initial_data.decode("latin-1", errors="ignore")
            first_line = req_str.splitlines()[0] if req_str else ""
            parts = first_line.split()
            if len(parts) < 2:
                client_sock.close()
                return

            method, target_uri = parts[0].upper(), parts[1]

            # 最多尝试 2 次（若当前节点在建连时断开，自动换新重试）
            for connect_attempt in range(2):
                with self._lock:
                    if not self._current_proxy_host:
                        self._extract_new_residential_ip_locked()

                    res_host = self._current_proxy_host
                    res_port = self._current_proxy_port
                    res_auth = self._current_proxy_auth

                try:
                    # 1. 第一跳：本地 -> 88.88 中转 -> 专享出口节点的隧道 (超时 8.0s)
                    tunnel_sock = self._connect_to_upstream(res_host, res_port, timeout=8.0)

                    # 2. 第二跳：专享出口节点 -> 目标域名 (HTTPS CONNECT, 超时 8.0s)
                    if method == "CONNECT":
                        connect_headers = [
                            f"CONNECT {target_uri} HTTP/1.1",
                            f"Host: {target_uri}",
                            "Proxy-Connection: Keep-Alive",
                        ]
                        if res_auth:
                            connect_headers.append(f"Proxy-Authorization: {res_auth}")
                        connect_cmd = ("\r\n".join(connect_headers) + "\r\n\r\n").encode("latin-1")
                        tunnel_sock.sendall(connect_cmd)

                        resp_bytes = b""
                        tunnel_sock.settimeout(8.0)
                        while b"\r\n\r\n" not in resp_bytes:
                            chunk = tunnel_sock.recv(4096)
                            if not chunk:
                                break
                            resp_bytes += chunk

                        resp_text = resp_bytes.decode("latin-1", errors="ignore")
                        status_line = resp_text.splitlines()[0] if resp_text else ""
                        if " 200 " not in status_line and not status_line.startswith("HTTP/1.1 200"):
                            tunnel_sock.close()
                            logger.warning(
                                "[链式代理] 专享出口 [%s:%d] 握手异常 (%s)，自动换新重试...",
                                res_host, res_port, status_line,
                            )
                            with self._lock:
                                self._blacklisted_egress.add(f"{res_host}:{res_port}")
                                self._current_proxy_host = None
                            if connect_attempt == 0:
                                continue
                            client_sock.sendall(resp_bytes)
                            client_sock.close()
                            return

                        # 通知客户端连接就绪
                        client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: ChainedProxyRelay/2.0\r\n\r\n")

                        # 双向全双工流透明转发
                        self._bi_directional_pipe(client_sock, tunnel_sock)
                        return

                    else:
                        # 普通 HTTP GET / POST 代理请求
                        if res_auth and b"Proxy-Authorization" not in initial_data:
                            header_end = initial_data.find(b"\r\n")
                            if header_end != -1:
                                auth_line = f"\r\nProxy-Authorization: {res_auth}".encode("latin-1")
                                initial_data = initial_data[:header_end] + auth_line + initial_data[header_end:]
                        tunnel_sock.sendall(initial_data)
                        self._bi_directional_pipe(client_sock, tunnel_sock)
                        return

                except Exception as ex:
                    logger.warning("[链式代理] 连接出口 [%s:%d] 异常 (%s)，触发换新...", res_host, res_port, ex)
                    with self._lock:
                        self._blacklisted_egress.add(f"{res_host}:{res_port}")
                        self._current_proxy_host = None
                    if connect_attempt == 0:
                        continue
                    raise

        except Exception as e:
            logger.debug("[链式代理] 客户端连接处理异常: %s", e)
        finally:
            try:
                client_sock.close()
            except Exception:
                pass

    def _bi_directional_pipe(self, sock1: socket.socket, sock2: socket.socket) -> None:
        """高性能全双工数据流透明转发。"""
        sock1.setblocking(False)
        sock2.setblocking(False)
        sockets = [sock1, sock2]

        while True:
            try:
                readable, _, exceptional = select.select(sockets, [], sockets, 60.0)
            except Exception:
                break

            if exceptional or not readable:
                break

            for s in readable:
                other = sock2 if s is sock1 else sock1
                try:
                    data = s.recv(16384)
                    if not data:
                        return
                    other.sendall(data)
                except Exception:
                    return

    # ----------------- 7天 Dedicated 专享节点池加载与管理 -----------------
    def _load_dedicated_pools_locked(self) -> dict[str, list[str]]:
        """从文件系统扫描并加载用户的 7 天 Dedicated 专享节点池（按时效性优先倒序加载）。"""
        if self._dedicated_pools:
            return self._dedicated_pools

        candidates_map = {"US": [], "DE": [], "EU": [], "JP": [], "SG": [], "ALL": []}

        # 动态扫描用户最新的 Dedicated 专享节点池与导出目录，按修改时间倒序（最新鲜优先）
        raw_paths: list[Path] = []
        pools_dir = Path(r"F:\github\resin\pools")
        if pools_dir.exists():
            for p in pools_dir.rglob("*.txt"):
                p_name = p.name.lower()
                # 排除 CDN、白名单/黑名单元数据文件及未经清洗的杂质 dump
                if any(bad in p_name for bad in ("cloudflare", "cloudfront", "fastly", "google", "private", "excluded", "harvested", "daily_top", "ai_clean", "example")):
                    continue
                if p.stat().st_size < 350:
                    continue
                raw_paths.append(p)

        downloads_dir = Path(r"C:\Users\hd\Downloads")
        if downloads_dir.exists():
            raw_paths.extend(downloads_dir.glob("proxies_output_*.txt"))
            raw_paths.extend(downloads_dir.glob("all_accounts_proxies_*.txt"))

        # 按最后修改时间倒序，保证 1008 等最新池排在最前
        search_paths = sorted(
            [p for p in raw_paths if p.exists() and p.is_file()],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        loaded_urls = set()
        for p in search_paths:
            p_name = p.name.lower()
            file_region = "OTHER"
            if "europe" in p_name or "_de" in p_name or "proxies_de" in p_name:
                file_region = "DE"
            elif "asia" in p_name or "_jp" in p_name or "proxies_jp" in p_name:
                file_region = "JP"
            elif "_sg" in p_name or "proxies_sg" in p_name:
                file_region = "SG"
            elif "americas" in p_name or "_us" in p_name or "proxies_us" in p_name:
                file_region = "US"
            else:
                m = re.search(r"proxies_([a-z]{2})\.txt", p_name)
                if m:
                    file_region = m.group(1).upper()
                else:
                    for cc in ("US", "DE", "JP", "SG", "GB", "FR", "CA", "AU"):
                        if f"proxies_{cc.lower()}" in p_name:
                            file_region = cc
                            break

            if file_region not in candidates_map:
                candidates_map[file_region] = []

            try:
                # 针对超大文件（如 35MB），流式读取前 5000 条高质量节点以保证秒级启动速度
                count = 0
                max_lines_per_file = 5000 if p.stat().st_size > 5_000_000 else 100000
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or line in loaded_urls:
                            continue
                        loaded_urls.add(line)
                        candidates_map["ALL"].append(line)
                        candidates_map[file_region].append(line)
                        if file_region == "DE":
                            candidates_map["EU"].append(line)
                        count += 1
                        if count >= max_lines_per_file:
                            break
            except Exception as e:
                logger.debug("[链式代理] 读取代理文件 %s 失败: %s", p, e)

        self._dedicated_pools = candidates_map
        logger.info(
            "[链式代理] 成功按区域加载 Dedicated 专享节点池: 全局=%d, US=%d, DE=%d, JP=%d, SG=%d",
            len(candidates_map.get("ALL", [])),
            len(candidates_map.get("US", [])),
            len(candidates_map.get("DE", [])),
            len(candidates_map.get("JP", [])),
            len(candidates_map.get("SG", [])),
        )
        return self._dedicated_pools

    def _probe_dedicated_node(self, host: str, port: int, auth: str | None) -> tuple[bool, str, str]:
        """通过 88.88 中转对候选 Dedicated 专享节点进行端到端探测 (超时 3.0s)。"""
        sock = None
        try:
            # 1. 连 88.88 中转 (6.0s)
            sock = self._connect_to_upstream(host, port, timeout=6.0)

            # 2. 向 Dedicated 发第二层 CONNECT 到 api.ipify.org:443
            headers = [
                "CONNECT api.ipify.org:443 HTTP/1.1",
                "Host: api.ipify.org:443",
                "Proxy-Connection: Keep-Alive",
            ]
            if auth:
                headers.append(f"Proxy-Authorization: {auth}")
            req_data = ("\r\n".join(headers) + "\r\n\r\n").encode("latin-1")
            sock.sendall(req_data)

            sock.settimeout(6.0)
            resp_bytes = b""
            while b"\r\n\r\n" not in resp_bytes:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp_bytes += chunk

            resp_str = resp_bytes.decode("latin-1", errors="ignore")
            status_line = resp_str.splitlines()[0] if resp_str else "无响应"
            if " 200 " not in resp_str and not resp_str.startswith("HTTP/1.1 200"):
                sock.close()
                return False, status_line, ""

            # 3. 提取真实出口 IP (3.0s)
            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(sock, server_hostname="api.ipify.org")
            ss.settimeout(3.0)
            ss.sendall(b"GET /?format=json HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n")
            body = ss.recv(4096).decode("latin-1", errors="ignore")
            ss.close()

            match = re.search(r'"ip":\s*"([^"]+)"', body)
            egress_ip = match.group(1) if match else host
            return True, "OK", egress_ip

        except Exception as exc:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            return False, str(exc), ""

    def _acquire_next_dedicated_node_locked(self, preferred_region: str | None = None) -> bool:
        """从 7 天 Dedicated 专享池中挑选并锁定下一个健康出口。"""
        pools = self._load_dedicated_pools_locked()
        reg = (preferred_region or "US").upper()
        candidates = pools.get(reg) or pools.get("ALL") or []
        if not candidates:
            return False

        cur_idx = self._dedicated_cursors.get(reg, 0)
        total = len(candidates)
        max_probe = min(15, total)

        for attempt in range(max_probe):
            idx = (cur_idx + attempt) % total
            node_url = candidates[idx]
            p = urllib.parse.urlsplit(node_url)
            host = p.hostname
            port = p.port or 3129
            key = f"{host}:{port}"

            if key in self._blacklisted_egress:
                continue

            auth = None
            if p.username or p.password:
                auth = "Basic " + base64.b64encode(f"{p.username or ''}:{p.password or ''}".encode()).decode()

            ok, reason, egress_ip = self._probe_dedicated_node(host, port, auth)
            if not ok:
                logger.warning(
                    "[链式代理] 候选 Dedicated 节点 [%s:%d] 握手不通 (%s)，自动淘汰换新...",
                    host, port, reason,
                )
                self._blacklisted_egress.add(key)
                continue

            # 锁定该健康节点
            self._current_proxy_host = host
            self._current_proxy_port = port
            self._current_proxy_auth = auth
            self._current_egress_ip = egress_ip
            self._current_region = reg
            self._accounts_on_current_ip = 0
            self._current_ip_acquired_at = time.time()
            self._dedicated_cursors[reg] = idx + 1

            logger.info(
                "[链式代理] ✅ 成功锁定 7天Dedicated专享出口 [%s:%d] (出口IP: %s, 地区: %s, 满 %d 账号自动换新)",
                host, port, egress_ip, reg, self.max_accounts_per_ip,
            )
            return True

        return False

    # ----------------- 第三方动态住宅 API (备选回退) -----------------
    def _call_api_for_ip(self, region: str) -> str:
        url = self.api_url
        if "regions=" in url:
            url = re.sub(r"regions=[^&]*", f"regions={region}", url)
        elif "region=" in url:
            url = re.sub(r"region=[^&]*", f"region={region}", url)
        else:
            delimiter = "&" if "?" in url else "?"
            url = f"{url}{delimiter}regions={region}"

        if "num=" in url:
            url = re.sub(r"num=\d+", "num=1", url)

        up_proxy = self.upstream_proxy
        proxy_handler = urllib.request.ProxyHandler({"http": up_proxy, "https": up_proxy})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.4.0"})

        with opener.open(req, timeout=12.0) as resp:
            content = resp.read().decode("utf-8", errors="ignore").strip()

        if not content:
            raise RuntimeError("动态住宅代理接口返回空数据")

        first_line = content.splitlines()[0].strip()
        if ":" not in first_line:
            raise RuntimeError(f"动态住宅代理返回无效格式: {first_line}")

        return first_line

    def _extract_new_residential_ip_locked(self, preferred_region: str | None = None) -> None:
        """根据当前配置提取并锁定全新的代理出口。"""
        # 1. 优先使用 7 天 Dedicated 专享节点池
        current_mode = self.mode
        if current_mode in ("dedicated", "auto", "forced"):
            if self._acquire_next_dedicated_node_locked(preferred_region):
                return

        # 2. 次选第三方动态住宅 API
        if current_mode in ("api", "auto"):
            regs = self.regions
            if preferred_region and preferred_region.upper() in regs:
                region = preferred_region.upper()
            else:
                region = regs[self._region_index % len(regs)]
                self._region_index += 1

            logger.info("[链式代理] 尝试从动态住宅 API 提取 [%s] 节点...", region)
            try:
                ip_port = self._call_api_for_ip(region)
                host, port_str = ip_port.split(":", 1)
                port = int(port_str.strip())
                self._current_proxy_host = host
                self._current_proxy_port = port
                self._current_proxy_auth = None
                self._current_region = region
                self._accounts_on_current_ip = 0
                self._current_ip_acquired_at = time.time()
                logger.info("[链式代理] ✅ 成功从动态住宅 API 锁定出口 [%s:%d]", host, port)
                return
            except Exception as e:
                if current_mode == "forced":
                    raise
                logger.warning("[链式代理] 动态住宅 API 提取失败: %s", e)

        raise RuntimeError("无法锁定任何可用的专享/动态住宅出口节点，请检查节点池或网络连接！")

    # ----------------- 对外调度接口 -----------------
    def get_registration_proxy(self, country: str | None = None, force_refresh: bool = False) -> str:
        """获取供本次注册使用的代理 URL（固定指向本地桥接端口）。"""
        if not self.enabled:
            return ""

        self.ensure_relay_server()

        with self._lock:
            now = time.time()
            is_expired = (
                self._current_proxy_host is None
                or (now - self._current_ip_acquired_at) > self.max_lifetime_seconds
                or self._accounts_on_current_ip >= self.max_accounts_per_ip
            )

            if force_refresh or is_expired:
                if self._accounts_on_current_ip >= self.max_accounts_per_ip:
                    logger.info(
                        "[链式代理] 当前出口 [%s:%d] 注册数已满 (%d/%d)，触发轮换！",
                        self._current_proxy_host, self._current_proxy_port,
                        self._accounts_on_current_ip, self.max_accounts_per_ip,
                    )
                self._extract_new_residential_ip_locked(preferred_region=country)

            proxy_url = f"http://{self.local_host}:{self.local_port}"
            return proxy_url

    def record_account_success(self, email: str | None = None) -> int:
        """注册成功回调：累计账号计数，满配额自动打标轮换。"""
        with self._lock:
            self._accounts_on_current_ip += 1
            cur = self._accounts_on_current_ip
            limit = self.max_accounts_per_ip
            logger.info(
                "[链式代理] 出口 [%s:%d] 注册成功计数 +1 (当前: %d/%d, 账号: %s)",
                self._current_proxy_host or "?", self._current_proxy_port or 0,
                cur, limit, email or "未知",
            )
            return cur

    def record_proxy_failure(self, reason: str | None = None) -> None:
        """注册失败或网络断开回调：主动淘汰坏节点，下次注册提取全新出口。"""
        with self._lock:
            if self._current_proxy_host and self._current_proxy_port:
                self._blacklisted_egress.add(f"{self._current_proxy_host}:{self._current_proxy_port}")
            logger.warning(
                "[链式代理] 检测到出口故障 (%s)，立即失效当前节点 [%s:%s]，下次将提取全新出口！",
                reason or "连接异常", self._current_proxy_host or "None", self._current_proxy_port or "None",
            )
            self._current_proxy_host = None
            self._current_proxy_port = None
            self._current_proxy_auth = None
            self._accounts_on_current_ip = 0

    def get_summary(self) -> dict:
        """获取当前状态字典供 WebUI 监控与排查。"""
        with self._lock:
            return {
                "enabled": self.enabled,
                "mode": self.mode,
                "local_bridge": f"http://{self.local_host}:{self.local_port}",
                "upstream_proxy": self.upstream_proxy,
                "upstream_name": "88.88 极速中转池 (Resin FAST_UPSTREAM)",
                "current_egress_node": f"{self._current_proxy_host}:{self._current_proxy_port}" if self._current_proxy_host else None,
                "current_egress_ip": self._current_egress_ip,
                "current_region": self._current_region,
                "accounts_on_current_ip": self._accounts_on_current_ip,
                "max_accounts_per_ip": self.max_accounts_per_ip,
                "uptime_seconds": round(time.time() - self._current_ip_acquired_at, 1) if self._current_ip_acquired_at else 0,
            }


# 全局单例与兼容别名
dynamic_residential_manager = DynamicResidentialProxyManager()
chained_proxy_manager = dynamic_residential_manager
