# -*- coding: utf-8 -*-
"""
GitHub 注册流程的 nodriver 驱动版本。
通过底层协议原生控制 Chromium，绕过 Playwright CDP 检测。
"""
import asyncio
import logging
import time
import sys

import nodriver as uc

from core.audio_solver.solver import solve_audio_file

logger = logging.getLogger("nodriver_github")

async def solve_arkose_with_audio_nodriver(page: uc.Tab) -> bool:
    """利用本地 Whisper 尝试语音降维打击 Arkose (支持多轮)"""
    logger.info("🎧 尝试启动本地 Whisper 语音降维...")
    try:
        frames = await page.get_frames()
        arkose_frame = None
        for f in frames:
            if "arkose" in f.target.url.lower() or "funcaptcha" in f.target.url.lower():
                arkose_frame = f
                break
                
        if not arkose_frame:
            return False
            
        # 寻找音频切换按钮并点击 (仅需点一次)
        audio_btn = await arkose_frame.select("button[id*='audio'], button[class*='audio'], button[aria-label*='audio' i], [id*='audio_btn']", timeout=3)
        if audio_btn:
            logger.info("🎧 找到音频挑战按钮，点击切换...")
            await audio_btn.click()
            await asyncio.sleep(2)
        
        # 循环解决可能存在的多轮语音验证
        for round_idx in range(5): # 最多允许 5 轮
            # 检测是否被风控拦截了语音通道
            is_blocked = await arkose_frame.evaluate("() => document.body.innerText.includes('We cannot provide an audio challenge')")
            if is_blocked:
                logger.warning("🎧 当前 IP 的语音通道被 Arkose 封锁 (Audio blocked)，降维打击中止。")
                return False

            js_fetch_audio = """
            (async () => {
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
            })()
            """
            audio_b64 = await arkose_frame.evaluate(js_fetch_audio, await_promise=True)
            
            if not audio_b64:
                download_link = await arkose_frame.select("a[href*='.mp3'], a[href*='.wav']", timeout=2)
                if download_link:
                    # 如果有下载链接，可以模拟点击或者注入 JS fetch
                    pass
                    
            if not audio_b64:
                # 判断 iframe 是否还存在
                is_solved = await arkose_frame.evaluate("() => document.body.innerText.includes('Verification complete') || document.body.innerText.includes('Verified')")
                if is_solved:
                    return True
                await asyncio.sleep(2)
                continue
                
            logger.info(f"🎧 第 {round_idx+1} 轮捕获音频数据，准备解码...")
            
            import tempfile, base64, os
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                # audio_b64 format: "data:audio/mp3;base64,xxxxxx"
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
                
            # 填入答案
            answer_input = await arkose_frame.select("input[type='text'], input[id*='audio'], input[id*='response']", timeout=3)
            if answer_input:
                await answer_input.send_keys(solution)
                await asyncio.sleep(1)
                
                submit_btn = await arkose_frame.select("button[type='submit'], button[id*='submit'], button[id*='verify']", timeout=2)
                if submit_btn:
                    await submit_btn.click()
                    await asyncio.sleep(4)
            else:
                logger.warning("🎧 找不到答案输入框")
                break
                
        return True # 默认跑出循环就是通过了
                
    except Exception as e:
        logger.error(f"🎧 语音降维执行异常: {e}")
        
    return False

async def try_auto_solve_arkose_nodriver(page: uc.Tab, timeout: int = 15) -> bool:
    """在 nodriver 架构下尝试 Token 注入"""
    import os
    try:
        import config
    except Exception:
        config = None

    yes_key = getattr(config, "YESCAPTCHA_API_KEY", "") or os.environ.get("YESCAPTCHA_API_KEY", "")
    cap_key = getattr(config, "CAPSOLVER_API_KEY", "") or os.environ.get("CAPSOLVER_API_KEY", "")
    ez_key = getattr(config, "EZCAPTCHA_API_KEY", "") or os.environ.get("EZCAPTCHA_API_KEY", "")

    if not (yes_key or cap_key or ez_key):
        return False

    deadline = time.time() + timeout
    blob = ""

    while time.time() < deadline:
        try:
            # 尝试通过 JS 注入提取 iframe 内部的 data-data-exchange-payload
            js_extract = """
            (() => {
                let frames = document.querySelectorAll('iframe');
                for (let f of frames) {
                    try {
                        let el = f.contentWindow.document.querySelector('#funcaptcha');
                        if (el) return el.getAttribute('data-data-exchange-payload') || '';
                    } catch(e) {}
                }
                return '';
            })()
            """
            blob = await page.evaluate(js_extract)
            if blob:
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not blob:
        return False

    logger.info("⚡ [nodriver 自动打码] 成功提取 Payload，启动打码平台求解...")
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
        logger.info("✨ [nodriver 自动打码] 获取到 Token: %s..., 准备注入...", token[:15])
        js_inject = f"""
        (() => {{
            let frames = document.querySelectorAll('iframe');
            for (let f of frames) {{
                try {{
                    if (f.src.includes('arkose') || f.src.includes('funcaptcha')) {{
                        f.contentWindow.postMessage({{event: 'captcha-complete', sessionToken: '{token}'}}, '*');
                    }}
                }} catch(e) {{}}
            }}
        }})()
        """
        await page.evaluate(js_inject)
        await asyncio.sleep(3)
        return True
    
    return False

async def wait_for_arkose_and_takeover_nodriver(page: uc.Tab, max_wait: int = 360) -> bool:
    """nodriver 版本的人机协同与自动接管"""
    logger.info("🧩 [nodriver 攻防管线] 等待 Arkose 验证...")
    if await try_auto_solve_arkose_nodriver(page, timeout=8):
        logger.info("✨ [nodriver 攻防管线] 自动 Token 注入成功！")
        return True
        
    # 如果 Token 注入失败/没配，尝试本地 Whisper 降维打击
    if await solve_arkose_with_audio_nodriver(page):
        logger.info("✨ [nodriver 攻防管线] Whisper 语音验证码降维打击成功！")
        return True
        
    _banner = (
        "\n" + "=" * 70 + "\n"
        "【nodriver 人机协同时刻】请在已弹出的浏览器窗口中完成验证！\n"
        "   • 终端回车兜底：若已完成验证，可在此按【回车键】接管。\n"
        + "=" * 70 + "\n"
    )
    print(_banner)

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
        if has_msvcrt and msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b'\r', b'\n'):
                takeover = True
                break

        url_now = page.target.url
        if "/account_verifications" in url_now or "/signup/verify" in url_now:
            takeover = True
            break
            
        try:
            btn = await page.select("button#create-account:not([disabled])", timeout=0.1)
            if btn:
                await btn.click()
                takeover = True
                break
        except Exception:
            pass

        try:
            code_input = await page.select("input[name='octocaptcha-input'], input#code", timeout=0.1)
            if code_input:
                takeover = True
                break
        except Exception:
            pass

        await asyncio.sleep(1)

    return takeover

async def fill_signup_form_nodriver(page: uc.Tab, email: str, password: str, username: str) -> dict:
    """使用 nodriver 填写 GitHub 表单"""
    logger.info("📝 [nodriver] 开始填表: %s", email)
    
    try:
        await asyncio.sleep(3)
        email_input = await page.select("input#email", timeout=30)
        if not email_input:
            email_input = await page.select("input[name='user[email]']", timeout=10)
        if not email_input:
            content = await page.get_content()
            raise Exception(f"Cannot find email input field. Page length: {len(content)}")
        from core.human_dynamics import BiometricKeyboard
        
        await BiometricKeyboard.type_nodriver(page, email_input, email)
        await asyncio.sleep(0.8)
        
        btn_email = await page.select("#email-container button.signup-continue-button:not([disabled])")
        await btn_email.click()
        await asyncio.sleep(1)
        
        pw_input = await page.select("input#password", timeout=15)
        await BiometricKeyboard.type_nodriver(page, pw_input, password, typo_rate=0.0)  # 密码不模拟打错
        await asyncio.sleep(0.8)
        
        btn_pw = await page.select("#password-container button.signup-continue-button:not([disabled])")
        await btn_pw.click()
        await asyncio.sleep(1)
        
        user_input = await page.select("input#login", timeout=15)
        await BiometricKeyboard.type_nodriver(page, user_input, username, typo_rate=0.01)
        await asyncio.sleep(1.2)
        
        btn_user = await page.select("#username-container button.signup-continue-button:not([disabled])")
        await btn_user.click()
        await asyncio.sleep(1)
        
        opt_input = await page.select("input#opt_in", timeout=5)
        if opt_input:
            await BiometricKeyboard.type_nodriver(page, opt_input, "n", typo_rate=0.0)
            await asyncio.sleep(0.8)
            btn_opt = await page.select("#opt-in-container button.signup-continue-button:not([disabled])")
            if btn_opt:
                await btn_opt.click()
                await asyncio.sleep(2)

    except Exception as e:
        logger.error(f"❌ [nodriver] 填表出错: {e}")
        return {"status": "error", "error": str(e)}

    return {"status": "ok"}

async def execute_github_nodriver_flow(email: str, password: str, username: str, proxy: str = None, extra_browser_args: list = None) -> dict:
    """暴露给主程序的独立调用入口"""
    browser_args = extra_browser_args or []
    if proxy:
        browser_args.append(f"--proxy-server={proxy}")
        
    logger.info("🚀 [nodriver] 启动浏览器...")
    browser = await uc.start(browser_args=browser_args, headless=False)
    
    try:
        page = await browser.get("https://github.com/signup")
        await asyncio.sleep(4)
        
        res = await fill_signup_form_nodriver(page, email, password, username)
        if res["status"] != "ok":
            return res
            
        await wait_for_arkose_and_takeover_nodriver(page, max_wait=360)
        
        logger.info("✨ [nodriver] Arkose 处理完成，等待外部验证码注入流程...")
        
        return {"status": "waiting_for_code", "browser": browser, "page": page}
        
    except Exception as e:
        logger.error(f"💥 [nodriver] 执行崩溃: {e}")
        return {"status": "error", "error": str(e)}
