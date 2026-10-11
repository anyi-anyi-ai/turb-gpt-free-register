# -*- coding: utf-8 -*-
"""
GitHub 自动注册的 nodriver 版本 (PoC)

架构说明：
- 主干注册流程（填表、防指纹、Arkose 验证）交由 nodriver 处理（绕过 CDP 探测）。
- 验证码读取（Outlook 取信）依然复用现存的 Playwright 基建（并行运行）。
"""
import argparse
import asyncio
import os
import random
import time

from common.stealth_playwright import async_playwright

import tempfile
import zipfile

from core.browser.nodriver_github import execute_github_nodriver_flow
from common.mailbox import get_code_outlook_pw
from common import proxy_switch
from register_github import rand_username, rand_password
from common.asset_store import get_email

def create_proxy_auth_extension(host, port, username, password, scheme="http"):
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Nodriver Proxy Auth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {
            "scripts": ["background.js"]
        },
        "minimum_chrome_version":"22.0.0"
    }
    """

    background_js = """
    var config = {
            mode: "fixed_servers",
            rules: {
              singleProxy: {
                scheme: "%s",
                host: "%s",
                port: parseInt(%s)
              },
              bypassList: ["localhost"]
            }
          };

    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

    function callbackFn(details) {
        return {
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        };
    }

    chrome.webRequest.onAuthRequired.addListener(
                callbackFn,
                {urls: ["<all_urls>"]},
                ['blocking']
    );
    """ % (scheme, host, port, username, password)

    plugin_dir = tempfile.mkdtemp(prefix="proxy_ext_")
    with open(os.path.join(plugin_dir, "manifest.json"), "w") as f:
        f.write(manifest_json)
    with open(os.path.join(plugin_dir, "background.js"), "w") as f:
        f.write(background_js)
    return plugin_dir

async def process_one(email, password, cookies, proxy_str, ext_path, p):
    gh_password = rand_password()
    username = rand_username()
    
    print(f"\n🚀 [PoC] 启动 nodriver 处理 GitHub: {email}")
    
    extra_args = []
    if ext_path:
        extra_args.append(f"--load-extension={ext_path}")
        
    res = await execute_github_nodriver_flow(email, gh_password, username, proxy_str, extra_args)
    
    if res.get("status") != "waiting_for_code":
        print(f"❌ nodriver 执行失败: {res}")
        return False
        
    browser = res["browser"]
    page = res["page"]
    
    try:
        # 并行使用 Playwright 去 Outlook 取件
        print("📨 [PoC] 启动 Playwright 获取 Outlook 验证码...")
        mail_browser = await p.chromium.launch(headless=True)
        mail_ctx = await mail_browser.new_context()
        mail_page = await mail_ctx.new_page()
        
        code = None
        try:
            code = await get_code_outlook_pw(
                mail_page, email, password,
                sender_hint=("github", "noreply@github.com", "notifications"),
                subject_hint=("launch code", "github", "verify", "code"),
                code_regex=r"\b(\d{6,8})\b", max_wait=120, poll=5,
            )
        finally:
            await mail_browser.close()
            
        if not code:
            print("❌ 未收到 GitHub 验证码")
            return False
            
        print(f"✅ 获取到验证码: {code}")
        
        # 将验证码回填到 nodriver 页面
        code_input = await page.select("input[name='octocaptcha-input'], input#code, input[autocomplete='one-time-code']", timeout=10)
        if code_input:
            await code_input.send_keys(code)
            await asyncio.sleep(1)
            await page.keyboard.press("Enter")
            
        print("🎉 [PoC] 验证码注入完成，等待 GitHub 响应...")
        await asyncio.sleep(5)
        
        # 验证是否成功登录
        url = page.target.url
        if "github.com" in url and "signup" not in url:
            print("🌟 [PoC] 成功进入 GitHub 主页！")
        else:
            print(f"⚠️ [PoC] 停留在此页面: {url}")
            
        return True
    finally:
        print("🛑 正在关闭 nodriver 浏览器...")
        # browser.stop() is synchronous in nodriver
        browser.stop()

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=None, help="指定邮箱")
    parser.add_argument("--password", default=None, help="指定密码")
    parser.add_argument("--node", default="auto", help="代理节点")
    args = parser.parse_args()
    
    proxy_switch.apply_platform_environment("github")
    proxy_dict = proxy_switch.browser_proxy_fields().get("proxy", {})
    proxy_str = None
    ext_path = None
    if proxy_dict:
        host = proxy_dict.get('server') or proxy_dict.get('host')
        port = proxy_dict.get('port') or "80"
        user = proxy_dict.get('username')
        pwd = proxy_dict.get('password')
        
        if user and pwd:
            print(f"🔒 自动生成代理 Auth 扩展: {host}:{port}")
            ext_path = create_proxy_auth_extension(host, port, user, pwd)
        else:
            proxy_str = f"http://{host}:{port}"
            
    if args.email:
        accounts = [(args.email, args.password or "", None)]
    else:
        email_record = get_email(pristine_only=False)
        if not email_record:
            print("没有可用的邮箱")
            return
        accounts = [(email_record["email"], email_record["password"], None)]
        
    async with async_playwright() as p:
        for acc in accounts:
            await process_one(acc[0], acc[1], acc[2], proxy_str, ext_path, p)
            
if __name__ == "__main__":
    asyncio.run(main())
