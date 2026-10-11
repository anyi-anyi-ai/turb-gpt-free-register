# -*- coding: utf-8 -*-
import io
import re
import sys
import time
import base64
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "references" / "captcha-solver"))
from recaptcha.onnx_classifier import get_classifier

classifier = get_classifier()
print(f"Loaded ONNX classifier: {classifier}", flush=True)

upstream_proxy = "http://FAST_UPSTREAM:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"
m = re.match(r"^(?:https?|socks5h?)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$", upstream_proxy)
u, p, h, pt = m.groups()


def solve_recaptcha_sync(page, max_attempts=8):
    """Automatically solve reCAPTCHA v2 / invisible bframe challenge using local ONNX classifier."""
    for attempt in range(max_attempts):
        print(f"\n[Solver] Attempt {attempt+1}/{max_attempts}...", flush=True)
        time.sleep(1.5)
        
        # 1. Locate bframe
        bframe = None
        for frame in page.frames:
            if "bframe" in (frame.url or ""):
                bframe = frame
                break

        if not bframe:
            # Check frame locator
            try:
                fl = page.frame_locator("iframe[title*='recaptcha challenge']")
                if fl.locator("#recaptcha-verify-button").is_visible(timeout=1500):
                    for frame in page.frames:
                        if "bframe" in (frame.url or ""):
                            bframe = frame
                            break
            except Exception:
                pass

        if not bframe:
            print("[Solver] No bframe detected. Challenge might not be required or already closed.", flush=True)
            return True

        # Check if verify button is visible in bframe
        try:
            btn = bframe.locator("#recaptcha-verify-button")
            if not btn.is_visible(timeout=2000):
                print("[Solver] Verify button is not visible. Challenge closed!", flush=True)
                return True
        except Exception:
            return True

        # 2. Extract metadata
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
        print(f"[Solver] Challenge Meta: {meta}", flush=True)
        target = meta.get("target", "").strip()
        rows = meta.get("rows", 0)
        cols = meta.get("cols", 0)
        dynamic = meta.get("dynamic", False)

        if not rows or not target:
            print("[Solver] Waiting for table / target to render...", flush=True)
            time.sleep(2)
            continue

        # 3. Screenshot table
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

        print(f"[Solver] Matches found: {len(matches)}/{rows*cols} for target '{target}' -> {[m[0] for m in matches]}", flush=True)

        for idx, r, c in matches:
            if idx < len(tiles):
                print(f"  -> Clicking tile {idx}...", flush=True)
                tiles[idx].click()
                time.sleep(0.4)

        if dynamic and matches:
            print("[Solver] Dynamic challenge: waiting 2.5s for new tiles to load...", flush=True)
            time.sleep(2.5)
            # Re-check this round before clicking verify
            continue

        time.sleep(1)
        print("  -> Clicking Verify button...", flush=True)
        try:
            bframe.locator("#recaptcha-verify-button").click(timeout=3000)
        except Exception as e:
            print(f"  -> Verify click error: {e}", flush=True)

        time.sleep(3)

        # Check if resolved
        try:
            if not bframe.locator("#recaptcha-verify-button").is_visible(timeout=2000):
                print(">>> SUCCESS: reCAPTCHA challenge modal has closed!", flush=True)
                return True
        except Exception:
            return True

    return False


print("Launching browser with FAST_UPSTREAM...", flush=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        proxy={"server": f"http://{h}:{pt}", "username": u, "password": p},
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    # Block heavy tracking/ads
    def block_ads(route):
        url = route.request.url.lower()
        if any(bad in url for bad in ["bing.com", "twitter.com", "t.co", "doubleclick.net", "sentry.io", "google-analytics.com", "analytics.google"]):
            return route.abort()
        return route.continue_()

    page.route("**/*", block_ads)

    # Listen to API register requests
    api_results = []
    def on_response(res):
        if "api/v2/register" in res.url or ("register" in res.url and res.request.method == "POST"):
            print(f"\n🔥🔥🔥 [API RESPONSE {res.status}] {res.url}", flush=True)
            try:
                body = res.text()[:500]
                print(f"Response Body: {body}\n", flush=True)
                api_results.append((res.status, body))
            except Exception:
                pass

    page.on("response", on_response)
    page.on("console", lambda m: print(f"[CONSOLE] {m.text}", flush=True) if "error" in m.type.lower() else None)

    print("Navigating to https://dashboard.webshare.io/register...", flush=True)
    page.goto("https://dashboard.webshare.io/register", timeout=35000, wait_until="domcontentloaded")

    print("Waiting for grecaptcha to hydrate...", flush=True)
    page.wait_for_function("() => typeof window.grecaptcha !== 'undefined' && typeof window.grecaptcha.render === 'function'", timeout=30000)
    print(">>> grecaptcha is ready!", flush=True)

    test_email = "lzhang47@alumno.uned.es"
    test_pass = "TestP@ssw0rd9876!"
    print(f"Filling form with: {test_email}...", flush=True)
    page.wait_for_selector('input[type="email"], input[name="email"]', timeout=10000)
    page.fill('input[type="email"], input[name="email"]', test_email)
    time.sleep(0.5)
    page.fill('input[type="password"], input[name="password"]', test_pass)
    time.sleep(0.5)

    print("Checking Terms of Service checkbox...", flush=True)
    page.locator('input[type="checkbox"]').first.check(force=True)
    time.sleep(0.5)

    print("Clicking 'Sign Up With Email' button...", flush=True)
    page.locator('button[data-testid="signup-button"]').first.click()

    print("Waiting 4s for possible reCAPTCHA modal...", flush=True)
    time.sleep(4)

    print("Running solve_recaptcha_sync...", flush=True)
    solved = solve_recaptcha_sync(page, max_attempts=8)
    print(f"Solver returned: {solved}", flush=True)

    print("Waiting 15s for registration API response...", flush=True)
    for _ in range(15):
        time.sleep(1)
        if api_results:
            break

    page.screenshot(path="webshare_final_result.png")
    print("Screenshot saved to webshare_final_result.png", flush=True)
    print(f"Final URL: {page.url}", flush=True)
    browser.close()
    print("Test run completed!", flush=True)
