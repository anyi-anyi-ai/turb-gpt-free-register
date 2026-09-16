# -*- coding: utf-8 -*-
"""ChatGPT 账号拟人养号与后期维护引擎。

特性：
1. 纯指纹浏览器驱动（优先 CloakBrowser + Playwright，兼容 RoxyBrowser）。
2. StorageState 免密秒进：复用上次 Cookies/LocalStorage，失效时自动账密+2FA 补登。
3. 三阶贝塞尔曲线人类鼠标移动模拟（Ghost-Cursor 算法），摆脱瞬移与直线轨迹。
4. 人类打字延迟与拟真退格（Typo & Correction）。
5. 混合概率行为池（自然问答、翻阅历史、探索浏览），结合本地多语种高质量语料库。
6. 代理绑定与同国别一致性保障。
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import warming as _cfg
from core import db
from core.humanize import delay as human_delay

logger = logging.getLogger(__name__)
_LOG_DIR = Path(__file__).resolve().parent.parent / "注册日志"


def _log_path(email: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-_@" else "_" for c in str(email).strip().lower())
    return _LOG_DIR / f"warming_{safe}.log"


def _append_log(email: str, line: str, *, clear: bool = False) -> None:
    p = _log_path(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M:%S")
    mode = "w" if clear else "a"
    try:
        with p.open(mode, encoding="utf-8") as f:
            f.write(f"{stamp} [INFO] {line}\n")
    except Exception:
        pass


# ==============================================================================
# 1. 三阶贝塞尔曲线鼠标轨迹模拟 (Cubic Bézier Curve Trajectory)
# ==============================================================================

class BezierMouse:
    """模拟人类鼠标加速度、抖动与平滑轨迹。"""

    def __init__(self, page):
        self.page = page
        self.current_x = float(random.randint(200, 500))
        self.current_y = float(random.randint(150, 400))

    @staticmethod
    def _ease_in_out(t: float) -> float:
        """余弦加速度曲线，两端慢中间快。"""
        return 0.5 * (1.0 - math.cos(math.pi * t))

    def _generate_bezier_points(
        self,
        p0: tuple[float, float],
        p3: tuple[float, float],
        steps: int = 28,
    ) -> list[tuple[float, float]]:
        x0, y0 = p0
        x3, y3 = p3
        dx = x3 - x0
        dy = y3 - y0
        dist = math.hypot(dx, dy)

        # 随机偏转控制点
        spread = min(120.0, max(25.0, dist * 0.25))
        jitter_x1 = random.uniform(-spread, spread)
        jitter_y1 = random.uniform(-spread, spread)
        jitter_x2 = random.uniform(-spread, spread)
        jitter_y2 = random.uniform(-spread, spread)

        p1 = (x0 + dx * random.uniform(0.15, 0.45) + jitter_x1, y0 + dy * random.uniform(0.15, 0.45) + jitter_y1)
        p2 = (x0 + dx * random.uniform(0.55, 0.85) + jitter_x2, y0 + dy * random.uniform(0.55, 0.85) + jitter_y2)

        points = []
        for i in range(1, steps + 1):
            raw_t = i / float(steps)
            t = self._ease_in_out(raw_t)
            # Cubic Bézier: B(t) = (1-t)^3*P0 + 3(1-t)^2*t*P1 + 3(1-t)*t^2*P2 + t^3*P3
            cx = (
                (1 - t) ** 3 * p0[0]
                + 3 * (1 - t) ** 2 * t * p1[0]
                + 3 * (1 - t) * t ** 2 * p2[0]
                + t ** 3 * p3[0]
            )
            cy = (
                (1 - t) ** 3 * p0[1]
                + 3 * (1 - t) ** 2 * t * p1[1]
                + 3 * (1 - t) * t ** 2 * p2[1]
                + t ** 3 * p3[1]
            )
            # 人手微颤抖动 (Micro-jitter)
            if i < steps:
                cx += random.uniform(-1.0, 1.0)
                cy += random.uniform(-1.0, 1.0)
            points.append((cx, cy))
        return points

    def move_to(self, target_x: float, target_y: float) -> None:
        p0 = (self.current_x, self.current_y)
        p3 = (float(target_x), float(target_y))
        dist = math.hypot(p3[0] - p0[0], p3[1] - p0[1])
        steps = max(15, min(45, int(dist / 22.0)))
        points = self._generate_bezier_points(p0, p3, steps=steps)

        for px, py in points:
            try:
                self.page.mouse.move(px, py)
            except Exception:
                pass
            time.sleep(random.uniform(0.005, 0.016))

        self.current_x = p3[0]
        self.current_y = p3[1]

    def click_element(self, locator) -> bool:
        try:
            box = locator.bounding_box()
            if not box:
                locator.click(timeout=3000)
                return True
            # 点击元素内部偏中心随机位置，避开边缘
            target_x = box["x"] + box["width"] * random.uniform(0.30, 0.70)
            target_y = box["y"] + box["height"] * random.uniform(0.30, 0.70)
            self.move_to(target_x, target_y)
            time.sleep(random.uniform(0.06, 0.16))
            self.page.mouse.down()
            time.sleep(random.uniform(0.05, 0.12))
            self.page.mouse.up()
            return True
        except Exception:
            try:
                locator.click(timeout=3000)
                return True
            except Exception:
                return False


# ==============================================================================
# 2. 拟真键盘打字机 (Human Typing with Jitter & Typo Correction)
# ==============================================================================

class HumanTyper:
    """模拟人类节奏打字，包含微延迟、标点停顿与偶发输入修正。"""

    @staticmethod
    def type_text(page, text: str, wpm: int = 180) -> None:
        if not text:
            return
        # 基础字符延迟 (秒)
        base_delay = 60.0 / (max(80, min(320, wpm)) * 5)

        for char in text:
            # 2% 概率模拟误触相邻键后退格修正
            if random.random() < 0.02 and char.isascii() and char.isalnum():
                typo_char = random.choice("abcdefghijklmnopqrstuvwxyz")
                try:
                    page.keyboard.type(typo_char)
                    time.sleep(random.uniform(0.12, 0.28))
                    page.keyboard.press("Backspace")
                    time.sleep(random.uniform(0.08, 0.18))
                except Exception:
                    pass

            try:
                page.keyboard.type(char)
            except Exception:
                pass

            # 正常敲击延迟（高斯分布）
            jitter = max(0.02, random.gauss(base_delay, base_delay * 0.35))
            time.sleep(jitter)

            # 遇到逗号、句号、问号时有人类构思短暂停顿
            if char in (",", "，", ".", "。", "?", "？", "!", "！", "\n"):
                time.sleep(random.uniform(0.20, 0.50))


# ==============================================================================
# 3. 本地语料库加载器
# ==============================================================================

def load_random_prompt() -> tuple[str, str]:
    """从本地语料库随机抽取一条高质量提问。返回 (category, prompt)。"""
    corpus_rel = getattr(_cfg, "WARMING_CORPUS_FILE", "config/prompts_corpus.json")
    corpus_path = Path(__file__).resolve().parent.parent / corpus_rel
    try:
        if corpus_path.exists():
            data = json.loads(corpus_path.read_text(encoding="utf-8"))
            categories = data.get("categories") or {}
            if categories:
                cat_name = random.choice(list(categories.keys()))
                prompts = categories[cat_name]
                if prompts:
                    return cat_name, random.choice(prompts)
    except Exception as exc:
        logger.warning("[Warming] 加载本地语料库失败，使用内置兜底题库: %s", exc)

    # 兜底默认语料
    fallback_prompts = [
        ("daily", "请推荐几道简单又营养好吃的家常快手菜做法。"),
        ("study", "费曼学习法的核心理念是什么？请举一个简短的例子说明。"),
        ("tech", "用 Python 写一个简单的倒计时工具，请给出代码示例。"),
        ("lifestyle", "如何有效改善睡眠质量？有哪些不依赖药物的生活习惯建议？"),
    ]
    return random.choice(fallback_prompts)


# ==============================================================================
# 4. 养号执行核心服务
# ==============================================================================

class AccountWarmingService:
    """单个账号拟人养号完整生命周期。"""

    def __init__(self, account_data: dict, *, headless: bool | None = None):
        self.account = dict(account_data)
        self.email = str(self.account.get("email") or "").strip()
        self.acc_id = int(self.account.get("id") or 0)
        self.headless = bool(getattr(_cfg, "WARMING_HEADLESS", False)) if headless is None else bool(headless)
        self.driver = None
        self.page = None
        self.mouse = None

    def _resolve_proxy(self) -> str | None:
        """依策略优先获取该账号注册时的粘性代理或同国别代理。"""
        use_sticky = bool(getattr(_cfg, "WARMING_USE_STICKY_PROXY", True))
        if use_sticky:
            saved_proxy = (
                self.account.get("proxy")
                or self.account.get("proxy_used")
                or self.account.get("live_check_proxy_used")
            )
            if saved_proxy:
                _append_log(self.email, f"[代理] 复用账号历史绑定出口: {saved_proxy}")
                return str(saved_proxy).strip()

        # 回退代理池
        try:
            from config.proxy import pick_proxy
            p = pick_proxy(for_registration=True)
            if p:
                _append_log(self.email, f"[代理] 从代理池分配节点: {p}")
                return p
        except Exception:
            pass
        return None

    def _init_browser(self) -> None:
        """启动指纹浏览器并注入 StorageState 登录态。"""
        proxy = self._resolve_proxy()
        storage_state = self.account.get("storage_state")

        _append_log(self.email, f"[浏览器] 正在启动指纹浏览器 (headless={self.headless}, 有旧状态={bool(storage_state)})...")
        from core.cloakbrowser_driver import build_cloak_driver

        self.driver, _ = build_cloak_driver(
            proxy=proxy,
            storage_state=storage_state if isinstance(storage_state, dict) else None,
            headless=self.headless,
        )
        self.page = getattr(self.driver, "page", None)
        if not self.page:
            raise RuntimeError("浏览器启动失败，无法获取 page 实例")
        self.mouse = BezierMouse(self.page)

    def _check_session_alive(self) -> bool:
        """检测当前 ChatGPT 登录态是否有效。"""
        try:
            script = """async () => {
                try {
                    const res = await fetch('/api/auth/session', {credentials: 'include'});
                    if (!res.ok) return {ok: false, status: res.status};
                    const j = await res.json();
                    return {ok: true, has_token: !!j.accessToken, user: j.user ? j.user.email : null};
                } catch (e) {
                    return {ok: false, error: String(e)};
                }
            }"""
            data = self.page.evaluate(script)
            if isinstance(data, dict) and data.get("ok") and data.get("has_token"):
                _append_log(self.email, f"[Session] 登录态有效 (user={data.get('user')})")
                return True
        except Exception as exc:
            logger.debug("[Warming] Session 检查异常: %s", exc)
        return False

    def _try_relogin(self) -> bool:
        """当 Session 失效时，尝试自动输入密码与 2FA TOTP 重新登录。"""
        _append_log(self.email, "[登录] Session 已失效，尝试执行自动登录...")
        password = str(self.account.get("password") or self.account.get("registration_password") or "").strip()
        totp_secret = str(self.account.get("totp_secret") or "").strip()

        if not password:
            _append_log(self.email, "[登录] 缺少保存的账号密码，无法自动补登")
            return False

        try:
            # 1. 寻找登录入口按钮
            login_btn = self.page.locator("button:has-text('Log in'), a:has-text('Log in'), button:has-text('登录')").first
            if login_btn.is_visible(timeout=4000):
                self.mouse.click_element(login_btn)
                time.sleep(2)

            # 2. 填写邮箱
            email_input = self.page.locator("input[type='email'], input[name='username'], input[name='email']").first
            if email_input.is_visible(timeout=5000):
                self.mouse.click_element(email_input)
                HumanTyper.type_text(self.page, self.email, wpm=200)
                time.sleep(0.5)
                continue_btn = self.page.locator("button:has-text('Continue'), button:has-text('继续')").first
                if continue_btn.is_visible(timeout=3000):
                    self.mouse.click_element(continue_btn)
                    time.sleep(2.5)

            # 3. 填写密码
            pwd_input = self.page.locator("input[type='password'], input[name='password']").first
            if pwd_input.is_visible(timeout=6000):
                self.mouse.click_element(pwd_input)
                HumanTyper.type_text(self.page, password, wpm=190)
                time.sleep(0.5)
                submit_btn = self.page.locator("button:has-text('Continue'), button:has-text('Log in'), button:has-text('继续')").first
                if submit_btn.is_visible(timeout=3000):
                    self.mouse.click_element(submit_btn)
                    time.sleep(3.5)

            # 4. 如果触发 2FA (TOTP)
            otp_input = self.page.locator("input[name='code'], input[placeholder*='code'], input[autocomplete='one-time-code']").first
            if otp_input.is_visible(timeout=4000) and totp_secret:
                import pyotp
                code = pyotp.TOTP(totp_secret).now()
                _append_log(self.email, f"[2FA] 自动计算 TOTP 动态码并填入: {code}")
                self.mouse.click_element(otp_input)
                HumanTyper.type_text(self.page, code, wpm=150)
                time.sleep(1)
                otp_submit = self.page.locator("button:has-text('Continue'), button:has-text('Verify')").first
                if otp_submit.is_visible(timeout=2000):
                    self.mouse.click_element(otp_submit)
                    time.sleep(3)

            # 等待回跳 chatgpt.com
            time.sleep(4)
            return self._check_session_alive()
        except Exception as exc:
            _append_log(self.email, f"[登录] 自动重登异常: {exc}")
            return False

    def _action_natural_chat(self) -> dict:
        """行为1：发送一条日常生活/学习提问，模拟阅读与互动。"""
        category, prompt = load_random_prompt()
        _append_log(self.email, f"[动作] 执行自然问答 (分类: {category}): {prompt}")

        # 1. 查找输入框 (#prompt-textarea 或 div[contenteditable="true"])
        input_locator = self.page.locator("#prompt-textarea, div[contenteditable='true'], textarea[data-id='root']").first
        if not input_locator.is_visible(timeout=8000):
            raise RuntimeError("未在页面中找到 ChatGPT 提示词输入框")

        # 2. 模拟鼠标平滑滑向输入框并点击
        self.mouse.click_element(input_locator)
        time.sleep(random.uniform(0.3, 0.8))

        # 3. 拟人键盘打字
        wpm = int(getattr(_cfg, "WARMING_TYPE_SPEED_WPM", 180) or 180)
        HumanTyper.type_text(self.page, prompt, wpm=wpm)
        time.sleep(random.uniform(0.4, 1.2))

        # 4. 发送提问 (按 Enter 或点击发送按钮)
        if random.random() < 0.75:
            self.page.keyboard.press("Enter")
        else:
            send_btn = self.page.locator("button[data-testid='send-button'], button[aria-label='Send prompt']").first
            if send_btn.is_visible(timeout=2000):
                self.mouse.click_element(send_btn)
            else:
                self.page.keyboard.press("Enter")

        _append_log(self.email, "[动作] 提问已发送，正在等待 AI 流式输出...")

        # 5. 等待流式输出完成（最多等待 60 秒）
        start_wait = time.time()
        completed = False
        while time.time() - start_wait < 60:
            # 视线轻度上下滚动
            time.sleep(random.uniform(2.5, 4.5))
            try:
                self.page.mouse.wheel(delta_x=0, delta_y=random.randint(40, 120))
            except Exception:
                pass
            # 判断 stop 按钮消失或重新出现 send 按钮
            stop_btn = self.page.locator("button[data-testid='stop-button'], button[aria-label='Stop streaming']").first
            send_btn = self.page.locator("button[data-testid='send-button']").first
            if not stop_btn.is_visible(timeout=500) and send_btn.is_visible(timeout=500):
                completed = True
                break

        _append_log(self.email, f"[动作] 回答生成完毕 (耗时: {time.time() - start_wait:.1f}s)")

        # 6. 随机互动：复制与点赞
        if random.random() < float(getattr(_cfg, "WARMING_COPY_PROBABILITY", 0.40)):
            try:
                copy_btn = self.page.locator("button[aria-label='Copy'], button[data-testid='copy-turn-action-button']").last
                if copy_btn.is_visible(timeout=1500):
                    time.sleep(random.uniform(1.0, 2.5))
                    self.mouse.click_element(copy_btn)
                    _append_log(self.email, "[动作] 模拟点击了回复内容【复制】按钮")
            except Exception:
                pass

        if random.random() < float(getattr(_cfg, "WARMING_UPVOTE_PROBABILITY", 0.25)):
            try:
                upvote_btn = self.page.locator("button[aria-label='Good response'], button[aria-label='Thumbs up']").last
                if upvote_btn.is_visible(timeout=1500):
                    time.sleep(random.uniform(0.8, 2.0))
                    self.mouse.click_element(upvote_btn)
                    _append_log(self.email, "[动作] 模拟点击了回复内容【点赞】按钮")
            except Exception:
                pass

        return {"action": "natural_chat", "prompt": prompt, "category": category}

    def _action_browse_history(self) -> dict:
        """行为2：翻阅历史会话，模拟人类回看旧对话。"""
        _append_log(self.email, "[动作] 执行历史会话翻阅...")
        history_links = self.page.locator("nav a[href*='/c/']").all()
        if history_links and len(history_links) > 0:
            target_link = random.choice(history_links[:5])
            self.mouse.click_element(target_link)
            time.sleep(random.uniform(3.0, 6.0))
            # 模拟视线滚动阅读
            for _ in range(random.randint(2, 5)):
                self.page.mouse.wheel(delta_x=0, delta_y=random.randint(150, 300))
                time.sleep(random.uniform(1.5, 3.5))
            for _ in range(random.randint(1, 3)):
                self.page.mouse.wheel(delta_x=0, delta_y=random.randint(-200, -100))
                time.sleep(random.uniform(1.2, 2.5))
            _append_log(self.email, "[动作] 历史会话翻阅完成")
            return {"action": "browse_history", "detail": "viewed_past_chat"}
        else:
            _append_log(self.email, "[动作] 当前账号无历史会话，回退到主页平滑浏览")
            return self._action_browse_home()

    def _action_browse_home(self) -> dict:
        """行为3：主页漫游，鼠标随机晃动、滚动与探索。"""
        _append_log(self.email, "[动作] 执行主页与探索漫游...")
        for _ in range(random.randint(3, 6)):
            # 随机移动鼠标
            rx = random.randint(150, 800)
            ry = random.randint(100, 600)
            self.mouse.move_to(rx, ry)
            time.sleep(random.uniform(0.5, 1.5))
            # 随机小幅度滚动
            self.page.mouse.wheel(delta_x=0, delta_y=random.randint(-100, 150))
            time.sleep(random.uniform(1.0, 2.5))

        # 尝试寻找 Explore GPTs 或侧边栏悬停
        gpts_link = self.page.locator("a[href*='/gpts'], a:has-text('Explore GPTs')").first
        if gpts_link.is_visible(timeout=1500):
            try:
                box = gpts_link.bounding_box()
                if box:
                    self.mouse.move_to(box["x"] + 20, box["y"] + 10)
                    time.sleep(1.5)
            except Exception:
                pass
        return {"action": "browse_home", "detail": "home_exploration"}

    def run(self) -> dict:
        """执行一次完整的养号流程。"""
        start_time = time.time()
        _append_log(self.email, "=" * 55, clear=False)
        _append_log(self.email, f"[养号] 开始为账号执行拟人养号: {self.email} (ID: {self.acc_id})")

        result = {
            "ok": False,
            "status": "failed",
            "email": self.email,
            "account_id": self.acc_id,
            "started_at": datetime.now().isoformat(),
            "action": None,
            "prompt": None,
            "dwell_seconds": 0,
            "storage_state": None,
            "error": None,
        }

        try:
            # 1. 启动指纹浏览器
            self._init_browser()

            # 2. 打开 ChatGPT 首页
            _append_log(self.email, "[导航] 访问 https://chatgpt.com ...")
            self.page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(3.0, 5.0))

            # 3. 检查登录态
            is_logged_in = self._check_session_alive()
            if not is_logged_in:
                if not self._try_relogin():
                    raise RuntimeError("账号未登录且自动重登失败，可能需手动核验或已废弃")

            # 4. 根据权重抽取本次养号动作
            w_chat = int(getattr(_cfg, "WARMING_WEIGHT_CHAT", 65))
            w_hist = int(getattr(_cfg, "WARMING_WEIGHT_HISTORY", 20))
            w_brow = int(getattr(_cfg, "WARMING_WEIGHT_BROWSE", 15))
            choices = ["chat", "history", "browse"]
            weights = [w_chat, w_hist, w_brow]
            selected_mode = random.choices(choices, weights=weights, k=1)[0]

            action_data = {}
            if selected_mode == "chat":
                action_data = self._action_natural_chat()
            elif selected_mode == "history":
                action_data = self._action_browse_history()
            else:
                action_data = self._action_browse_home()

            # 5. 补充自然停留时长 (Dwell Time)
            target_dwell = random.uniform(
                float(getattr(_cfg, "WARMING_MIN_DWELL_SECONDS", 35.0)),
                float(getattr(_cfg, "WARMING_MAX_DWELL_SECONDS", 90.0)),
            )
            elapsed = time.time() - start_time
            remain_dwell = max(5.0, target_dwell - elapsed)
            _append_log(self.email, f"[停留] 模拟自然停留在页面阅读思考: {remain_dwell:.1f}s")
            time.sleep(remain_dwell)

            # 6. 保存最新的 StorageState 登录态
            try:
                context = getattr(self.driver, "context", None)
                if context:
                    new_state = context.storage_state()
                    result["storage_state"] = new_state
                    _append_log(self.email, "[状态] 已捕获最新 StorageState (Cookies+LocalStorage)")
            except Exception as exc:
                logger.debug("[Warming] 导出 StorageState 失败: %s", exc)

            total_elapsed = time.time() - start_time
            result["ok"] = True
            result["status"] = "success"
            result["action"] = action_data.get("action")
            result["prompt"] = action_data.get("prompt")
            result["dwell_seconds"] = total_elapsed
            _append_log(self.email, f"[完成] 养号成功！总耗时: {total_elapsed:.1f}s")

        except Exception as exc:
            err_msg = str(exc)
            logger.error("[Warming] 养号执行失败: %s - %s", self.email, err_msg)
            _append_log(self.email, f"[失败] 养号过程异常: {err_msg}")
            result["ok"] = False
            result["status"] = "failed"
            result["error"] = err_msg

        finally:
            # 优雅退出浏览器
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            # 写回数据库
            db.update_account_warming_result(self.acc_id, result)

        return result


def warm_single_account(account_id: int, *, trigger: str = "manual", headless: bool | None = None) -> dict:
    """对指定 ID 的账号执行单次养号。"""
    acc = db.get_account(account_id)
    if not acc:
        return {"ok": False, "error": f"账号不存在 (ID: {account_id})"}

    if not db.claim_account_warming(account_id, trigger=trigger):
        return {"ok": False, "error": "账号已在养号队列或正在执行中"}

    db.mark_account_warming_running(account_id)
    service = AccountWarmingService(acc, headless=headless)
    return service.run()
