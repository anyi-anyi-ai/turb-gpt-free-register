# -*- coding: utf-8 -*-
import io
import re
import sys
import time
import uuid
import base64
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "references" / "captcha-solver"))

from recaptcha.onnx_classifier import get_classifier
from core.proxy_harvester.email_dispatcher import CascadingEmailDispatcher

classifier = get_classifier()
print(f"Loaded ONNX classifier: {classifier}", flush=True)

dispatcher = CascadingEmailDispatcher()

# 1. 动态轮换节点：由 ResinEgressManager 探针自动分配高信誉纯净节点
from core.proxy_harvester.resin_egress import ResinEgressManager
egress_mgr = ResinEgressManager()
upstream_proxy = egress_mgr.get_account_proxy(account_index=0, batch_tag=uuid.uuid4().hex[:6])
m = re.match(r"^(?:https?|socks5h?)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$", upstream_proxy)
u, p, h, pt = m.groups()
print(f"🔄 [节点轮换] 本次注册专属验证节点: {h}:{pt} (Auth: {bool(u)})", flush=True)

# 2. 邮箱调度分配
target_email, email_src = dispatcher.allocate_email(preferred_source="uned")
target_pwd = "P@ssw0rd987654321!"
print(f"📧 [邮箱调度] 分配目标邮箱: {target_email} ({email_src})", flush=True)


def solve_recaptcha_sync(page, is_api_success_fn=None, max_attempts=12):
    """有头模式下自动结合本地 ONNX 破解 reCAPTCHA 弹窗"""
    print("\n[Solver] 开始监控验证码状态与挑战弹窗...", flush=True)
    for attempt in range(max_attempts):
        if is_api_success_fn and is_api_success_fn():
            print(">>> SUCCESS: 注册接口已成功响应，无感直通！", flush=True)
            return True

        time.sleep(1.5)
        
        bframe = None
        for frame in page.frames:
            if "bframe" in (frame.url or ""):
                bframe = frame
                break

        if not bframe:
            try:
                fl = page.frame_locator("iframe[title*='recaptcha challenge']")
                if fl.locator("#recaptcha-verify-button").is_visible(timeout=1000):
                    for frame in page.frames:
                        if "bframe" in (frame.url or ""):
                            bframe = frame
                            break
            except Exception:
                pass

        if not bframe:
            if attempt < 8:
                print(f"[Solver] 等待验证码弹窗或无感放行 (轮询 {attempt+1}/8)...", flush=True)
                continue
            print("[Solver] 未检测到验证码弹窗，且未见阻断。", flush=True)
            return True

        try:
            btn = bframe.locator("#recaptcha-verify-button")
            if not btn.is_visible(timeout=3000):
                if attempt > 0:
                    print("[Solver] 验证按钮已不可见，弹窗已成功关闭！", flush=True)
                    return True
                else:
                    time.sleep(1.5)
        except Exception:
            if attempt > 0:
                return True

        meta = bframe.evaluate("""() => {
            const t = document.querySelector('table');
            const desc = document.querySelector('.rc-imageselect-desc-no-canonical, .rc-imageselect-desc');
            const strong = desc?.querySelector('strong')?.innerText;
            const rows = t ? t.rows.length : 0;
            const cols = t && t.rows[0] ? t.rows[0].cells.length : 0;
            return {
                target: strong || (desc ? desc.innerText.split('\\n')[0] : ''),
                rows, cols,
                dynamic: !!document.querySelector('.rc-imageselect-dynamic-selected') || /click verify once there are none/i.test(desc?.innerText || ''),
                has_table: !!t,
            };
        }""")
        print(f"[Solver] 验证码元数据: {meta}", flush=True)
        target = meta.get("target", "").strip()
        rows = meta.get("rows", 0)
        cols = meta.get("cols", 0)
        dynamic = meta.get("dynamic", False)

        if not rows or not target:
            time.sleep(2)
            continue

        table = bframe.locator("table")
        table_bytes = table.screenshot()
        img = Image.open(io.BytesIO(table_bytes)).convert("RGB")
        w, h = img.size
        tw, th = w // cols, h // rows

        tiles = bframe.locator("td[role=button], .rc-imageselect-tile").all()
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
                    matches.append((idx, r, c))

        print(f"[Solver] 视觉模型命中: {len(matches)}/{rows*cols} 目标='{target}' -> {[m[0] for m in matches]}", flush=True)

        for idx, r, c in matches:
            if idx < len(tiles):
                print(f"  -> 点击单元格 {idx}...", flush=True)
                tiles[idx].click()
                time.sleep(0.4)

        if dynamic and matches:
            print("[Solver] 动态消隐验证码：等待 2.5s 新图加载...", flush=True)
            time.sleep(2.5)
            continue

        time.sleep(1)
        print("  -> 点击 Verify 确认按钮...", flush=True)
        try:
            bframe.locator("#recaptcha-verify-button").click(timeout=3000)
        except Exception as e:
            print(f"  -> 确认按钮点击异常: {e}", flush=True)

        time.sleep(3)

        try:
            if not bframe.locator("#recaptcha-verify-button").is_visible(timeout=2000):
                print(">>> SUCCESS: reCAPTCHA 验证码弹窗成功解决并关闭！", flush=True)
                return True
        except Exception:
            return True

    return False


print("🌐 [有头浏览器] 启动可视化防检测 Chromium 实例...", flush=True)
with sync_playwright() as pw:
    proxy_config = {"server": f"http://{h}:{pt}"}
    if u and p:
        proxy_config["username"] = u
        proxy_config["password"] = p

    browser = pw.chromium.launch(
        headless=False,
        proxy=proxy_config,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--start-maximized",
            "--lang=en-US",
        ]
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 850},
        locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    # 监听 API 响应
    api_success = False
    api_error_msg = None
    def on_response(res):
        global api_success, api_error_msg
        if "webshare.io/api/v2/register" in res.url:
            print(f"\n🔥🔥🔥 [Webshare API 注册响应 HTTP {res.status}] {res.url}", flush=True)
            try:
                txt = res.text()[:500]
                print(f"响应报文: {txt}\n", flush=True)
                if res.status in (200, 201):
                    api_success = True
                else:
                    api_error_msg = f"HTTP {res.status}: {txt}"
            except Exception:
                pass

    page.on("response", on_response)

    print("🚀 导航至 Webshare 注册页面...", flush=True)
    page.goto("https://dashboard.webshare.io/register", timeout=40000, wait_until="domcontentloaded")

    print("⏳ 等待前端应用与 reCAPTCHA 依赖充分初始化就绪...", flush=True)
    page.wait_for_function("() => typeof window.grecaptcha !== 'undefined' && typeof window.grecaptcha.render === 'function'", timeout=30000)
    print("✅ reCAPTCHA 运行时组件已 100% 就绪！", flush=True)

    print(f"📝 正在填写表单信息: {target_email}...", flush=True)
    page.fill('input[type="email"], input[name="email"]', target_email)
    time.sleep(1)
    page.fill('input[type="password"], input[name="password"]', target_pwd)
    time.sleep(1)

    print("☑️ 勾选用户服务协议条款 (Terms of Service)...", flush=True)
    tos_cb = page.locator('[data-testid="tos-checkbox"]').first
    tos_cb.click()
    time.sleep(1)

    print("🚀 精准点击 'Sign Up With Email' 提交注册...", flush=True)
    page.locator('button[data-testid="signup-button"]').first.click()

    # 尝试自动破解验证码 (若为无感直通则快速跳过)
    solve_recaptcha_sync(page, is_api_success_fn=lambda: api_success, max_attempts=12)

    # 等待 API 注册响应返回
    print("⏳ 等待 Webshare 注册 API 响应 (最多 15 秒)...", flush=True)
    for _ in range(15):
        if api_success or api_error_msg:
            break
        time.sleep(1)

    if api_error_msg:
        print(f"❌ 注册被拒绝: {api_error_msg}", flush=True)
    elif api_success:
        print("🎉 注册请求已被 Webshare 接收成功！", flush=True)

    print(f"⏳ 正在监听 [{email_src}] 邮箱 {target_email} 的激活邮件 (超时 90s)...", flush=True)
    act_link = dispatcher.wait_for_activation_link(target_email, email_src, timeout=90)

    if act_link:
        print(f"\n🎉🎉🎉 成功捕获激活邮件链接: {act_link}", flush=True)
        print("🔗 在当前浏览器会话中直接打开激活链接...", flush=True)
        page.goto(act_link, timeout=40000, wait_until="domcontentloaded")
        time.sleep(5)
        print(f"✅ 账号激活完成！当前页面: {page.url}", flush=True)

        print("📊 访问代理列表页提取分配的节点...", flush=True)
        page.goto("https://dashboard.webshare.io/proxy/list", timeout=40000, wait_until="domcontentloaded")
        time.sleep(5)
        page.screenshot(path="webshare_activated_list.png")
        print("📸 已保存代理列表页截图至 webshare_activated_list.png", flush=True)

        token = page.evaluate("() => localStorage.getItem('token') || sessionStorage.getItem('token') || ''")
        print(f"🔑 抓取到 Webshare 认证 Token: {token}", flush=True)
        if token:
            dl_url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{token}/-/any/username/direct/-"
            res = page.request.get(dl_url)
            print(f"📥 下载代理列表 (HTTP {res.status}):", flush=True)
            if res.status == 200:
                print(res.text()[:500], flush=True)
    else:
        print("⚠️ 未收到激活邮件，保存当前有头浏览器画面用于复盘...", flush=True)
        page.screenshot(path="webshare_headed_state.png")
        print("📸 已保存截图至 webshare_headed_state.png", flush=True)

    print("流程测试执行完毕，3秒后安全关闭浏览器...", flush=True)
    time.sleep(3)
    browser.close()
