import asyncio
import base64
import json
import random
import re
import sys
import time

import cv2
import numpy as np
from playwright.async_api import async_playwright

def calculate_rotate_angle(bg_bytes: bytes, move_bytes: bytes) -> float:
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_COLOR)
    move = cv2.imdecode(np.frombuffer(move_bytes, np.uint8), cv2.IMREAD_UNCHANGED)

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    move_gray = cv2.cvtColor(move[:, :, :3], cv2.COLOR_BGR2GRAY)

    bg_cx, bg_cy = 300, 180
    m_cx, m_cy = 100, 180

    angles = np.linspace(0, 2 * np.pi, 720, endpoint=False)

    bg_ring = []
    for r in range(103, 110):
        xs = np.clip((bg_cx + r * np.cos(angles)).astype(int), 0, bg.shape[1] - 1)
        ys = np.clip((bg_cy + r * np.sin(angles)).astype(int), 0, bg.shape[0] - 1)
        bg_ring.append(bg_gray[ys, xs])
    bg_ring = np.mean(bg_ring, axis=0)

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
    return angle, best_corr

async def run_net_diag():
    async with async_playwright() as p:
        proxy = {
            'server': 'http://127.0.0.1:2260',
            'username': 'US',
            'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'
        }
        browser = await p.chromium.launch(headless=True, proxy=proxy)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        async def on_response(res):
            url = res.url
            if any(k in url.lower() for k in ["captcha", "check", "valid", "code", "mail", "send", "register", "login"]):
                try:
                    status = res.status
                    body = await res.text()
                    print(f"\n[NET RESPONSE] {res.request.method} {url} -> {status}\n{body[:400]}", flush=True)
                except Exception:
                    pass

        page.on("response", on_response)

        print("1. 加载首页...", flush=True)
        await page.goto("https://www.runninghub.ai", timeout=35000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        login_btns = await page.locator("button.login-btn, button:has-text('Sign In')").all()
        for b in login_btns:
            if await b.is_visible():
                await b.click(force=True)
                break
        await page.wait_for_timeout(2000)

        await page.locator("*:has-text('Email Login')").last.click(force=True)
        await page.wait_for_timeout(1500)
        await page.locator("*:has-text('SIGN UP')").last.click(force=True)
        await page.wait_for_timeout(2000)

        inputs = await page.locator(".loginAndRegisterModal input, .ant-modal input").all()
        await inputs[0].fill("TimNorris51145qb@outlook.com")
        await inputs[1].fill("Aa12345678#Test")
        await inputs[2].fill("Aa12345678#Test")
        await page.wait_for_timeout(500)

        print("2. 点击 Next Step...", flush=True)
        await page.locator("button:has-text('Next Step')").first.click(force=True)
        await page.wait_for_timeout(3000)

        slider = page.locator(".tianai-captcha-slider").first
        await slider.wait_for(state="visible", timeout=6000)

        bg_src = await page.locator("#tianai-captcha-slider-bg-img").get_attribute("src")
        move_src = await page.locator("#tianai-captcha-slider-move-img").get_attribute("src")

        bg_bytes = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", bg_src))
        move_bytes = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", move_src))

        angle, corr = calculate_rotate_angle(bg_bytes, move_bytes)
        target_dx = (angle / 360.0) * 242.0
        print(f"解出角度: {angle:.2f}°, 目标位移: {target_dx:.1f}px, 相关度: {corr:.4f}", flush=True)

        btn = page.locator(".slider-move-btn").first
        btn_box = await btn.bounding_box()
        start_x = btn_box["x"] + btn_box["width"] / 2
        start_y = btn_box["y"] + btn_box["height"] / 2

        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(150)
        await page.mouse.down()
        await page.wait_for_timeout(100)

        steps = 22
        for i in range(1, steps + 1):
            t = i / steps
            progress = 1.0 - (1.0 - t) * (1.0 - t)
            x = start_x + target_dx * progress
            y = start_y + random.uniform(-0.8, 0.8)
            await page.mouse.move(x, y)
            await page.wait_for_timeout(random.randint(15, 25))

        await page.wait_for_timeout(200)
        await page.mouse.up()
        print("滑块释放，观察网络请求响应 5 秒...", flush=True)
        await page.wait_for_timeout(5000)

        await page.screenshot(path="rh_captcha_after_release.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_net_diag())
