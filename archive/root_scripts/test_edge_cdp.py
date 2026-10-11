import os
import subprocess
import time
from playwright.sync_api import sync_playwright

edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
user_data = r"F:\github\turb-gpt-free-register\data\edge_profile"
os.makedirs(user_data, exist_ok=True)

# 杀掉之前的 edge 实例确保端口可用
subprocess.run('taskkill /F /IM msedge.exe /T', shell=True, capture_output=True)
time.sleep(1)

# 启动 Edge 并直达目标页面
cmd = f'cmd.exe /c start "" "{edge_path}" --remote-debugging-port=9222 --user-data-dir="{user_data}" --no-first-run --no-default-browser-check --proxy-server="http://127.0.0.1:7890" --new-window "https://elevenlabs.io/sign-up"'
print("Launching Edge directly to desktop:", cmd)
subprocess.run(cmd, shell=True)

time.sleep(3)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    
    # 获取或创建目标页面
    page = None
    for pg in context.pages:
        if "elevenlabs" in pg.url:
            page = pg
            break
    if not page:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://elevenlabs.io/sign-up")

    page.wait_for_selector('input[type="email"]', timeout=30000)
    print("Page ready! Filling form...")

    email = "veronicaclark27425ii@outlook.com"
    pwd = "ElevenPassword#2026!"

    page.locator('input[type="email"]').first.fill(email)
    time.sleep(0.5)
    page.locator('input[type="password"]').first.fill(pwd)
    time.sleep(1.0)

    print("Pressing Enter to trigger hCaptcha...")
    page.locator('input[type="password"]').first.press("Enter")

    print("\n" + "=" * 70)
    print("  【人机协作提示】真实 Microsoft Edge 浏览器已在您的屏幕前弹出！")
    print(f"  账号：{email}")
    print("  [*] 请在桌面弹出的 Edge 浏览器中央点击完成镜像翻转拼图。")
    print("=" * 70 + "\n")

print("Done. Edge remains open for user interaction.")
