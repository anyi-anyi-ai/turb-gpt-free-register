# -*- coding: utf-8 -*-
"""
core/live_browser_hub.py — CDP 实时网页视窗与交互中心 (Live Browser Screencast Hub)

功能：
1. 捕获后台无头/有头 Chromium 的 CDP Screencast 实时帧流 (JPEG base64)。
2. 通过 WebSocket 零延迟分发到前端 Web 界面。
3. 将用户在 Web 画布上的鼠标点击、拖拽、滚轮、键盘按键双向转发回后台浏览器。
4. 提供独立的 Web 查看页面 /live 与 WebSocket 端点。
5. 支持跨进程双向网桥 (IPC Bridge)：外部独立 CLI / 脚本启动的浏览器，可自动无缝推屏至 WebUI (8799)！
"""

import asyncio
import base64
import inspect
import json
import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("live_browser")


class LiveBrowserHub:
    """全局单例实时浏览器投屏与交互中枢。"""

    _instance: Optional['LiveBrowserHub'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'LiveBrowserHub':
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_hub()
            return cls._instance

    def _init_hub(self) -> None:
        self.active_page: Any = None
        self.active_cdp: Any = None
        self.last_frame: Optional[str] = None
        self.last_frame_metadata: Dict[str, Any] = {}
        self.last_frame_ts: float = 0.0
        self.clients: Set[Any] = set()
        self.bridge_clients: Set[Any] = set()  # 作为服务端时连接的外部 Worker
        self.page_info: Dict[str, str] = {"url": "about:blank", "title": "未连接浏览器"}
        self._is_screencast_running: bool = False
        self._frame_lock = threading.Lock()
        self._bridge_task: Optional[asyncio.Task] = None
        self._bridge_send_queue: Optional[asyncio.Queue] = None

    @classmethod
    def get_hub(cls) -> 'LiveBrowserHub':
        return cls()

    @classmethod
    def is_port_in_use(cls, port: int = 8799) -> bool:
        """检查指定端口是否已被占用/处于监听状态。"""
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                return s.connect_ex(('127.0.0.1', port)) == 0
        except Exception:
            return False

    @classmethod
    def ensure_server(cls, port: int = 8799) -> None:
        """
        确保 8799 端口有 Web 视窗服务在监听。
        若外部主 WebUI (server.py) 未运行，则在后台常驻守护线程中轻量拉起嵌入式 Uvicorn 服务。
        """
        if cls.is_port_in_use(port):
            logger.debug(f"[LiveBrowser] 端口 {port} 已处于运行中，无需自启嵌入式服务。")
            return

        logger.info(f"[LiveBrowser] 端口 {port} 处于空闲状态，正在启动后台轻量嵌入式投屏服务器...")

        def _run_server():
            try:
                import uvicorn
                from fastapi import FastAPI
                micro_app = FastAPI(title="LiveBrowserHub Embedded MicroServer")
                mount_live_routes(micro_app)
                config = uvicorn.Config(micro_app, host="127.0.0.1", port=port, log_level="warning")
                server = uvicorn.Server(config)
                server.run()
            except Exception as ex:
                logger.warning(f"[LiveBrowser] 嵌入式服务器启动异常: {ex}")

        th = threading.Thread(target=_run_server, daemon=True, name="LiveBrowserHub-Server")
        th.start()

        # 等待端口就绪，最长等待 3 秒
        for _ in range(30):
            time.sleep(0.1)
            if cls.is_port_in_use(port):
                logger.info(f"[LiveBrowser] ✅ 嵌入式投屏服务启动成功: http://127.0.0.1:{port}/live")
                break

    @classmethod
    def open_browser(cls, port: int = 8799) -> str:
        """在系统默认浏览器中自动弹出实时投屏互动视窗。"""
        import webbrowser
        cls.ensure_server(port)
        url = f"http://127.0.0.1:{port}/live"
        try:
            webbrowser.open(url)
            logger.info(f"[LiveBrowser] 🚀 已在系统默认浏览器中打开投屏视窗: {url}")
        except Exception as e:
            logger.warning(f"[LiveBrowser] 调用默认浏览器打开视窗失败: {e}")
        return url

    def attach_page(self, page: Any, auto_open: bool = False, port: int = 8799) -> None:
        """绑定当前活跃的 Playwright Page，并开启 CDP Screencast。"""
        if not page:
            return
        if auto_open:
            self.open_browser(port)
        self.active_page = page
        logger.info("[LiveBrowser] 正在为当前页面挂载 CDP 实时视窗...")

        # 严格判断是异步 Playwright 还是同步 Playwright
        is_async = (
            inspect.iscoroutinefunction(getattr(page, "goto", None))
            or inspect.iscoroutinefunction(getattr(page.context, "new_cdp_session", None))
            or type(page).__module__.startswith("playwright.async_api")
        )

        if is_async:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._attach_page_async(page))
                    return
            except RuntimeError:
                pass

        # 同步回退 / 线程执行
        try:
            self.active_cdp = page.context.new_cdp_session(page)
            self._setup_cdp_listeners()
            self._start_screencast()
            try:
                self.page_info = {
                    "url": getattr(page, "url", "about:blank") or "about:blank",
                    "title": getattr(page, "title", lambda: "RunningHub")() if callable(getattr(page, "title", None)) else "RunningHub",
                }
            except Exception:
                pass

            # 若 8799 视窗已作为常驻守护进程运行，无缝接通网桥线程
            if self._bridge_send_queue is None and self.is_port_in_use(port):
                self._bridge_send_queue = queue.Queue(maxsize=10)
                def _start_sync_bridge():
                    b_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(b_loop)
                    b_loop.run_until_complete(self._run_bridge_client_loop(f"ws://127.0.0.1:{port}/live/bridge"))
                b_thread = threading.Thread(target=_start_sync_bridge, daemon=True, name="LiveBrowser-BridgeThread")
                b_thread.start()

            logger.info("[LiveBrowser] CDP 实时视窗挂载成功！")
        except Exception as e:
            logger.warning(f"[LiveBrowser] 挂载 CDP Screencast 异常: {e}")

    async def _attach_page_async(self, page: Any) -> None:
        """异步挂载 CDP Screencast，并自动开启双向网桥。"""
        try:
            self.active_page = page
            self.active_cdp = await page.context.new_cdp_session(page)
            self._setup_cdp_listeners()
            await self.active_cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": 75,
                    "maxWidth": 1280,
                    "maxHeight": 800,
                    "everyNthFrame": 1,
                },
            )
            self._is_screencast_running = True
            try:
                self.page_info = {
                    "url": page.url or "about:blank",
                    "title": (await page.title()) or "RunningHub",
                }
            except Exception:
                pass
            logger.info("[LiveBrowser] 异步 CDP 实时视窗挂载成功！")

            # 启动外部 Worker 网桥连接，将帧与交互双向桥接到 8799 WebUI
            self._bridge_send_queue = asyncio.Queue(maxsize=10)
            self._bridge_task = asyncio.create_task(self._run_bridge_client_loop())

        except Exception as e:
            logger.warning(f"[LiveBrowser] 异步挂载 CDP 异常: {e}")

    def detach_page(self) -> None:
        """解绑并关闭当前页面投屏。"""
        if self._bridge_task and not self._bridge_task.done():
            self._bridge_task.cancel()
            self._bridge_task = None

        if self.active_cdp:
            try:
                # 兼容 async 与 sync
                res = self.active_cdp.send("Page.stopScreencast")
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception:
                pass
            self.active_cdp = None
        self.active_page = None
        self._is_screencast_running = False
        self.last_frame = None
        self.page_info = {"url": "about:blank", "title": "浏览器已断开"}
        logger.info("[LiveBrowser] CDP 实时视窗已解绑。")

    def _setup_cdp_listeners(self) -> None:
        if not self.active_cdp:
            return

        def on_screencast_frame(params: Dict[str, Any]) -> None:
            data = params.get("data", "")
            session_id = params.get("sessionId")
            metadata = params.get("metadata", {})

            with self._frame_lock:
                self.last_frame = data
                self.last_frame_metadata = metadata
                self.last_frame_ts = time.time()
                try:
                    if self.active_page:
                        self.page_info["url"] = getattr(self.active_page, "url", "") or ""
                except Exception:
                    pass

            # 若网桥启用，推入发送队列
            if self._bridge_send_queue is not None:
                frame_msg = {
                    "type": "frame",
                    "data": data,
                    "metadata": metadata,
                    "info": self.page_info,
                }
                try:
                    self._bridge_send_queue.put_nowait(frame_msg)
                except (asyncio.QueueFull, queue.Full):
                    try:
                        self._bridge_send_queue.get_nowait()
                        self._bridge_send_queue.put_nowait(frame_msg)
                    except Exception:
                        pass

            # 确认应答帧以维持 CDP 帧流持续推送 (底层单向通知，零开销无 greenlet 切换异常)
            try:
                if self.active_cdp:
                    impl = getattr(self.active_cdp, "_impl_obj", None)
                    if impl and hasattr(impl, "_channel") and hasattr(impl._channel, "send_no_reply"):
                        impl._channel.send_no_reply("send", None, {"method": "Page.screencastFrameAck", "params": {"sessionId": session_id}})
                    else:
                        res = self.active_cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                        if asyncio.iscoroutine(res):
                            asyncio.create_task(res)
            except Exception:
                pass

        self.active_cdp.on("Page.screencastFrame", on_screencast_frame)

    def _start_screencast(self) -> None:
        if not self.active_cdp or self._is_screencast_running:
            return
        try:
            self.active_cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": 75,
                    "maxWidth": 1280,
                    "maxHeight": 800,
                    "everyNthFrame": 1,
                },
            )
            self._is_screencast_running = True
        except Exception as e:
            logger.warning(f"[LiveBrowser] 启动 Page.startScreencast 失败: {e}")

    def dispatch_mouse_event(self, event_type: str, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        """处理 Web 视窗传来的鼠标交互事件。"""
        if not self.active_page:
            return
        # 优先通过 CDP 底层派发输入事件（通过 loop.call_soon_threadsafe 跨线程安全派发）
        if self.active_cdp:
            impl = getattr(self.active_cdp, "_impl_obj", None)
            loop = getattr(getattr(impl, "_connection", None), "_loop", None)
            cdp_btn = {"left": "left", "right": "right", "middle": "middle"}.get(button, "none")
            cdp_type_map = {
                "click": "mousePressed",
                "down": "mousePressed",
                "up": "mouseReleased",
                "move": "mouseMoved",
                "wheel": "mouseWheel",
            }
            cdp_type = cdp_type_map.get(event_type, "mouseMoved")
            params = {"type": cdp_type, "x": x, "y": y, "button": cdp_btn, "clickCount": click_count}
            if event_type == "wheel":
                params["deltaY"] = y
                params["deltaX"] = x

            def _send_cdp_cmd(method: str, p: dict):
                try:
                    if impl and hasattr(impl, "_channel") and hasattr(impl._channel, "send_no_reply"):
                        impl._channel.send_no_reply("send", None, {"method": method, "params": p})
                except Exception:
                    pass

            try:
                if loop and loop.is_running() and impl and hasattr(impl, "_channel"):
                    if event_type in ("click", "down"):
                        move_params = {"type": "mouseMoved", "x": x, "y": y, "button": "none", "clickCount": 0}
                        loop.call_soon_threadsafe(_send_cdp_cmd, "Input.dispatchMouseEvent", move_params)

                    loop.call_soon_threadsafe(_send_cdp_cmd, "Input.dispatchMouseEvent", params)
                    if event_type == "click":
                        time.sleep(0.08)  # 拟人生理延时 80ms
                        params_up = dict(params, type="mouseReleased")
                        loop.call_soon_threadsafe(_send_cdp_cmd, "Input.dispatchMouseEvent", params_up)
                    return
                elif impl and hasattr(impl, "_channel") and hasattr(impl._channel, "send_no_reply"):
                    if event_type in ("click", "down"):
                        move_params = {"type": "mouseMoved", "x": x, "y": y, "button": "none", "clickCount": 0}
                        impl._channel.send_no_reply("send", None, {"method": "Input.dispatchMouseEvent", "params": move_params})

                    impl._channel.send_no_reply("send", None, {"method": "Input.dispatchMouseEvent", "params": params})
                    if event_type == "click":
                        time.sleep(0.08)
                        params_up = dict(params, type="mouseReleased")
                        impl._channel.send_no_reply("send", None, {"method": "Input.dispatchMouseEvent", "params": params_up})
                    return
            except Exception:
                pass

        try:
            if inspect.iscoroutinefunction(getattr(self.active_page, "goto", None)):
                mouse = self.active_page.mouse
                if event_type == "click":
                    asyncio.create_task(mouse.click(x, y, button=button))
                elif event_type == "move":
                    asyncio.create_task(mouse.move(x, y))
                elif event_type == "down":
                    asyncio.create_task(mouse.down(button=button))
                elif event_type == "up":
                    asyncio.create_task(mouse.up(button=button))
        except Exception as e:
            logger.debug(f"[LiveBrowser] 派发鼠标事件 {event_type} 异常: {e}")

    def dispatch_keyboard_event(self, event_type: str, key: str = "", text: str = "") -> None:
        """处理 Web 视窗传来的键盘交互事件。"""
        if not self.active_page:
            return
        if self.active_cdp:
            impl = getattr(self.active_cdp, "_impl_obj", None)
            loop = getattr(getattr(impl, "_connection", None), "_loop", None)

            def _send_cdp_cmd(method: str, p: dict):
                try:
                    if impl and hasattr(impl, "_channel") and hasattr(impl._channel, "send_no_reply"):
                        impl._channel.send_no_reply("send", None, {"method": method, "params": p})
                except Exception:
                    pass

            if loop and loop.is_running() and impl and hasattr(impl, "_channel"):
                if event_type == "press" and key:
                    loop.call_soon_threadsafe(_send_cdp_cmd, "Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key})
                    time.sleep(0.05)
                    loop.call_soon_threadsafe(_send_cdp_cmd, "Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
                    return
                elif event_type == "type" and text:
                    for ch in text:
                        loop.call_soon_threadsafe(_send_cdp_cmd, "Input.dispatchKeyEvent", {"type": "char", "text": ch})
                    return

        try:
            kb = self.active_page.keyboard
            if event_type == "type" and text:
                res = kb.type(text)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            elif event_type == "press" and key:
                res = kb.press(key)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
        except Exception as e:
            logger.debug(f"[LiveBrowser] 派发键盘事件 {event_type} 异常: {e}")

    async def _run_bridge_client_loop(self, target_url: str = "ws://127.0.0.1:8799/live/bridge") -> None:
        """作为客户端将本地 CDP 实时帧与指令推送到主 WebUI 8799 服务。"""
        import websockets

        logger.info(f"[LiveBrowserBridge] 正在连接主 WebUI 网桥: {target_url}")
        while True:
            try:
                async with websockets.connect(target_url, ping_interval=10, ping_timeout=10) as ws:
                    logger.info("[LiveBrowserBridge] ✅ 已成功接通主 WebUI 网桥！画面正实时推流到 8799/live")

                    async def send_frames_task():
                        while True:
                            if isinstance(self._bridge_send_queue, queue.Queue):
                                msg = await asyncio.to_thread(self._bridge_send_queue.get)
                            else:
                                msg = await self._bridge_send_queue.get()
                            await ws.send(json.dumps(msg))

                    async def recv_commands_task():
                        while True:
                            data_text = await ws.recv()
                            try:
                                payload = json.loads(data_text)
                                msg_type = payload.get("type")
                                if msg_type in ("click", "down", "up", "move", "wheel"):
                                    x = int(payload.get("x", 0))
                                    y = int(payload.get("y", 0))
                                    btn = payload.get("button", "left")
                                    self.dispatch_mouse_event(msg_type, x, y, button=btn)
                                elif msg_type in ("press", "type"):
                                    k = payload.get("key", "")
                                    t = payload.get("text", "")
                                    self.dispatch_keyboard_event(msg_type, key=k, text=t)
                            except Exception as ex:
                                logger.debug(f"[LiveBrowserBridge] 解析指令异常: {ex}")

                    sender = asyncio.create_task(send_frames_task())
                    receiver = asyncio.create_task(recv_commands_task())
                    done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[LiveBrowserBridge] 网桥重连中 ({e})...")
                await asyncio.sleep(2)


def mount_live_routes(app: Any) -> None:
    """直接将 Live 视窗与双向网桥路由挂载到 FastAPI app 实例上。"""
    from fastapi import WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse

    hub = LiveBrowserHub.get_hub()

    @app.get("/live", response_class=HTMLResponse)
    async def get_live_page():
        return HTMLResponse(content=LIVE_BROWSER_HTML)

    async def live_bridge_endpoint(websocket):
        """接收外部独立 Python 脚本或 CLI 推来的帧流，并双向反射鼠标指令。"""
        await websocket.accept()
        hub.bridge_clients.add(websocket)
        logger.info(f"[LiveBrowserBridge] 外部 Worker 已连接，当前网桥节点数: {len(hub.bridge_clients)}")

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    payload = json.loads(data_text)
                    if payload.get("type") == "frame":
                        with hub._frame_lock:
                            hub.last_frame = payload.get("data")
                            hub.last_frame_metadata = payload.get("metadata", {})
                            hub.last_frame_ts = time.time()
                            if "info" in payload and payload["info"]:
                                hub.page_info = payload["info"]
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            hub.bridge_clients.discard(websocket)
            logger.info(f"[LiveBrowserBridge] 外部 Worker 断开，当前网桥节点数: {len(hub.bridge_clients)}")

    async def live_websocket_endpoint(websocket):
        await websocket.accept()
        hub.clients.add(websocket)
        logger.info(f"[LiveBrowser] 前端视窗连接，当前在线视窗: {len(hub.clients)}")

        last_sent_ts = 0.0

        async def send_frames():
            nonlocal last_sent_ts
            try:
                while True:
                    await asyncio.sleep(0.04)  # ~25 FPS
                    if hub.last_frame and hub.last_frame_ts > last_sent_ts:
                        last_sent_ts = hub.last_frame_ts
                        msg = {
                            "type": "frame",
                            "data": hub.last_frame,
                            "info": {
                                "url": hub.page_info.get("url", ""),
                                "title": hub.page_info.get("title", ""),
                                "width": hub.last_frame_metadata.get("deviceWidth", 1280),
                                "height": hub.last_frame_metadata.get("deviceHeight", 720),
                            },
                        }
                        await websocket.send_text(json.dumps(msg))
            except Exception:
                pass

        send_task = asyncio.create_task(send_frames())

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    # 1. 派发给本地 active_page（如果在同一进程中）
                    payload = json.loads(data_text)
                    msg_type = payload.get("type")
                    if hub.active_page:
                        if msg_type in ("click", "down", "up", "move", "wheel"):
                            x = int(payload.get("x", 0))
                            y = int(payload.get("y", 0))
                            btn = payload.get("button", "left")
                            hub.dispatch_mouse_event(msg_type, x, y, button=btn)
                        elif msg_type in ("press", "type"):
                            k = payload.get("key", "")
                            t = payload.get("text", "")
                            hub.dispatch_keyboard_event(msg_type, key=k, text=t)

                    # 2. 如果存在外部网桥 Worker，把用户交互事件同步派发给 Worker
                    if hub.bridge_clients:
                        for b_ws in list(hub.bridge_clients):
                            asyncio.create_task(b_ws.send_text(data_text))

                except Exception as ex:
                    logger.debug(f"[LiveBrowser] 解析前端指令失败: {ex}")
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            hub.clients.discard(websocket)
            logger.info(f"[LiveBrowser] 前端视窗断开，当前在线视窗: {len(hub.clients)}")

    from starlette.routing import WebSocketRoute
    app.routes.append(WebSocketRoute("/live/bridge", live_bridge_endpoint))
    app.routes.append(WebSocketRoute("/live/ws", live_websocket_endpoint))


def create_live_router():
    """生成用于挂载到 WebUI 的 FastAPI APIRouter。"""
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse

    router = APIRouter()
    hub = LiveBrowserHub.get_hub()

    @router.get("/live", response_class=HTMLResponse)
    async def get_live_page():
        return HTMLResponse(content=LIVE_BROWSER_HTML)

    @router.websocket("/live/bridge")
    async def live_bridge_endpoint(websocket: WebSocket):
        """接收外部独立 Python 脚本或 CLI 推来的帧流，并双向反射鼠标指令。"""
        await websocket.accept()
        hub.bridge_clients.add(websocket)
        logger.info(f"[LiveBrowserBridge] 外部 Worker 已连接，当前网桥节点数: {len(hub.bridge_clients)}")

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    payload = json.loads(data_text)
                    if payload.get("type") == "frame":
                        with hub._frame_lock:
                            hub.last_frame = payload.get("data")
                            hub.last_frame_metadata = payload.get("metadata", {})
                            hub.last_frame_ts = time.time()
                            if "info" in payload and payload["info"]:
                                hub.page_info = payload["info"]
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            hub.bridge_clients.discard(websocket)
            logger.info(f"[LiveBrowserBridge] 外部 Worker 断开，当前网桥节点数: {len(hub.bridge_clients)}")

    @router.websocket("/live/ws")
    async def live_websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        hub.clients.add(websocket)
        logger.info(f"[LiveBrowser] 前端视窗连接，当前在线视窗: {len(hub.clients)}")

        last_sent_ts = 0.0

        async def send_frames():
            nonlocal last_sent_ts
            try:
                while True:
                    await asyncio.sleep(0.04)  # ~25 FPS
                    if hub.last_frame and hub.last_frame_ts > last_sent_ts:
                        last_sent_ts = hub.last_frame_ts
                        msg = {
                            "type": "frame",
                            "data": hub.last_frame,
                            "info": {
                                "url": hub.page_info.get("url", ""),
                                "title": hub.page_info.get("title", ""),
                                "width": hub.last_frame_metadata.get("deviceWidth", 1280),
                                "height": hub.last_frame_metadata.get("deviceHeight", 720),
                            },
                        }
                        await websocket.send_text(json.dumps(msg))
            except Exception:
                pass

        send_task = asyncio.create_task(send_frames())

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    # 1. 派发给本地 active_page（如果在同一进程中）
                    payload = json.loads(data_text)
                    msg_type = payload.get("type")
                    if hub.active_page:
                        if msg_type in ("click", "down", "up", "move", "wheel"):
                            x = int(payload.get("x", 0))
                            y = int(payload.get("y", 0))
                            btn = payload.get("button", "left")
                            hub.dispatch_mouse_event(msg_type, x, y, button=btn)
                        elif msg_type in ("press", "type"):
                            k = payload.get("key", "")
                            t = payload.get("text", "")
                            hub.dispatch_keyboard_event(msg_type, key=k, text=t)

                    # 2. 如果存在外部网桥 Worker，把用户交互事件同步派发给 Worker
                    if hub.bridge_clients:
                        for b_ws in list(hub.bridge_clients):
                            asyncio.create_task(b_ws.send_text(data_text))

                except Exception as ex:
                    logger.debug(f"[LiveBrowser] 解析前端指令失败: {ex}")
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            hub.clients.discard(websocket)
            logger.info(f"[LiveBrowser] 前端视窗断开，当前在线视窗: {len(hub.clients)}")

    return router


# ============================================================
# 现代化深色科技感 Web 前端页面 HTML 模板
# ============================================================
LIVE_BROWSER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CloakBrowser 实时视窗 (Live Screencast)</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0f1117;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      user-select: none;
    }
    header {
      background: #1a1e29;
      border-bottom: 1px solid #2d3748;
      padding: 10px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 52px;
      flex-shrink: 0;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 9999px;
      background: rgba(16, 185, 129, 0.15);
      color: #10b981;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: pulse 2s infinite;
    }
    .status-badge.disconnected {
      background: rgba(239, 68, 68, 0.15);
      color: #ef4444;
      border-color: rgba(239, 68, 68, 0.3);
    }
    .status-badge.disconnected .status-dot {
      background: #ef4444;
      box-shadow: none;
      animation: none;
    }
    @keyframes pulse {
      0% { transform: scale(0.95); opacity: 0.8; }
      50% { transform: scale(1.15); opacity: 1; }
      100% { transform: scale(0.95); opacity: 0.8; }
    }
    .url-bar {
      flex: 1;
      margin: 0 16px;
      background: #0b0d13;
      border: 1px solid #2d3748;
      border-radius: 8px;
      padding: 6px 14px;
      display: flex;
      align-items: center;
      gap: 8px;
      overflow: hidden;
    }
    .url-text {
      color: #94a3b8;
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: monospace;
    }
    .title-text {
      color: #f8fafc;
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
    }
    .btn-group {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    button.btn {
      background: #2d3748;
      color: #f8fafc;
      border: none;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    button.btn:hover {
      background: #3b475c;
    }
    main {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #090a0f;
      position: relative;
      overflow: hidden;
    }
    #viewport-container {
      position: relative;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.7);
      border-radius: 6px;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #000;
      cursor: crosshair;
    }
    #screen-img {
      display: block;
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      pointer-events: none;
    }
    .overlay-tip {
      position: absolute;
      top: 16px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(59, 130, 246, 0.4);
      color: #93c5fd;
      padding: 8px 16px;
      border-radius: 20px;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
      z-index: 10;
      pointer-events: none;
      animation: fadeIn 0.3s ease;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translate(-50%, -10px); } to { opacity: 1; transform: translate(-50%, 0); } }
    footer {
      background: #131722;
      border-top: 1px solid #1e293b;
      padding: 8px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 42px;
      font-size: 12px;
      color: #64748b;
      flex-shrink: 0;
    }
    .shortcut-chips {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .chip {
      background: #1e293b;
      color: #94a3b8;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      cursor: pointer;
      border: 1px solid #334155;
    }
    .chip:hover {
      background: #334155;
      color: #f1f5f9;
    }
  </style>
</head>
<body>
  <header>
    <div id="status-badge" class="status-badge">
      <div class="status-dot"></div>
      <span id="status-text">已连接实时流</span>
    </div>
    <div class="url-bar">
      <span class="title-text" id="page-title">等待浏览器画面...</span>
      <span class="url-text" id="page-url"></span>
    </div>
    <div class="btn-group">
      <button class="btn" id="btn-enter" title="触发键盘回车">⏎ Enter</button>
      <button class="btn" id="btn-fullscreen" title="全屏查看">⛶ 全屏</button>
    </div>
  </header>

  <main>
    <div class="overlay-tip" id="overlay-tip">
      <span>💡 鼠标在此画布中可直接点击旋转滑块、拼图、输入或拖拽，人机协同零感知</span>
    </div>
    <div id="viewport-container">
      <img id="screen-img" src="" alt="等待浏览器启动...">
    </div>
  </main>

  <footer>
    <div class="shortcut-chips">
      <span>快捷键：</span>
      <span class="chip" onclick="sendKey('Enter')">Enter</span>
      <span class="chip" onclick="sendKey('Tab')">Tab</span>
      <span class="chip" onclick="sendKey('Backspace')">Backspace</span>
    </div>
    <div id="stats">FPS: 0 | 延时: -- ms</div>
  </footer>

  <script>
    const img = document.getElementById('screen-img');
    const container = document.getElementById('viewport-container');
    const statusBadge = document.getElementById('status-badge');
    const statusText = document.getElementById('status-text');
    const pageTitle = document.getElementById('page-title');
    const pageUrl = document.getElementById('page-url');
    const stats = document.getElementById('stats');

    let ws = null;
    let frameCount = 0;
    let lastFpsUpdate = Date.now();
    let naturalWidth = 1280;
    let naturalHeight = 720;

    function connectWs() {
      const loc = window.location;
      const wsProto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsProto}//${loc.host}/live/ws`;

      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        statusBadge.className = 'status-badge';
        statusText.innerText = '已连接实时流';
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'frame' && msg.data) {
            img.src = 'data:image/jpeg;base64,' + msg.data;
            frameCount++;
            const now = Date.now();
            if (now - lastFpsUpdate >= 1000) {
              const fps = Math.round((frameCount * 1000) / (now - lastFpsUpdate));
              stats.innerText = `FPS: ${fps} | 原生视窗: ${naturalWidth}×${naturalHeight}`;
              frameCount = 0;
              lastFpsUpdate = now;
            }
          }
          if (msg.info) {
            if (msg.info.title) pageTitle.innerText = msg.info.title;
            if (msg.info.url) pageUrl.innerText = msg.info.url;
            if (msg.info.width) naturalWidth = msg.info.width;
            if (msg.info.height) naturalHeight = msg.info.height;
          }
        } catch (e) {}
      };

      ws.onclose = () => {
        statusBadge.className = 'status-badge disconnected';
        statusText.innerText = '已断开 (重连中...)';
        setTimeout(connectWs, 2000);
      };
    }

    function getNormalizedCoords(e) {
      const rect = img.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      const nw = naturalWidth || img.naturalWidth || 1280;
      const nh = naturalHeight || img.naturalHeight || 800;
      const scaleX = nw / rect.width;
      const scaleY = nh / rect.height;
      return {
        x: Math.round(Math.max(0, Math.min(nw, clickX * scaleX))),
        y: Math.round(Math.max(0, Math.min(nh, clickY * scaleY)))
      };
    }

    // 拖拽支持 (用于天爱旋转验证码滑块拖动及原生点击确权)
    let isDragging = false;

    container.addEventListener('mousedown', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      isDragging = true;
      const { x, y } = getNormalizedCoords(e);
      const button = e.button === 2 ? 'right' : 'left';
      ws.send(JSON.stringify({ type: 'down', x, y, button }));
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging || !ws || ws.readyState !== WebSocket.OPEN) return;
      const { x, y } = getNormalizedCoords(e);
      ws.send(JSON.stringify({ type: 'move', x, y }));
    });

    window.addEventListener('mouseup', (e) => {
      if (!isDragging) return;
      isDragging = false;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const { x, y } = getNormalizedCoords(e);
      const button = e.button === 2 ? 'right' : 'left';
      ws.send(JSON.stringify({ type: 'up', x, y, button }));
    });

    container.addEventListener('wheel', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: 'wheel', x: 0, y: Math.round(e.deltaY) }));
    });

    window.addEventListener('keydown', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (['F12', 'F5', 'Control', 'Alt'].includes(e.key)) return;
      ws.send(JSON.stringify({ type: 'press', key: e.key }));
    });

    function sendKey(key) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'press', key: key }));
      }
    }

    document.getElementById('btn-enter').onclick = () => sendKey('Enter');
    document.getElementById('btn-fullscreen').onclick = () => {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
      } else {
        document.exitFullscreen();
      }
    };

    connectWs();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI(title="LiveBrowserHub Standalone Daemon")
    mount_live_routes(app)
    print("[LiveBrowserHub] Daemon listening on http://127.0.0.1:8799/live")
    uvicorn.run(app, host="127.0.0.1", port=8799, log_level="warning")
