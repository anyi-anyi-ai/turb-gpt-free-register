import asyncio
import base64
import re
from playwright.async_api import async_playwright

async def dump_images():
    async with async_playwright() as p:
        proxy = {
            'server': 'http://127.0.0.1:2260',
            'username': 'US',
            'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'
        }
        browser = await p.chromium.launch(headless=True, proxy=proxy)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

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
        await inputs[0].fill("probe_rotate_user@outlook.com")
        await inputs[1].fill("TestProbe12345#")
        await inputs[2].fill("TestProbe12345#")
        await page.wait_for_timeout(500)

        await page.locator("button:has-text('Next Step')").first.click(force=True)
        await page.wait_for_timeout(3000)

        bg_src = await page.locator("#tianai-captcha-slider-bg-img").get_attribute("src")
        move_src = await page.locator("#tianai-captcha-slider-move-img").get_attribute("src")

        if bg_src:
            bg_data = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", bg_src))
            with open("captcha_bg.jpg", "wb") as f:
                f.write(bg_data)
            print("Saved captcha_bg.jpg")

        if move_src:
            move_data = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", move_src))
            with open("captcha_move.png", "wb") as f:
                f.write(move_data)
            print("Saved captcha_move.png")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(dump_images())
