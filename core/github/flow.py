# -*- coding: utf-8 -*-
"""
GitHub 页面表单填写、人机协同感知机与引导跳过流程模块。

借鉴 Crawlee 与 Midscene.js 启发式 DOM 容错设计：
- 多级选择器回退机制 (Heuristic Selector Fallbacks)
- 双通道人机感知器 (DOM/URL 变化监听 + 控制台按键兜底)
- 智能个性化问卷穿透跳过
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Any, Tuple, Optional

logger = logging.getLogger("github.flow")


def _safe_click_continue(page: Any, container_id: str, target_container: str) -> None:
    """精准定位推进按钮，严格过滤第三方 SSO (Google / Apple / Passkey) 误点。"""
    selectors = [
        f"#{container_id} button[data-continue-to='{target_container}']:not([disabled])",
        f"button[data-continue-to='{target_container}']:not([disabled])",
        f"#{container_id} button.signup-continue-button:not([disabled])",
        f"#{container_id} button:text-is('Continue'):not([disabled])",
    ]
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            text = (loc.inner_text() or "").lower()
            if "google" in text or "passkey" in text or "apple" in text:
                continue
            try:
                loc.click(timeout=3000)
                return
            except Exception:
                pass


def check_and_wait_datadome_or_waf(page: Any, max_wait: int = 180) -> bool:
    """检测并等待 GitHub 前置 WAF / DataDome 滑块验证通过。"""
    try:
        content = page.content()
        title = page.title()
    except Exception:
        content = ""
        title = ""

    is_datadome = (
        "访问暂时受限" in content or
        "captcha-delivery.com" in content or
        "我不是机器人" in content or
        (title.strip().lower() == "github.com" and page.locator("iframe[src*='captcha-delivery']").count() > 0)
    )

    # 如果已经有可见的邮箱输入框，说明已经放行
    if page.locator("input#email, input[type='email'], input[name='user[email]']").count() > 0:
        if page.locator("input#email, input[type='email'], input[name='user[email]']").first.is_visible():
            return True

    if is_datadome:
        print("\n" + "=" * 70)
        print("🛡️ 【检测到 GitHub 前置安全验证 (DataDome / 访问暂时受限)】")
        print("   • 当前出口 IP 触发了 GitHub 的 DataDome 防护滑块；")
        print("   • 👉 请在已弹出的浏览器窗口中【向右拖动滑块】完成拼图验证！")
        print("   • 滑块通过后页面将自动放行，脚本将在进入注册表单后无缝接管推进...")
        print("=" * 70 + "\n")
        logger.info("🛡️ [DataDome] 等待人工在浏览器中完成前置滑块验证 (最长 %ds)...", max_wait)

        start_time = time.time()
        while time.time() - start_time < max_wait:
            email_check = page.locator("input#email, input[type='email'], input[name='user[email]']").first
            try:
                if email_check.count() > 0 and email_check.is_visible():
                    logger.info("✨ [DataDome] 检测到前置验证已通过，已成功进入注册表单！")
                    page.wait_for_timeout(1500)
                    return True
            except Exception:
                pass
            time.sleep(1.5)

        logger.warning("⚠️ 等待 DataDome 前置验证超时")
        return False

    return True


def fill_signup_form(
    page: Any,
    email: str,
    password: str,
    username: str,
) -> dict:
    """启发式多级选择器填入注册表单第一阶段 (邮箱、密码、用户名、订阅偏好)。"""
    # 0. 防止误入第三方登录页
    if "accounts.google.com" in page.url:
        page.goto("https://github.com/signup", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

    # 0.1 前置安全验证检测与等待 (DataDome / 访问受限滑块)
    if not check_and_wait_datadome_or_waf(page, max_wait=180):
        return {"status": "waf_blocked", "error": "前置 DataDome 验证未通过或超时"}

    # 1. 填入邮箱
    logger.info("📝 [表单填写] 正在填入邮箱: %s ...", email)
    email_loc = page.locator("input#email, input[type='email'], input[name='user[email]']").first
    email_loc.wait_for(state="visible", timeout=30000)
    email_loc.click()
    page.wait_for_timeout(300)
    
    from core.human_dynamics import BiometricKeyboard
    BiometricKeyboard.type_playwright_sync(page, email, typo_rate=0.015)
    page.wait_for_timeout(800)

    # 占用校验
    if page.locator("text='Email is invalid or already taken'").is_visible(timeout=1200):
        return {"status": "email_taken", "error": "邮箱已被占用或无效"}

    # 推进至密码容器 (精准避免误点 Continue with Google)
    _safe_click_continue(page, "email-container", "password-container")
    page.wait_for_timeout(1000)
    pw_loc = page.locator("input#password, input[type='password'], input[name='user[password]']").first
    if not pw_loc.is_visible():
        try:
            email_loc.press("Enter")
            page.wait_for_timeout(1000)
        except Exception:
            pass

    # 2. 填入密码
    logger.info("📝 [表单填写] 正在填入高强度安全密码...")
    pw_loc.wait_for(state="visible", timeout=15000)
    pw_loc.click()
    page.wait_for_timeout(200)
    BiometricKeyboard.type_playwright_sync(page, password, typo_rate=0.0)  # 密码不模拟打错
    page.wait_for_timeout(800)

    # 推进至用户名容器
    _safe_click_continue(page, "password-container", "username-container")
    page.wait_for_timeout(1000)
    user_loc = page.locator("input#login, input[name='user[login]']").first
    if not user_loc.is_visible():
        try:
            pw_loc.press("Enter")
            page.wait_for_timeout(1000)
        except Exception:
            pass

    # 3. 填入用户名
    logger.info("📝 [表单填写] 正在填入用户名: %s ...", username)
    user_loc.wait_for(state="visible", timeout=15000)
    user_loc.click()
    page.wait_for_timeout(200)
    BiometricKeyboard.type_playwright_sync(page, username, typo_rate=0.01)
    page.wait_for_timeout(1500)

    # 校验用户名提示
    err_msg = page.locator(".input-error, [aria-invalid='true'], p:has-text('is not available')")
    if err_msg.count() > 0 and err_msg.first.is_visible():
        return {"status": "username_taken", "error": f"用户名 {username} 已被占用"}

    # 推进至偏好容器
    _safe_click_continue(page, "username-container", "opt-in-container")
    page.wait_for_timeout(1000)
    opt_loc = page.locator("input#opt_in, input[name='opt_in']").first
    if not opt_loc.is_visible():
        try:
            user_loc.press("Enter")
            page.wait_for_timeout(1000)
        except Exception:
            pass

    # 4. 邮件更新偏好 (n)
    if opt_loc.is_visible(timeout=4000):
        opt_loc.click()
        BiometricKeyboard.type_playwright_sync(page, "n", typo_rate=0.0)
        page.wait_for_timeout(600)

        # 推进至验证码容器
        _safe_click_continue(page, "opt-in-container", "captcha-and-submit-container")
        page.wait_for_timeout(1000)
        btn_captcha_direct = page.locator("#opt-in-container button:not([disabled])").first
        if btn_captcha_direct.is_visible():
            try:
                opt_loc.press("Enter")
            except Exception:
                pass
        page.wait_for_timeout(1500)

    return {"status": "ok"}


def try_auto_solve_arkose(page: Any, timeout: int = 15) -> bool:
    """尝试优先自动求解 Arkose 挑战 (全自动优先)。

    支持打码平台 Token 注入与视觉模型双轨。
    若未检测到挑战、未配置求解密钥或自动求解未达成，安全返回 False 以便无缝降级至人工协同接管。
    """
    import os
    try:
        import config
    except Exception:
        config = None

    yes_key = getattr(config, "YESCAPTCHA_API_KEY", "") or os.environ.get("YESCAPTCHA_API_KEY", "")
    cap_key = getattr(config, "CAPSOLVER_API_KEY", "") or os.environ.get("CAPSOLVER_API_KEY", "")
    ez_key = getattr(config, "EZCAPTCHA_API_KEY", "") or os.environ.get("EZCAPTCHA_API_KEY", "")
    vision_key = getattr(config, "VISION_API_KEY", "") or os.environ.get("VISION_API_KEY", "")

    if not (yes_key or cap_key or ez_key or vision_key):
        return False

    deadline = time.time() + timeout
    arkose_frame = None

    while time.time() < deadline:
        url_now = getattr(page, "url", "")
        if "/account_verifications" in url_now or "/signup/verify" in url_now:
            return True
        otp_boxes = page.locator("input[name='octocaptcha-input'], input#code, .js-verification-code-input-item, input[autocomplete='one-time-code']")
        if otp_boxes.count() > 0 and otp_boxes.first.is_visible():
            return True

        for fr in getattr(page, "frames", []):
            u = (getattr(fr, "url", "") or "").lower()
            if any(k in u for k in ("octocaptcha", "arkose", "funcaptcha")):
                arkose_frame = fr
                break
        if arkose_frame:
            break
        time.sleep(1.0)

    if not arkose_frame:
        return False

    logger.info("⚡ [自动打码] 检测到 Arkose 验证框架，启动自动优先攻防管线...")

    # 1. 尝试触发 Visual puzzle
    try:
        vp_btn = arkose_frame.get_by_text("Visual puzzle", exact=False).first
        if vp_btn.count() > 0 and vp_btn.is_visible():
            vp_btn.click(timeout=3000)
            page.wait_for_timeout(2000)
    except Exception:
        pass

    # 2. 若配置了打码平台 Key，提取 data-exchange payload 并执行 token 注入
    if yes_key or cap_key or ez_key:
        try:
            blob = arkose_frame.evaluate(
                "() => { const el = document.querySelector('#funcaptcha'); return el ? (el.getAttribute('data-data-exchange-payload') || '') : ''; }"
            )
            blob = (blob or "").strip()
            public_key = "747B83EC-2CA3-43AD-A7DF-701F286FBABA"
            target_url = "https://github.com/signup"
            subdomain = "github-api.arkoselabs.com"
            token = ""

            if yes_key:
                from register_github import _solve_funcaptcha_yescaptcha
                token = _solve_funcaptcha_yescaptcha(public_key, target_url, subdomain, blob, max_wait=90)
            elif cap_key:
                from register_github import _solve_funcaptcha_capsolver
                token = _solve_funcaptcha_capsolver(public_key, target_url, subdomain, blob, max_wait=90)
            elif ez_key:
                from register_github import _solve_funcaptcha_ezcaptcha
                token = _solve_funcaptcha_ezcaptcha(public_key, target_url, subdomain, blob, max_wait=90)

            if token:
                logger.info("✨ [自动打码] 成功获取 FunCaptcha token，正在回灌...")
                origin = arkose_frame.evaluate(
                    "() => { const el = document.querySelector('#funcaptcha'); return el ? (el.getAttribute('data-target-origin') || '') : ''; }"
                ) or "https://github.com"
                arkose_frame.evaluate(
                    "([tok, org]) => { parent.postMessage({event: 'captcha-complete', sessionToken: tok}, org || '*'); }",
                    [token, origin],
                )
                page.wait_for_timeout(3000)
                return True
        except Exception as exc:
            logger.debug("[自动打码] 平台打码执行异常: %s", exc)

    return False

def solve_arkose_with_audio_playwright(page: Any) -> bool:
    """利用本地 Whisper 尝试语音降维打击 Arkose (支持多轮)"""
    logger.info("🎧 尝试启动本地 Whisper 语音降维...")
    try:
        arkose_frame = None
        for fr in getattr(page, "frames", []):
            u = (getattr(fr, "url", "") or "").lower()
            if any(k in u for k in ("arkose", "funcaptcha")):
                arkose_frame = fr
                break
                
        if not arkose_frame:
            return False
            
        # 寻找音频切换按钮
        audio_btn = arkose_frame.locator("button[id*='audio'], button[class*='audio'], button[aria-label*='audio' i], [id*='audio_btn']").first
        if audio_btn.count() > 0 and audio_btn.is_visible():
            logger.info("🎧 找到音频挑战按钮，点击切换...")
            audio_btn.click(timeout=3000)
            page.wait_for_timeout(2000)
            
        for round_idx in range(5):
            is_blocked = arkose_frame.evaluate("() => document.body.innerText.includes('We cannot provide an audio challenge')")
            if is_blocked:
                logger.warning("🎧 当前 IP 的语音通道被 Arkose 封锁 (Audio blocked)，降维打击中止。")
                return False
                
            js_fetch_audio = """
            async () => {
                let a = document.querySelector('audio');
                let src = a ? (a.src || (a.querySelector('source') && a.querySelector('source').src) || '') : '';
                if (!src) return '';
                let resp = await fetch(src);
                let blob = await resp.blob();
                return new Promise((resolve, _) => {
                    let reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.readAsDataURL(blob);
                });
            }
            """
            audio_b64 = arkose_frame.evaluate(js_fetch_audio)
            
            if not audio_b64:
                # 检查通过状态
                is_solved = arkose_frame.evaluate("() => document.body.innerText.includes('Verification complete') || document.body.innerText.includes('Verified')")
                if is_solved:
                    return True
                page.wait_for_timeout(2000)
                continue
                
            logger.info(f"🎧 第 {round_idx+1} 轮捕获音频数据，准备解码...")
            
            import tempfile, base64, os
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                b64_data = audio_b64.split(",")[1] if "," in audio_b64 else audio_b64
                with open(temp_path, "wb") as f:
                    f.write(base64.b64decode(b64_data))
                
                from core.audio_solver.solver import solve_audio_file
                solution = solve_audio_file(temp_path)
            finally:
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass
                    
            if not solution:
                logger.warning("🎧 无法听写音频，中止。")
                return False
                
            answer_input = arkose_frame.locator("input[type='text'], input[id*='audio'], input[id*='response']").first
            if answer_input.count() > 0 and answer_input.is_visible():
                answer_input.fill(solution)
                page.wait_for_timeout(1000)
                
                submit_btn = arkose_frame.locator("button[type='submit'], button[id*='submit'], button[id*='verify']").first
                if submit_btn.count() > 0 and submit_btn.is_visible():
                    submit_btn.click()
                    page.wait_for_timeout(4000)
            else:
                logger.warning("🎧 找不到答案输入框")
                break
                
        return True
    except Exception as e:
        logger.error(f"🎧 语音降维执行异常: {e}")
        return False


def wait_for_arkose_and_takeover(page: Any, max_wait: int = 360) -> bool:
    """人机协同双通道感知机 (全自动优先 + 人工热备双轨制)。

    轨道 1: 自动优先探测并尝试调用视觉模型/打码平台求解 Arkose 挑战。
    轨道 2: 降级/热备双通道感知机：
      - 通道 A: 异步轮询页面 URL 与 DOM 变化（跳转 /account_verifications、验证码框就绪、提交按钮激活）
      - 通道 B: 终端非阻塞按键监听（用户在控制台按下 Enter 键立即强制接管）
    """
    logger.info("🧩 [攻防管线] 正在启动 Arkose 验证感知 (全自动优先)...")
    if try_auto_solve_arkose(page, timeout=8):
        logger.info("✨ [攻防管线] Arkose 自动求解通过，无缝进入后续步骤！")
        return True
        
    if solve_arkose_with_audio_playwright(page):
        logger.info("✨ [攻防管线] Whisper 语音验证码降维打击成功！")
        return True

    try:
        from core.live_browser_hub import LiveBrowserHub
        LiveBrowserHub.ensure_server(8799)
        LiveBrowserHub.get_hub().attach_page(page, auto_open=True)
    except Exception as _ex:
        logger.debug(f"LiveBrowser attach: {_ex}")

    _banner = (
        "\n" + "=" * 70 + "\n"
        "【人机零侵入 Web 协同】Arkose 旋转/拼图视窗已在系统默认浏览器中打开！\n"
        "   • 地址: http://127.0.0.1:8799/live\n"
        "   • 交互方式: 请直接在网页画面中点击或旋转拼图\n"
        "   • 智能接管: 一旦完成验证进入验证码阶段，脚本将【自动无缝接管】\n"
        "   • 终端回车兜底: 若您在网页中已点击提交，亦可在此控制台直接敲击【回车键 (Enter)】强制接管！\n"
        + "=" * 70 + "\n"
    )
    try:
        print(_banner)
    except UnicodeEncodeError:
        try:
            print(_banner.encode("gbk", errors="replace").decode("gbk"))
        except Exception:
            pass

    has_msvcrt = False
    if sys.platform.startswith("win"):
        try:
            import msvcrt
            has_msvcrt = True
        except Exception:
            pass

    watch_start = time.time()
    takeover = False

    while time.time() - watch_start < max_wait:
        # 1. 终端按键探测
        if has_msvcrt and msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b'\r', b'\n'):
                logger.info("⌨️ [按键感知] 检测到控制台回车，立即强制接管后续流程！")
                takeover = True
                break

        # 2. 页面 URL 跳转探测
        url_now = page.url
        if "/account_verifications" in url_now or "/signup/verify" in url_now:
            logger.info("⚡ [智能感知] 监测到页面已导航至邮箱验证页: %s", url_now)
            takeover = True
            break

        # 3. Create account 按钮激活探测
        submit_btn = page.locator("button#create-account:not([disabled]), button[type='submit']:has-text('Create account'):not([disabled])")
        if submit_btn.count() > 0 and submit_btn.first.is_visible():
            logger.info("⚡ [智能感知] 监测到 'Create account' 按钮已激活，正在自动点击提交...")
            try:
                submit_btn.first.click()
                page.wait_for_timeout(2000)
            except Exception:
                pass
            takeover = True
            break

        # 4. 验证码输入框出现探测
        otp_boxes = page.locator("input[name='octocaptcha-input'], input#code, .js-verification-code-input-item, input[autocomplete='one-time-code']")
        if otp_boxes.count() > 0 and otp_boxes.first.is_visible():
            logger.info("⚡ [智能感知] 监测到邮箱验证码输入框已就绪，自动无缝接管！")
            takeover = True
            break

        time.sleep(1.0)

    try:
        from core.live_browser_hub import LiveBrowserHub
        LiveBrowserHub.get_hub().detach_page()
    except Exception:
        pass

    return takeover


def inject_verification_code(page: Any, code: str) -> None:
    """启发式自适应注入 GitHub 验证码 (支持分散框与单输入框)。"""
    logger.info("⌨️ [自动填码] 正在注入验证码: 【%s】 ...", code)
    page.wait_for_timeout(800)

    # 1. 分散多字符输入框
    split_inputs = page.locator(".js-verification-code-input-item, input[type='text'][maxlength='1']")
    if split_inputs.count() >= len(code):
        logger.info("   -> 匹配到分散单字符输入框，逐位输入...")
        for idx, ch in enumerate(code):
            try:
                box = split_inputs.nth(idx)
                box.fill(ch)
                page.wait_for_timeout(120)
            except Exception:
                pass
    else:
        # 2. 单一输入框
        single_input = page.locator("input#code, input[name='octocaptcha-input'], input[autocomplete='one-time-code']").first
        if single_input.is_visible(timeout=5000):
            single_input.fill(code)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")

    page.wait_for_timeout(3500)


def skip_onboarding_guide(page: Any, max_rounds: int = 5) -> None:
    """启发式检测并快速穿透跳过新手问卷 (Personalization)。"""
    logger.info("🧭 [引导跳过] 正在检测并跳过个性化新手问卷...")
    for _ in range(max_rounds):
        url_now = page.url
        if any(k in url_now for k in ("github.com/welcome", "github.com/getting-started", "join/personalize")):
            # 优先寻找明确的 Skip 链接
            skip_btn = page.locator(
                "a:has-text('Skip personalization'), button:has-text('Skip personalization'), a[href*='skip_personalization']"
            ).first
            if skip_btn.is_visible(timeout=2500):
                logger.info("   -> 找到 'Skip personalization'，正在点击跳过...")
                skip_btn.click()
                page.wait_for_timeout(3000)
                break
            else:
                # 寻找 Continue 推进
                cont_btn = page.locator("button:has-text('Continue'):not([disabled])").first
                if cont_btn.is_visible(timeout=2000):
                    cont_btn.click()
                    page.wait_for_timeout(2000)
        elif "github.com/dashboard" in url_now or url_now.rstrip("/") in ("https://github.com", "http://github.com"):
            break
        time.sleep(1.0)
