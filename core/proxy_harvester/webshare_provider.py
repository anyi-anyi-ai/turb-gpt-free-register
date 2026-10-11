import base64
import io
import json
import logging
import random
import re
import string
import sys
import time
from pathlib import Path
from typing import Any
from PIL import Image

from playwright.sync_api import BrowserContext, Page, sync_playwright

from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile
from core.human_dynamics import BiometricKeyboard, BiometricMouse
from core.proxy_harvester.base import BaseProxyProvider
from core.proxy_harvester.models import ProxyAccountRecord

try:
    from playwright_stealth import stealth_sync
except Exception:
    stealth_sync = None

_ROOT = Path(__file__).resolve().parent.parent.parent
_SOLVER_PATH = _ROOT / "references" / "captcha-solver"
if str(_SOLVER_PATH) not in sys.path:
    sys.path.insert(0, str(_SOLVER_PATH))

try:
    from recaptcha.onnx_classifier import get_classifier
except Exception:
    get_classifier = None

try:
    from core.audio_solver.solver import get_whisper_model
except Exception:
    get_whisper_model = None

logger = logging.getLogger("harvester.webshare")

WEBSHARE_REGISTER_URL = "https://dashboard.webshare.io/register"
WEBSHARE_LIST_URL = "https://dashboard.webshare.io/proxy/list"
WEBSHARE_API_BASE = "https://proxy.webshare.io/api/v2"


def _generate_strong_password(length: int = 16) -> str:
    """生成满足高复杂度校验的密码。"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%^&*"),
    ]
    pwd += [random.choice(chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


class WebshareHarvester(BaseProxyProvider):
    """Webshare (3xK 架构) 自动化注册器。
    
    单账号配发 100 节点 (端口 3129)，支持独立 Session 代理注册与无头/智能混合模式。
    """
    provider_name: str = "webshare"

    def __init__(
        self,
        headless: bool = True,
        upstream_proxy: str | None = None,
        timeout_sec: int = 75,
    ):
        self.headless = headless
        self.upstream_proxy = upstream_proxy
        self.timeout_sec = timeout_sec

    def harvest_account(
        self,
        email: str,
        activation_link_getter: Any,
        email_source: str = "outlook",
        batch_id: str = "",
        verification_code_getter: Any = None,
        **kwargs: Any,
    ) -> ProxyAccountRecord | None:
        """执行单账号完整注册、过盾、激活与 100 节点导出流程。"""
        password = _generate_strong_password(16)
        logger.info(f"🚀 [Webshare] 开始为 {email} 注册账号 (上游出口: {self.upstream_proxy or '直连'})...")

        driver = None
        try:
            from core.cloakbrowser_driver import build_cloak_driver
            driver, _ = build_cloak_driver(
                proxy=self.upstream_proxy,
                headless=self.headless,
                account_email=email,
            )
            page: Page = driver.page
            context: BrowserContext = driver.context
        except Exception as cloak_err:
            logger.warning(f"⚠️ [Webshare] 拉起 CloakBrowser 异常 ({cloak_err})，回退到基础 Playwright 内核...")
            p_instance = sync_playwright().start()
            proxy_cfg = None
            if self.upstream_proxy:
                match = re.match(r"^(?:https?|socks5h?)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$", self.upstream_proxy)
                if match:
                    user, pwd, host, port = match.groups()
                    proxy_cfg = {"server": f"http://{host}:{port}"}
                    if user and pwd:
                        proxy_cfg["username"] = user
                        proxy_cfg["password"] = pwd

            browser = p_instance.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                proxy=proxy_cfg,
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 850},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = context.new_page()

        # 监听注册 API 响应与去重预警
        api_registered = [False]
        reg_duplicate = [False]
        def on_res(res):
            try:
                if res.request.method == "POST":
                    u = res.url.lower()
                    if any(k in u for k in ("/register", "/user", "/signup", "/account", "/auth")):
                        if res.status in (200, 201):
                            api_registered[0] = True
                            logger.info(f"🎉 [Webshare API] 接收到注册成功响应: {res.url} (HTTP {res.status})")
                        else:
                            try:
                                body_preview = res.text()[:250]
                                if any(k in body_preview.lower() for k in ("already exists", "already registered", "user with this email already exists")):
                                    reg_duplicate[0] = True
                                    logger.warning(f"⚠️ [Webshare API] 该邮箱已在平台注册过: {body_preview}")
                            except Exception:
                                body_preview = ""
                            logger.warning(f"⚠️ [Webshare API] 注册响应状态 HTTP {res.status} ({res.url}): {body_preview}")
            except Exception:
                pass
        page.on("response", on_res)

        try:
            # 1. 访问注册页
            logger.info("🌐 打开 Webshare 注册入口...")
            page.goto(WEBSHARE_REGISTER_URL, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # 2. 检查并过盾 (Cloudflare Turnstile)
            self._handle_anti_bot(page)

            # 3. 拟人化行为填充注册表单 (贝塞尔轨迹移动 + 真实击键生物飞行时延)
            logger.info(f"📝 正在以生物拟人行为填写注册表单 ({email})...")
            email_input = page.locator('input[type="email"], input[name="email"]').first
            email_input.wait_for(state="visible", timeout=20000)
            email_input.scroll_into_view_if_needed()
            BiometricMouse.click_playwright_sync(page, email_input)
            page.wait_for_timeout(random.randint(180, 320))
            BiometricKeyboard.type_playwright_sync(page, email, typo_rate=0.0)
            page.wait_for_timeout(random.randint(250, 450))

            # 校验并兜底邮箱
            if email_input.input_value().strip() != email:
                email_input.fill(email)
                page.wait_for_timeout(200)

            pwd_input = page.locator('input[type="password"], input[name="password"]').first
            pwd_input.scroll_into_view_if_needed()
            BiometricMouse.click_playwright_sync(page, pwd_input)
            page.wait_for_timeout(random.randint(180, 320))
            BiometricKeyboard.type_playwright_sync(page, password, typo_rate=0.0)
            page.wait_for_timeout(random.randint(300, 500))

            # 校验并兜底密码
            if pwd_input.input_value().strip() != password:
                pwd_input.fill(password)
                page.wait_for_timeout(200)

            # React 18/Material-UI 受控组件 SyntheticEvent 强制同步
            try:
                page.evaluate("""({ em, pw }) => {
                    function setInputVal(el, val) {
                        if (!el) return;
                        const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                        if (desc && desc.set) {
                            desc.set.call(el, val);
                        } else {
                            el.value = val;
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                    }
                    const emailEl = document.querySelector('input[type="email"], input[name="email"]');
                    const pwdEl = document.querySelector('input[type="password"], input[name="password"]');
                    setInputVal(emailEl, em);
                    setInputVal(pwdEl, pw);
                }""", {"em": email, "pw": password})
            except Exception:
                pass

            # 确保前端 reCAPTCHA 运行时组件就绪
            try:
                page.wait_for_function("() => typeof window.grecaptcha !== 'undefined' && typeof window.grecaptcha.render === 'function'", timeout=15000)
                logger.info("✅ reCAPTCHA 运行时组件已 100% 就绪")
            except Exception:
                pass

            # 点击 Terms of Service 协议复选框并保证 React 状态生效
            try:
                cb = page.locator('input[type="checkbox"]').first
                if cb.count() > 0:
                    cb.scroll_into_view_if_needed()
                    page.wait_for_timeout(random.randint(200, 350))
                    if not cb.is_checked():
                        cb.click()
                        logger.info("✅ 已点击 Webshare 服务条款复选框")
                else:
                    tos_cb = page.locator('[data-testid="tos-checkbox"]').first
                    if tos_cb.count() > 0:
                        tos_cb.click()
            except Exception as e:
                logger.debug(f"勾选协议异常: {e}")

            page.wait_for_timeout(400)
            # 双重保障 React 表单受控 state 同步
            try:
                page.evaluate("""() => {
                    const cb = document.querySelector('input[type="checkbox"]');
                    if (cb && !cb.checked) {
                        cb.click();
                        cb.dispatchEvent(new Event('input', { bubbles: true }));
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""")
            except Exception:
                pass

            # 点击邮件注册提交按钮 (data-testid='signup-button')
            submit_btn = page.locator('button[data-testid="signup-button"]').first
            if submit_btn.count() == 0:
                submit_btn = page.locator('button[type="submit"]:has-text("Email")').first
            if submit_btn.count() == 0:
                submit_btn = page.locator('button[type="submit"]:not(:has-text("Google"))').first

            if submit_btn.count() > 0:
                submit_btn.scroll_into_view_if_needed()
                page.wait_for_timeout(random.randint(300, 500))
                try:
                    submit_btn.click()
                except Exception:
                    submit_btn.click(force=True)
                logger.info("🚀 注册表单已提交 (signup-button)，等待验证码或直接响应...")
            else:
                page.locator('button[type="submit"]').first.click()

            # 自动过 reCAPTCHA (若弹出挑战由本地 ONNX 模型秒解，若无感则直通)
            self._solve_recaptcha(page, is_success_fn=lambda: api_registered[0], max_attempts=20)

            # 人机通过后：若尚未触发 API 响应，安全触发二次提交 (requestSubmit & click)
            if not api_registered[0] and not reg_duplicate[0]:
                page.wait_for_timeout(1000)
                logger.info("🚀 [Webshare] 人机挑战验证完成，二次触发表单提交触发注册 API...")
                try:
                    page.evaluate("""() => {
                        const form = document.querySelector('form');
                        if (form && typeof form.requestSubmit === 'function') {
                            form.requestSubmit();
                            return;
                        }
                        const btn = document.querySelector('button[data-testid="signup-button"], button[type="submit"]');
                        if (btn) btn.click();
                    }""")
                except Exception:
                    pass
                submit_btn = page.locator('button[data-testid="signup-button"], button[type="submit"]').first
                if submit_btn.count() > 0 and submit_btn.is_visible():
                    try:
                        submit_btn.click(timeout=2500)
                    except Exception:
                        try:
                            submit_btn.click(force=True)
                        except Exception:
                            pass
                page.wait_for_timeout(1500)

            # 等待 API 注册响应确认 (最多 15 秒)
            for _ in range(15):
                if api_registered[0] or reg_duplicate[0]:
                    break
                page.wait_for_timeout(1000)

            # 检查页面 URL 或 DOM 是否已提示成功或跳转
            if "dashboard" in page.url.lower() and "register" not in page.url.lower():
                api_registered[0] = True
                logger.info("🎉 [Webshare] 页面已自动重定向至控制台，注册成功！")

            page_status_info = page.evaluate("""() => {
                const body = document.body ? document.body.innerText : '';
                const has_success = /check your email|verification email|sent to|link has been sent/i.test(body);
                const errEl = document.querySelector('.ant-form-item-explain-error, .text-danger, [role="alert"]');
                const err_text = (errEl && errEl.offsetParent !== null) ? errEl.innerText.trim() : '';
                const m = body.match(/cannot register|forbidden|too many requests|already registered/i);
                return {
                    has_success,
                    err_text: err_text || (m ? m[0] : '')
                };
            }""")

            if page_status_info.get("has_success"):
                api_registered[0] = True
                logger.info("🎉 [Webshare] 页面已展示验证邮件发送成功提示！")

            if reg_duplicate[0]:
                logger.warning(f"⚠️ [Webshare] 检测到邮箱 {email} 已存在，安全跳过换号")
                return None

            err_txt = page_status_info.get("err_text", "")
            if err_txt:
                logger.warning(f"⚠️ [Webshare] 注册表单拦截提示: {err_txt}，终止无效等待直接换号")
                return None

            if not api_registered[0]:
                logger.warning("⚠️ 未直接拦截到明确成功的 API 响应，尝试直接向邮箱调度器探测激活邮件兜底...")

            # 4. 获取验证邮件并激活
            logger.info("⏳ 正在请求邮箱调度器提取激活邮件...")
            activation_url = activation_link_getter()
            if not activation_url:
                logger.error("❌ 未能获取到激活邮件链接，流程终止")
                return None

            logger.info(f"🔗 捕获到激活链接: {activation_url}，在同一会话中打开激活...")

            # 实时监听激活阶段控制台网络流，捕获节点与 Token
            captured_nodes: list[str] = []
            captured_tokens: list[str] = []
            def on_proxy_resp(res):
                try:
                    u = res.url.lower()
                    if ("proxy/list" in u or "proxy/config" in u) and res.status == 200:
                        data = res.json()
                        if isinstance(data, dict):
                            if "results" in data and isinstance(data["results"], list):
                                for item in data["results"]:
                                    ip = item.get("proxy_address")
                                    port = item.get("port", 3129)
                                    u_name = item.get("username")
                                    p_word = item.get("password")
                                    if ip and u_name and p_word:
                                        captured_nodes.append(f"http://{u_name}:{p_word}@{ip}:{port}")
                            if data.get("proxy_list_download_token"):
                                captured_tokens.append(str(data.get("proxy_list_download_token")))
                except Exception:
                    pass
            page.on("response", on_proxy_resp)

            page.goto(activation_url, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            self._handle_anti_bot(page)

            # 检查是否需要登录补登
            if page.locator('text="Invalid activation token"').count() > 0 or "login" in page.url.lower():
                logger.info("ℹ️ 激活页面提示 Token 已消费或需登录，执行控制台自动登录...")
                go_login = page.locator('button:has-text("Go To Login"), a:has-text("Go To Login")').first
                if go_login.count() > 0:
                    try:
                        go_login.click()
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)
                elif "login" not in page.url.lower():
                    try:
                        page.goto("https://dashboard.webshare.io/login", timeout=25000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

                em_in = page.locator('input[type="email"], input[name="email"]').first
                pw_in = page.locator('input[type="password"], input[name="password"]').first
                if em_in.count() > 0 and pw_in.count() > 0:
                    em_in.fill(email)
                    pw_in.fill(password)
                    sub_l = page.locator('button[type="submit"]:not(:has-text("Google"))').first
                    if sub_l.count() > 0:
                        sub_l.click()
                        page.wait_for_timeout(3500)
                        self._handle_anti_bot(page)

            # 5. 导航至代理控制台并提取凭证
            logger.info("📊 访问代理列表页提取 100 个节点...")
            page.wait_for_timeout(2000)

            # 5.1 若处于欢迎向导或控制台概览，主动点击 [View My Proxy List / Get Started] 激活 100 节点配额
            view_btn = page.locator('button:has-text("View My Proxy List"), a:has-text("View My Proxy List"), button:has-text("Get Started"), a:has-text("Get Started")').first
            if view_btn.count() > 0:
                logger.info("👉 检测到控制台欢迎向导，点击 [View My Proxy List] 按钮分配节点...")
                try:
                    view_btn.click()
                except Exception:
                    view_btn.click(force=True)
                page.wait_for_timeout(3500)

            # 5.2 确保到达节点列表大厅
            if "proxy/list" not in page.url.lower():
                page.goto(WEBSHARE_LIST_URL, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(3500)

            # 5.3 尝试点击列表页的 [Download] 弹窗获取直链
            try:
                dl_btn = page.locator('button:has-text("Download")').first
                if dl_btn.count() > 0:
                    logger.info("📥 点击列表页 [Download] 按钮获取官方下载直链...")
                    dl_btn.click()
                    page.wait_for_timeout(1500)
                    link_input = page.locator('[data-testid="download-test-link"], input[value*="webshare.io"], input[value*="download"]').first
                    if link_input.count() > 0:
                        direct_dl = link_input.get_attribute("value") or link_input.input_value()
                        if direct_dl and "http" in direct_dl:
                            logger.info(f"🔗 捕获到节点下载直链: {direct_dl}")
                            r = page.request.get(direct_dl)
                            if r.status == 200:
                                lines = [l.strip() for l in r.text().splitlines() if l.strip()]
                                parsed = self._normalize_lines_to_urls(lines)
                                if parsed:
                                    captured_nodes.extend(parsed)
            except Exception as ex:
                logger.debug(f"下载弹窗提取略过: {ex}")

            # 提取 Token / API Key
            api_token = captured_tokens[0] if captured_tokens else self._extract_api_token(page, context)
            logger.info(f"🔑 捕获到 Webshare API Token: {api_token[:8]}***" if api_token else "⚠️ 未直接定位到单独 Token，尝试全量抽取列表")

            # 提取 100 节点代理列表
            proxies = captured_nodes or self._fetch_proxy_nodes(page, context, api_token)
            if not proxies:
                logger.warning("⚠️ 首轮提取为空，尝试重新刷新列表页面...")
                page.reload()
                page.wait_for_timeout(4000)
                proxies = captured_nodes or self._fetch_proxy_nodes(page, context, api_token)

            if not proxies:
                logger.error("❌ 提取 100 节点失败（返回 0 节点）")
                return None

            # 识别提取子账号信息
            sub_user, sub_pass = "", ""
            first_node = proxies[0]
            m = re.match(r"https?://([^:]+):([^@]+)@", first_node)
            if m:
                sub_user, sub_pass = m.groups()

            logger.info(f"🎉 [成功] 账号 {email} 已就绪！成功提取 {len(proxies)} 个纯净节点 (SubUser: {sub_user})")

            return ProxyAccountRecord(
                email=email,
                account_password=password,
                email_source=email_source,
                sub_user=sub_user,
                sub_pass=sub_pass,
                api_token=api_token,
                node_count=len(proxies),
                proxies=proxies,
                batch_id=batch_id,
                status="active",
            )

        except Exception as e:
            logger.error(f"❌ 注册流程发生异常: {e}", exc_info=True)
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            elif 'browser' in locals() and browser:
                try:
                    browser.close()
                except Exception:
                    pass

    def _handle_anti_bot(self, page: Page) -> None:
        """检查并尝试穿透 Cloudflare Turnstile。"""
        try:
            class _DriverProxy:
                def __init__(self, p):
                    self.page = p
                    self._is_headless = self.page.context.browser is not None

                def execute_script(self, script, *args):
                    return self.page.evaluate(script, args)

            wrapper = _DriverProxy(page)
            solve_or_wait_cloudflare_turnstile(wrapper, timeout=15, is_headless=self.headless)
        except Exception as ex:
            logger.debug(f"[AntiBot] 检测波动: {ex}")

    def _solve_recaptcha(self, page: Page, is_success_fn: Any = None, max_attempts: int = 20) -> bool:
        """结合本地 ONNX YOLOv8 视觉分类器求解 reCAPTCHA 图片挑战。"""
        if not get_classifier:
            logger.debug("[Solver] 未载入 ONNX classifier，略过视觉破解")
            return True

        classifier = get_classifier()
        logger.info("🛡️ [Solver] 启动 reCAPTCHA 挑战监控与求解引擎...")

        # 1. 探针等待挑战弹窗是否真正弹出并可见 (最多 25 秒，兼容代理网络下慢速握手与风控判别)
        bf = None
        has_challenge = False
        for _wait in range(25):
            if is_success_fn and is_success_fn():
                logger.info("✨ [Solver] 注册接口已成功响应，无感直通！")
                return True
            for fr in page.frames:
                if "/bframe" in (fr.url or ""):
                    bf = fr
                    try:
                        # 检查 bframe 内部是否有真实的验证码交互元素 (非隐藏骨架)
                        has_elem = fr.evaluate("() => !!document.querySelector('.rc-imageselect-desc, table, #rc-imageselect, .rc-imageselect-desc-no-canonical, #recaptcha-verify-button')")
                        if has_elem:
                            has_challenge = True
                            break
                    except Exception:
                        pass
            if has_challenge:
                break
            page.wait_for_timeout(1000)

        # 二次确认：若发现 bframe 已注入但尚未渲染完毕，额外补充 5 秒等待
        if not has_challenge and any("/bframe" in (f.url or "") for f in page.frames):
            for _extra in range(5):
                page.wait_for_timeout(1000)
                for fr in page.frames:
                    if "/bframe" in (fr.url or ""):
                        bf = fr
                        try:
                            if fr.evaluate("() => !!document.querySelector('.rc-imageselect-desc, table, #rc-imageselect, .rc-imageselect-desc-no-canonical, #recaptcha-verify-button')"):
                                has_challenge = True
                                break
                        except Exception:
                            pass
                if has_challenge:
                    break

        if not has_challenge or not bf:
            logger.info("✅ [Solver] 未检测到验证码弹窗或已无感直通放行")
            return True

        logger.info("🧩 [Solver] 捕获到 reCAPTCHA 交互挑战帧，开始多轮求解...")
        if not self.headless:
            logger.info("👀 [有头可视化模式] 若需人工协助，可直接在可视浏览器窗口中点击验证码")

        # 优先尝试 Faster-Whisper 音频秒解
        if get_whisper_model:
            try:
                if self._try_solve_audio_recaptcha(bf, page, is_success_fn):
                    return True
            except Exception as e:
                logger.debug(f"[Solver] 音频推断跳过: {e}")

        consecutive_errors = 0
        zero_match_rounds = 0
        for attempt in range(max_attempts):
            if is_success_fn and is_success_fn():
                logger.info("✨ [Solver] 挑战已被服务端接收，无感放行！")
                return True

            # 重新定位最新 bframe
            for fr in page.frames:
                if "/bframe" in (fr.url or ""):
                    bf = fr
                    break

            page.wait_for_timeout(1200)

            try:
                meta = bf.evaluate("""() => {
                    const t = document.querySelector('table, #rc-imageselect-target table');
                    const desc = document.querySelector('.rc-imageselect-instructions, .rc-imageselect-desc-no-canonical, .rc-imageselect-desc');
                    const strong = desc?.querySelector('strong')?.innerText || document.querySelector('#rc-imageselect strong')?.innerText;
                    const rows = t ? t.rows.length : 0;
                    const cols = t && t.rows[0] ? t.rows[0].cells.length : 0;
                    const errElements = Array.from(document.querySelectorAll('.rc-imageselect-error-dynamic, .rc-imageselect-error-select-more, .rc-imageselect-incorrect-response'));
                    const visibleErr = errElements.find(el => el && el.offsetParent !== null && el.innerText.trim().length > 0);
                    const errText = visibleErr ? visibleErr.innerText.trim() : '';
                    return {
                        target: strong || (desc ? desc.innerText.split('\\n')[0] : ''),
                        rows, cols,
                        dynamic: !!document.querySelector('.rc-imageselect-dynamic-selected') || /click verify once there are none/i.test(desc?.innerText || ''),
                        has_table: !!t,
                        has_error: !!visibleErr && /try again|incorrect|check the new|select all|solve more/i.test(errText),
                        error_text: errText,
                    };
                }""")
            except Exception as e:
                # 弹窗可能已经关闭
                if is_success_fn and is_success_fn():
                    return True
                logger.debug(f"[Solver] evaluate 异常: {e}")
                page.wait_for_timeout(1000)
                continue

            # 检测到红字提示或挑战错误时，主动点击刷新按钮换一题
            if meta.get("has_error"):
                logger.info(f"🔄 [Solver] 检测到挑战提示重试 ({meta.get('error_text')})，点击 #recaptcha-reload-button 换一题...")
                try:
                    bf.locator("#recaptcha-reload-button").click(timeout=2500)
                    page.wait_for_timeout(2000)
                    continue
                except Exception:
                    pass

            target = meta.get("target", "").strip()
            rows = meta.get("rows", 0)
            cols = meta.get("cols", 0)
            dynamic = meta.get("dynamic", False)

            # 多语言归一化
            _TRANS = {
                "fahrräder": "bicycle", "fahrrad": "bicycle", "busse": "bus", "bus": "bus", "autos": "car", "auto": "car",
                "schornsteine": "chimney", "zebrastreifen": "crosswalk", "feuerhydranten": "fire hydrant", "feuerhydrant": "fire hydrant",
                "motorräder": "motorcycle", "berge": "mountain", "palmen": "palm tree", "treppen": "stair", "taxis": "taxi",
                "ampeln": "traffic light", "ampel": "traffic light", "brücken": "bridge", "boote": "boat",
                "bicicletas": "bicycle", "autobuses": "bus", "coches": "car", "chimeneas": "chimney",
                "pasos de peatones": "crosswalk", "bocas de incendio": "fire hydrant", "motocicletas": "motorcycle",
                "montañas": "mountain", "palmeras": "palm tree", "escaleras": "stair", "semáforos": "traffic light",
                "puentes": "bridge", "barcos": "boat"
            }
            clean_target = target.lower().strip()
            target = _TRANS.get(clean_target, target)

            if not rows or not target:
                page.wait_for_timeout(1200)
                continue

            logger.info(f"🎯 [Solver 第 {attempt+1} 轮] 目标='{target}', 规格={rows}x{cols}, 动态={dynamic}")

            table = bf.locator("table")
            try:
                table_bytes = table.screenshot(timeout=6000)
            except Exception:
                try:
                    table_bytes = page.frame_locator("iframe[title*='recaptcha challenge']").locator("table").screenshot(timeout=6000)
                except Exception:
                    continue

            img = Image.open(io.BytesIO(table_bytes)).convert("RGB")
            w, h = img.size
            tw, th = w // cols, h // rows

            tiles = bf.locator("td[role=button], .rc-imageselect-tile").all()
            matches = []

            for r in range(rows):
                for c in range(cols):
                    idx = r * cols + c
                    tile_img = img.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
                    buf = io.BytesIO()
                    tile_img.save(buf, "PNG")
                    b64_str = base64.b64encode(buf.getvalue()).decode()

                    is_match = classifier.classify(b64_str, target)
                    if is_match:
                        matches.append(idx)

            logger.info(f"🔍 [Solver] 模型命中 {len(matches)}/{rows*cols} 单元格 -> {matches}")

            for idx in matches:
                if idx < len(tiles):
                    try:
                        tiles[idx].click(timeout=2500)
                        page.wait_for_timeout(random.randint(280, 480))
                    except Exception:
                        pass
            if dynamic and matches:
                zero_match_rounds = 0
                logger.info("⏳ [Solver] 动态消隐验证码：等待 2.8s 新图平滑加载...")
                page.wait_for_timeout(2800)
                continue

            if len(matches) == 0:
                zero_match_rounds += 1
                if zero_match_rounds >= 2:
                    logger.info("🔄 [Solver] 动态消隐验证码已多次无命中且弹窗未闭合，点击 #recaptcha-reload-button 换题...")
                    try:
                        bf.locator("#recaptcha-reload-button").click(timeout=2500)
                        page.wait_for_timeout(2000)
                        zero_match_rounds = 0
                        continue
                    except Exception:
                        pass
            else:
                zero_match_rounds = 0

            # 点击确认按钮
            page.wait_for_timeout(random.randint(600, 1000))
            clicked_v = False
            for _v in range(3):
                try:
                    page.frame_locator("iframe[title*='recaptcha challenge']").locator("#recaptcha-verify-button").click(timeout=3000, force=True)
                    clicked_v = True
                    break
                except Exception:
                    try:
                        bf.locator("#recaptcha-verify-button").click(timeout=2500)
                        clicked_v = True
                        break
                    except Exception:
                        try:
                            bf.evaluate("() => { const b=document.querySelector('#recaptcha-verify-button'); if(b) b.click(); }")
                            clicked_v = True
                            break
                        except Exception:
                            pass
                page.wait_for_timeout(500)

            page.wait_for_timeout(2000)

            # 检查是否成功关闭弹窗
            try:
                btn_visible = page.frame_locator("iframe[title*='recaptcha challenge']").locator("#recaptcha-verify-button").is_visible(timeout=1000)
                if not btn_visible:
                    logger.info("🎉 [Solver] reCAPTCHA 挑战完成，弹窗已成功闭合！")
                    return True
            except Exception:
                return True

        return True

    def _switch_to_image_challenge(self, bf: Any, page: Page) -> None:
        """从音频挑战安全切回视觉图片验证码，确保后续视觉分类器可正常提取 table。"""
        try:
            img_btn = bf.locator("#recaptcha-image-button")
            if img_btn.count() > 0:
                img_btn.click(timeout=2000)
                page.wait_for_timeout(1500)
        except Exception:
            pass

    def _try_solve_audio_recaptcha(self, bf: Any, page: Page, is_success_fn: Any = None) -> bool:
        """尝试通过 reCAPTCHA 音频验证挑战求解 (Faster-Whisper 转录)。"""
        try:
            audio_btn = bf.locator("#recaptcha-audio-button")
            if audio_btn.count() == 0:
                return False

            logger.info("🎵 [Audio Solver] 探测到 reCAPTCHA 音频挑战入口，尝试以 Faster-Whisper 秒级推断...")
            try:
                audio_btn.click(timeout=3000)
            except Exception:
                audio_btn.click(force=True)
            page.wait_for_timeout(2500)

            # 轮询探测音频就绪状态 (最多等待 6 秒)
            href = ""
            for _wait_audio in range(6):
                # 检查是否命中 Google 频率限制 (automated queries)
                if bf.locator(".rc-doscaptcha-body-text, text=/automated queries/i").count() > 0:
                    logger.info("⚠️ [Audio Solver] 当前 IP 音频挑战受 Google 频控限制，自动平滑切回视觉多模态破解...")
                    self._switch_to_image_challenge(bf, page)
                    return False

                # 探测音频下载链接或 audio 播放源
                for sel in (
                    "a.rc-audiochallenge-tdownload-link",
                    "a.rc-audiochallenge-download-link",
                    "a[href*='payload?audio=']",
                    "a[href*='audio']",
                    "audio#audio-source",
                    "audio source",
                    "audio",
                ):
                    cand = bf.locator(sel).first
                    if cand.count() > 0:
                        href = cand.get_attribute("href") or cand.get_attribute("src") or ""
                        if href:
                            break
                if href:
                    break
                page.wait_for_timeout(1000)

            if not href:
                logger.info("⚠️ [Audio Solver] 未捕获到有效音频流 URL，平滑切回视觉多模态图形验证...")
                self._switch_to_image_challenge(bf, page)
                return False

            logger.info("🎧 [Audio Solver] 捕获音频流 URL，正在通过浏览器会话高速拉取...")
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                audio_bytes = b""
                # 优先走 page.request: 100% 保持浏览器代理出口与会话 Cookies，杜绝 IP 不一致与国内直连超时
                try:
                    resp = page.request.get(href, timeout=12000)
                    if resp.status == 200:
                        audio_bytes = resp.body()
                except Exception as req_err:
                    logger.debug(f"[Audio Solver] page.request 获取音频波动: {req_err}，尝试本地直连降级")

                if not audio_bytes:
                    import urllib.request
                    req = urllib.request.Request(href, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        audio_bytes = r.read()

                if not audio_bytes:
                    logger.warning("⚠️ [Audio Solver] 音频数据为空，切回视觉验证码")
                    self._switch_to_image_challenge(bf, page)
                    return False

                with open(tmp_path, "wb") as f:
                    f.write(audio_bytes)

                model = get_whisper_model()
                segments, _ = model.transcribe(tmp_path, language="en")
                transcribed = " ".join([seg.text for seg in segments]).strip()
                logger.info(f"🎙️ [Audio Solver] Whisper 转录文本: '{transcribed}'")

                ans_input = bf.locator("#audio-response")
                if ans_input.count() > 0 and transcribed:
                    ans_input.fill(transcribed)
                    page.wait_for_timeout(600)
                    v_btn = bf.locator("#recaptcha-verify-button")
                    if v_btn.count() > 0:
                        v_btn.click()
                        page.wait_for_timeout(2500)
                        if is_success_fn and is_success_fn():
                            logger.info("🎉 [Audio Solver] 音频验证成功通过！")
                            return True
                        if bf.locator("#audio-response").count() == 0:
                            logger.info("🎉 [Audio Solver] 音频弹窗已闭合，验证通过！")
                            return True
                        # 若音频错误提示出现
                        if bf.locator(".rc-audiochallenge-error-message").count() > 0:
                            logger.warning("⚠️ [Audio Solver] 转录结果未被接受，平滑切回视觉验证码...")
                            self._switch_to_image_challenge(bf, page)
                            return False
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        except Exception as ex:
            logger.debug(f"[Audio Solver] 流程异常: {ex}")
            try:
                self._switch_to_image_challenge(bf, page)
            except Exception:
                pass
        return False

    def _extract_api_token(self, page: Page, context: BrowserContext) -> str:
        """从 Storage 或 Cookie 中抓取 Webshare Token。"""
        try:
            cookies = context.cookies()
            for c in cookies:
                if "token" in c["name"].lower():
                    return c["value"]

            token = page.evaluate("() => localStorage.getItem('token') || sessionStorage.getItem('token') || ''")
            if token:
                return str(token).strip('"')
        except Exception:
            pass
        return ""

    def _fetch_proxy_nodes(self, page: Page, context: BrowserContext, api_token: str) -> list[str]:
        """通过 API 或页面 DOM 导出 100 节点列表并标准化清洗。"""
        # 1. 尝试直接调用 API 下载接口 (如果拿到了 Token)
        if api_token:
            for download_url in (
                f"{WEBSHARE_API_BASE}/proxy/list/download/{api_token}/-/any/username/direct/-/",
                f"{WEBSHARE_API_BASE}/proxy/list/download/{api_token}/-/any/username/direct/-",
            ):
                try:
                    resp = page.request.get(download_url)
                    if resp.status == 200:
                        text = resp.text()
                        lines = [line.strip() for line in text.splitlines() if line.strip()]
                        cleaned = self._normalize_lines_to_urls(lines)
                        if cleaned:
                            return cleaned
                except Exception as e:
                    logger.debug(f"API 下载接口请求异常: {e}")

        # 2. 尝试从页面内部请求或 DOM 提取
        try:
            resp = page.request.get(f"{WEBSHARE_API_BASE}/proxy/list/?mode=direct&page=1&page_size=100")
            if resp.status == 200:
                data = resp.json()
                results = data.get("results", [])
                nodes = []
                for item in results:
                    ip = item.get("proxy_address")
                    port = item.get("port", 3129)
                    user = item.get("username")
                    pwd = item.get("password")
                    if ip and user and pwd:
                        nodes.append(f"http://{user}:{pwd}@{ip}:{port}")
                if nodes:
                    return nodes
        except Exception as e:
            logger.debug(f"REST API 提取异常: {e}")

        # 3. DOM 页面文本兜底
        try:
            content = page.content()
            # 匹配 ip:3129 样式
            raw_matches = re.findall(r"(?:https?://)?(?:[a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+@)?\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:3129", content)
            if raw_matches:
                return self._normalize_lines_to_urls(raw_matches)
        except Exception:
            pass

        return []

    def _normalize_lines_to_urls(self, raw_lines: list[str]) -> list[str]:
        """将任意格式 (ip:port:user:pass 或 user:pass@ip:port) 规整为 http://user:pass@ip:3129。"""
        res = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue

            # 已是完整 URL
            if line.startswith("http://") or line.startswith("https://"):
                res.append(line)
                continue

            # 格式：ip:port:username:password
            parts = line.split(":")
            if len(parts) == 4:
                ip, port, user, pwd = parts
                res.append(f"http://{user}:{pwd}@{ip}:{port}")
            # 格式：username:password@ip:port
            elif "@" in line:
                res.append(f"http://{line}")
        return res
