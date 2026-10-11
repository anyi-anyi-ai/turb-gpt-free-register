import asyncio
import os
import json
import sqlite3
import logging
from pathlib import Path
from curl_cffi import requests as c_req
from playwright.async_api import async_playwright
from core.video_task_manager import task_manager

logger = logging.getLogger("video_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "turb.sqlite3"
VIDEOS_DIR = BASE_DIR / "data" / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

PROXY_SERVER = "http://127.0.0.1:2260"
PROXY_USER = "US.sess_982842"
PROXY_PASS = "resin_proxy_c3d7b8e1f0a2496582d9183b"
PROXY_AUTH_URL = f"http://{PROXY_USER}:{PROXY_PASS}@127.0.0.1:2260"

def get_db_accounts():
    """从数据库获取可能可用的账号列表"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT email, sso_token FROM grok_accounts WHERE status = 'healthy' AND length(sso_token) > 50")
    rows = cur.fetchall()
    conn.close()
    return rows

def ensure_account_onboarded(email: str, sso_val: str) -> bool:
    """通过 HTTP 自动完成 TOS 接受与出生年份提交，消除弹窗拦截"""
    session = c_req.Session(impersonate="chrome124")
    session.proxies = {"http": PROXY_AUTH_URL, "https": PROXY_AUTH_URL}
    session.cookies.set("sso", sso_val, domain="grok.com")
    session.cookies.set("sso-rw", sso_val, domain="grok.com")

    headers_base = {
        "Accept": "*/*",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    try:
        # 1. TOS
        session.post("https://grok.com/tos-gate", json=[{"tosVersion": 6}], headers=headers_base, timeout=15)
        # 2. BirthDate (Year 2000)
        headers_age = dict(headers_base)
        headers_age["Accept"] = "text/x-component"
        headers_age["Content-Type"] = "text/plain;charset=UTF-8"
        headers_age["next-action"] = "7f36fca64ed253dffdaa43984be80a3c29335a937e"
        session.post("https://grok.com/", data='[{"birthDate":{"seconds":"946771200","nanos":0}}]', headers=headers_age, timeout=15)
        return True
    except Exception as e:
        logger.warning(f"Onboarding error for {email}: {e}")
        return False

def check_account_video_quota(email: str, sso_val: str) -> int:
    """查询账号剩余视频额度"""
    session = c_req.Session(impersonate="chrome124")
    session.proxies = {"http": PROXY_AUTH_URL, "https": PROXY_AUTH_URL}
    session.cookies.set("sso", sso_val, domain="grok.com")
    session.cookies.set("sso-rw", sso_val, domain="grok.com")

    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/imagine",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    try:
        r = session.post("https://grok.com/rest/media/imagine/quota_info", json={}, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return (data.get("video720p") or {}).get("remainingQueries", 0)
    except Exception as e:
        logger.warning(f"Check quota error for {email}: {e}")
    return 0

def select_available_account():
    """挑选一个具有可用视频额度的健康账号"""
    accounts = get_db_accounts()
    for email, sso in accounts:
        sso_val = sso.replace("sso=", "").strip()
        quota = check_account_video_quota(email, sso_val)
        if quota > 0:
            # 顺便预激活一次合规协议
            ensure_account_onboarded(email, sso_val)
            return email, sso_val
    return None, None

async def execute_video_generation(task_id: str):
    """后台执行真实的 Grok 视频生成全流程"""
    task = task_manager.get_task(task_id)
    if not task:
        return

    logger.info(f"[{task_id}] Selecting account for video generation...")
    email, sso_val = select_available_account()
    if not email:
        logger.error(f"[{task_id}] No available accounts with remaining video quota!")
        task_manager.update_task(task_id, status="failed", error="No available Grok accounts with video quota in pool")
        return

    task_manager.update_task(task_id, status="in_progress", progress=5, account_used=email)
    logger.info(f"[{task_id}] Assigned account: {email}")

    prompt = task.get("prompt", "")

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                proxy={
                    "server": PROXY_SERVER,
                    "username": PROXY_USER,
                    "password": PROXY_PASS
                }
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 850}
            )

            await context.add_cookies([
                {"name": "sso", "value": sso_val, "domain": ".grok.com", "path": "/"},
                {"name": "sso-rw", "value": sso_val, "domain": ".grok.com", "path": "/"},
            ])

            page = await context.new_page()

            logger.info(f"[{task_id}] Navigating to https://grok.com/imagine ...")
            await page.goto("https://grok.com/imagine", wait_until="domcontentloaded", timeout=60000)

            # 等待前端加载
            for _ in range(15):
                await asyncio.sleep(1)
                for btn_txt in ["Got it", "Dismiss", "Close"]:
                    b = await page.query_selector(f"button:has-text('{btn_txt}')")
                    if b and await b.is_visible():
                        await b.click()
                x_btn = await page.query_selector("button:has(svg.lucide-x)")
                if x_btn and await x_btn.is_visible():
                    await x_btn.click()
                img_btn = await page.query_selector("button:has-text('Image')")
                if img_btn and await img_btn.is_visible():
                    break

            task_manager.update_task(task_id, progress=15)

            # 点击切换到 Video 模式
            img_btn = await page.query_selector("button:has-text('Image')")
            if not img_btn:
                raise Exception("Failed to locate mode selector on Imagine page")

            parent_pill = await img_btn.evaluate_handle("e => e.parentElement")
            pill_buttons = await parent_pill.query_selector_all("button")
            if len(pill_buttons) < 2:
                raise Exception("Could not find Video button in selector pill")

            video_button = pill_buttons[1]
            await video_button.click()
            await asyncio.sleep(2)
            task_manager.update_task(task_id, progress=25)

            # 输入 Prompt
            editor = await page.query_selector("textarea, div[contenteditable='true'], input[type='text']")
            if not editor:
                raise Exception("Prompt editor input not found")

            await editor.click()
            await page.keyboard.type(prompt, delay=15)
            await asyncio.sleep(1)

            # 点击 Submit
            submit_btn = await page.query_selector("button[aria-label='Submit'], button[type='submit']")
            if submit_btn and await submit_btn.is_visible():
                await submit_btn.click()
            else:
                await page.keyboard.press("Enter")

            logger.info(f"[{task_id}] Video generation requested! Waiting for rendering...")
            task_manager.update_task(task_id, progress=35)

            # 轮询资产库等待生成完成
            target_asset = None
            for wait_round in range(40):
                await asyncio.sleep(3)
                progress_val = min(95, 35 + wait_round * 2)
                task_manager.update_task(task_id, progress=progress_val)

                # 请求资产接口
                assets_resp = await context.request.get("https://grok.com/rest/assets?pageSize=10&orderBy=ORDER_BY_CREATE_TIME&isLatest=true&includeImagineFiles=true")
                if assets_resp.status == 200:
                    assets_data = await assets_resp.json()
                    assets = assets_data.get("assets") or []
                    for item in assets:
                        if item.get("mimeType") == "video/mp4" and item.get("sizeBytes", 0) > 100000:
                            target_asset = item
                            break
                if target_asset:
                    logger.info(f"[{task_id}] Video asset ready in library: {target_asset.get('assetId')}")
                    break

            if not target_asset:
                raise Exception("Video generation timed out or failed to appear in library")

            # 下载视频文件
            asset_key = target_asset.get("key", "")
            download_url = f"https://assets.grok.com/{asset_key}"
            logger.info(f"[{task_id}] Downloading video from {download_url} ...")

            dl_resp = await context.request.get(download_url)
            if dl_resp.status != 200:
                raise Exception(f"Failed to download video file: HTTP {dl_resp.status}")

            video_content = await dl_resp.body()
            local_filename = f"{task_id}.mp4"
            local_file_path = str(VIDEOS_DIR / local_filename)
            with open(local_file_path, "wb") as f:
                f.write(video_content)

            file_size = len(video_content)
            width = target_asset.get("width")
            height = target_asset.get("height")

            logger.info(f"[{task_id}] Video saved successfully: {local_file_path} ({file_size} bytes)")

            import time
            task_manager.update_task(
                task_id,
                status="completed",
                progress=100,
                completed_at=int(time.time()),
                video_url=f"/v1/videos/{task_id}/content",
                remote_url=download_url,
                file_path=local_file_path,
                file_size=file_size,
                width=width,
                height=height
            )

        except Exception as e:
            logger.error(f"[{task_id}] Generation failed: {e}", exc_info=True)
            task_manager.update_task(task_id, status="failed", error=str(e))
        finally:
            if browser:
                await browser.close()
