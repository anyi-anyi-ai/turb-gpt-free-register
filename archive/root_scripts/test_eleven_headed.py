import sys
import time
from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

proxy = {"server": "http://127.0.0.1:7890"}

print("[*] 正在启动真实有头 Chrome 浏览器 (channel='chrome')...", flush=True)
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",
        proxy=proxy,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(no_viewport=True)
    page = context.new_page()

    print("[*] 正在打开 ElevenLabs 注册页面...", flush=True)
    page.goto("https://elevenlabs.io/sign-up")
    page.wait_for_selector('input[type="email"]', timeout=15000)

    print("[*] 正在填写邮箱与密码...", flush=True)
    page.locator('input[type="email"]').first.fill("veronicaclark27425ii@outlook.com")
    time.sleep(0.5)
    page.locator('input[type="password"]').first.fill("ElevenPassword#2026!")
    time.sleep(1.0)

    print("[*] 正在提交表单唤出验证码...", flush=True)
    page.locator('input[type="password"]').first.press("Enter")

    print("\n" + "=" * 70, flush=True)
    print("  【人机协作提示】真实有头 Chrome 浏览器窗口已弹出！", flush=True)
    print("  [*] 请在浏览器中央点击完成 hCaptcha 镜像翻转拼图。", flush=True)
    print("=" * 70 + "\n", flush=True)

    # 保持浏览器打开 15 秒供观察
    time.sleep(15)
    browser.close()

print("[*] 测试完成。", flush=True)
