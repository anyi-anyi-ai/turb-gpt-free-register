import asyncio
import base64
import json
import logging
import random
import re
import sys
import time
from typing import Optional

import cv2
import numpy as np
from playwright.async_api import async_playwright

from core.outlook_client import (
    get_account_context,
    _ms_access_token,
    _ms_http,
    _fetch_outlook_rest_messages,
)
from core.otp_utils import extract_otp
from core.runninghub_register_service import generate_strong_password
from core.runninghub2api_sync import push_account_to_rh2api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rh_e2e")

def calculate_rotate_angle(bg_bytes: bytes, move_bytes: bytes) -> float:
    """利用 OpenCV 极坐标环状互相关精准解算天爱旋转验证码角度"""
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_COLOR)
    move = cv2.imdecode(np.frombuffer(move_bytes, np.uint8), cv2.IMREAD_UNCHANGED)

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    move_gray = cv2.cvtColor(move[:, :, :3], cv2.COLOR_BGR2GRAY)

    bg_cx, bg_cy = 300, 180
    m_cx, m_cy = 100, 180

    angles = np.linspace(0, 2 * np.pi, 720, endpoint=False)

    # 采样背景外环
    bg_ring = []
    for r in range(103, 110):
        xs = np.clip((bg_cx + r * np.cos(angles)).astype(int), 0, bg.shape[1] - 1)
        ys = np.clip((bg_cy + r * np.sin(angles)).astype(int), 0, bg.shape[0] - 1)
        bg_ring.append(bg_gray[ys, xs])
    bg_ring = np.mean(bg_ring, axis=0)

    # 采样滑块内环 (避开绿色边框)
    move_ring = []
    for r in range(86, 92):
        xs = np.clip((m_cx + r * np.cos(angles)).astype(int), 0, move.shape[1] - 1)
        ys = np.clip((m_cy + r * np.sin(angles)).astype(int), 0, move.shape[0] - 1)
        move_ring.append(move_gray[ys, xs])
    move_ring = np.mean(move_ring, axis=0)

    best_corr = -1
    best_shift = 0
    for shift in range(720):
        rolled = np.roll(move_ring, shift)
        corr = np.corrcoef(bg_ring, rolled)[0, 1]
        if corr > best_corr:
            best_corr = corr
            best_shift = shift

    angle = (360.0 - (best_shift / 720.0) * 360.0) % 360.0
    logger.info(f"计算出的旋转角度: {angle:.2f}° (相关系数: {best_corr:.4f})")
    return angle


async def solve_tianai_captcha(page) -> bool:
    """自动获取图片、计算角度并拟人化拖拽滑块"""
    logger.info("检测天爱旋转验证码中...")
    try:
        slider = page.locator(".tianai-captcha-slider").first
        await slider.wait_for(state="visible", timeout=6000)
    except Exception:
        logger.info("未出现旋转滑块，直接跳过")
        return True

    # 提取 base64 图片
    bg_src = await page.locator("#tianai-captcha-slider-bg-img").get_attribute("src")
    move_src = await page.locator("#tianai-captcha-slider-move-img").get_attribute("src")

    if not bg_src or not move_src:
        logger.error("未能获取滑块图片 Base64 数据")
        return False

    bg_bytes = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", bg_src))
    move_bytes = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", move_src))

    angle = calculate_rotate_angle(bg_bytes, move_bytes)
    # 比例关系: 360 度对应 242 像素
    target_dx = (angle / 360.0) * 242.0
    logger.info(f"计算拖拽目标位移: {target_dx:.1f}px")

    btn = page.locator(".slider-move-btn").first
    btn_box = await btn.bounding_box()
    if not btn_box:
        logger.error("未找到滑块按钮位置")
        return False

    start_x = btn_box["x"] + btn_box["width"] / 2
    start_y = btn_box["y"] + btn_box["height"] / 2

    # 拟人化轨迹生成
    await page.mouse.move(start_x, start_y)
    await page.wait_for_timeout(random.randint(100, 250))
    await page.mouse.down()
    await page.wait_for_timeout(random.randint(50, 150))

    steps = random.randint(18, 28)
    cur_x = start_x
    for i in range(1, steps + 1):
        # 贝塞尔/缓动模型: 前快后慢
        t = i / steps
        # easeOutQuad: 1 - (1 - t)^2
        progress = 1.0 - (1.0 - t) * (1.0 - t)
        x = start_x + target_dx * progress
        # 模拟真人微小垂直抖动
        y = start_y + random.uniform(-1.2, 1.2)
        await page.mouse.move(x, y)
        await page.wait_for_timeout(random.randint(12, 28))

    # 轻微回弹修正
    overshoot = random.uniform(1.0, 2.5)
    await page.mouse.move(start_x + target_dx + overshoot, start_y)
    await page.wait_for_timeout(random.randint(40, 90))
    await page.mouse.move(start_x + target_dx, start_y)
    await page.wait_for_timeout(random.randint(150, 300))

    await page.mouse.up()
    logger.info("滑块拖拽完成，等待平台校验...")
    await page.wait_for_timeout(2500)

    # 检查滑块是否已消失
    is_still_there = await page.locator(".tianai-captcha-slider").count()
    if is_still_there > 0 and await page.locator(".tianai-captcha-slider").is_visible():
        logger.warning("滑块未能一次性通过，准备检查重试...")
        return False
    logger.info("🎉 旋转滑块校验通过！")
    return True


def fetch_runninghub_otp(email: str, wait_timeout: int = 120) -> Optional[str]:
    """通过 Outlook REST API 轮询读取 RunningHub 注册验证码"""
    logger.info(f"[{email}] 正在拉取邮箱验证码邮件...")
    ctx = get_account_context(email)
    if not ctx:
        logger.error(f"未找到邮箱 {email} 的凭据上下文")
        return None

    started = time.time()
    http_c = _ms_http()
    token, _ = _ms_access_token(ctx, http=http_c)

    while time.time() - started < wait_timeout:
        try:
            msgs = _fetch_outlook_rest_messages(http_c, token)
            for m in msgs:
                subject = m.get("subject", "")
                body = m.get("body", "") or m.get("snippet", "")
                full_text = f"{subject} {body}"
                if any(k in full_text.lower() for k in ["runninghub", "verification code", "verify code", "código", "验证码"]):
                    code = extract_otp(full_text)
                    if code:
                        logger.info(f"[{email}] 成功获取验证码: {code}")
                        return code
        except Exception as e:
            logger.debug(f"读信异常重试: {e}")

        time.sleep(3)

    logger.warning(f"[{email}] 获取邮件验证码超时")
    return None


async def run_full_registration(email: str, proxy_url: str, invite_code: str = "rh-v1180"):
    password = generate_strong_password()
    logger.info(f"==== 开始 RunningHub 注册流程: {email} ====")
    logger.info(f"固定代理: {proxy_url}")
    logger.info(f"随机密码: {password}")
    logger.info(f"新人邀请: {invite_code}")

    from urllib.parse import urlsplit
    u = urlsplit(proxy_url.strip())
    proxy_cfg = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        proxy_cfg["username"] = u.username
    if u.password:
        proxy_cfg["password"] = u.password

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=proxy_cfg)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        try:
            from playwright_stealth import Stealth
            await Stealth().apply_stealth_async(page)
        except Exception:
            pass

        # 1. 访问首页
        logger.info("1. 正在访问 RunningHub 首页...")
        await page.goto("https://www.runninghub.ai", timeout=35000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 2. 点击可见的 Sign In 按钮
        login_btns = await page.locator("button.login-btn, button:has-text('Sign In')").all()
        for b in login_btns:
            if await b.is_visible():
                await b.click(force=True)
                break
        await page.wait_for_timeout(2000)

        # 3. 点击 Email Login 并切换至 SIGN UP
        await page.locator("*:has-text('Email Login')").last.click(force=True)
        await page.wait_for_timeout(1500)
        await page.locator("*:has-text('SIGN UP')").last.click(force=True)
        await page.wait_for_timeout(2000)

        # 4. 填写邮箱与密码并点击下一步
        logger.info("2. 填写注册表单 (Email & Password)...")
        inputs = await page.locator(".loginAndRegisterModal input, .ant-modal input").all()
        await inputs[0].fill(email)
        await page.wait_for_timeout(300)
        await inputs[1].fill(password)
        await page.wait_for_timeout(300)
        await inputs[2].fill(password)
        await page.wait_for_timeout(500)

        logger.info("3. 点击 Next Step...")
        await page.locator("button:has-text('Next Step')").first.click(force=True)
        await page.wait_for_timeout(3000)

        # 5. 解算天爱旋转滑块
        solved = False
        for attempt in range(3):
            solved = await solve_tianai_captcha(page)
            if solved:
                break
            await page.wait_for_timeout(1500)

        if not solved:
            await page.screenshot(path="rh_captcha_failed.png")
            raise RuntimeError("天爱旋转滑块校验失败")

        await page.screenshot(path="rh_step_otp_view.png")
        logger.info("4. 已进入验证码填写界面，截图 rh_step_otp_view.png")

        # 6. 获取邮件 OTP
        otp = fetch_runninghub_otp(email, wait_timeout=120)
        if not otp:
            raise RuntimeError("未能在指定时间内收到邮箱验证码")

        # 7. 填写 OTP 和邀请码
        logger.info(f"5. 填入验证码: {otp}")
        otp_inputs = await page.locator("input[placeholder*='code' i], input[placeholder*='verification' i], input[placeholder*='Code' i]").all()
        if otp_inputs:
            await otp_inputs[0].fill(otp)
        else:
            # 备用：按位置寻找可见输入框
            all_vis_inputs = [inp for inp in (await page.locator("input").all()) if await inp.is_visible()]
            if all_vis_inputs:
                await all_vis_inputs[0].fill(otp)

        # 填写邀请码
        if invite_code:
            invite_inputs = await page.locator("input[placeholder*='invite' i], input[placeholder*='referral' i]").all()
            if invite_inputs:
                logger.info(f"填入邀请码: {invite_code}")
                await invite_inputs[0].fill(invite_code)

        # 点击提交完成注册
        logger.info("6. 提交完成注册...")
        submit_btn = page.locator("button.ant-btn-primary:not(.login-btn), button:has-text('Sign Up'), button:has-text('Register'), button:has-text('Complete'), button:has-text('Confirm')").first
        await submit_btn.click(force=True)
        await page.wait_for_timeout(6000)

        await page.screenshot(path="rh_registered_success.png")

        # 8. 提取鉴权信息
        storage = await page.evaluate("() => ({ ...localStorage })")
        cookies = await context.cookies()

        token = ""
        user_id = ""
        for k in ("Rh-Accesstoken", "rh-accesstoken", "token", "access_token"):
            if k in storage and storage[k]:
                token = storage[k]
                break
        if not token:
            for c in cookies:
                if c["name"] in ("Rh-Accesstoken", "rh-accesstoken", "token"):
                    token = c["value"]
                    break

        if "userInfo" in storage:
            try:
                uinfo = json.loads(storage["userInfo"])
                user_id = str(uinfo.get("id") or uinfo.get("userId") or "")
            except Exception:
                pass

        if not token:
            uinfo_eval = await page.evaluate("""async () => {
                try {
                    const res = await fetch('/uc/getUserInfo', { credentials: 'include' });
                    return await res.json();
                } catch(e) { return null; }
            }""")
            if uinfo_eval and uinfo_eval.get("data"):
                user_id = str(uinfo_eval["data"].get("userId") or uinfo_eval["data"].get("id") or "")

        logger.info(f"🎉 注册完成！User ID: {user_id or email}, Token: {token[:12] if token else 'None'}...")

        # 9. 入库网关
        if token:
            push_ok = push_account_to_rh2api(
                token=token,
                user_id=user_id or email,
                email=email,
                proxy=proxy_url,
                total_coin=1000,
                remark=f"自动化注册 (邀请码: {invite_code})",
            )
            logger.info(f"网关入库结果: {push_ok}")

        await browser.close()
        return token


if __name__ == "__main__":
    test_email = "TimNorris51145qb@outlook.com"
    test_proxy = "http://US:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"
    asyncio.run(run_full_registration(test_email, test_proxy))
