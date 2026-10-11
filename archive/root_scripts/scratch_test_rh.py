import asyncio
import sys
from playwright.async_api import async_playwright

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

async def test_drag():
    async with async_playwright() as p:
        proxy = {
            'server': 'http://127.0.0.1:2260',
            'username': 'US',
            'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'
        }
        browser = await p.chromium.launch(headless=True, proxy=proxy)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

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
        await inputs[0].fill("drag_test_user@outlook.com")
        await inputs[1].fill("DragTest12345#")
        await inputs[2].fill("DragTest12345#")
        await page.wait_for_timeout(500)

        await page.locator("button:has-text('Next Step')").first.click(force=True)
        await page.wait_for_timeout(3000)

        btn = page.locator(".slider-move-btn").first
        track = page.locator(".slider-move-track").first
        btn_box = await btn.bounding_box()
        track_box = await track.bounding_box()
        print(f"Btn box: {btn_box}", flush=True)
        print(f"Track box: {track_box}", flush=True)

        start_x = btn_box["x"] + btn_box["width"] / 2
        start_y = btn_box["y"] + btn_box["height"] / 2

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()

        for move_px in [40, 80, 120, 160, 200]:
            await page.mouse.move(start_x + move_px, start_y)
            await page.wait_for_timeout(300)
            style = await page.locator("#tianai-captcha-slider-move-img").get_attribute("style")
            print(f"Drag +{move_px}px -> move-img style: {style}", flush=True)

        await page.mouse.up()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_drag())
