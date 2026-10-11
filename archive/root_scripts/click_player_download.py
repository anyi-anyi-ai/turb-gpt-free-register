import sqlite3
import json
import time
import os
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
row = cur.fetchone()
email, jwt, cookies_json = row
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)

try:
    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/")
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get("https://suno.com/create")
    time.sleep(6)
    
    # Click play on the first song
    print("[*] Starting playback...")
    driver.page.evaluate("""() => {
        const img = document.querySelector("img[src*='image_c861bf72']");
        if (img) (img.closest('button') || img.parentElement).click();
    }""")
    time.sleep(3)
    
    # In the bottom player bar, find the more options button (three vertical dots)
    print("[*] Finding more button in bottom player bar...")
    player_dots = driver.page.evaluate("""() => {
        // Find footer or bottom bar
        const footer = Array.from(document.querySelectorAll('div, footer')).find(el => {
            const rect = el.getBoundingClientRect();
            return rect.bottom >= window.innerHeight - 5 && rect.height > 40 && rect.width > 500;
        });
        if (!footer) return "Footer not found";
        
        // Find all buttons in footer
        const btns = Array.from(footer.querySelectorAll('button')).map((b, idx) => ({
            idx,
            text: b.innerText,
            aria: b.getAttribute('aria-label'),
            title: b.getAttribute('title'),
            className: b.className
        }));
        return { footer: true, btns };
    }""")
    print("[*] Player buttons:", json.dumps(player_dots, indent=2))
    
    # Click the more options button in footer
    print("[*] Clicking more options in footer...")
    driver.page.evaluate("""() => {
        const footer = Array.from(document.querySelectorAll('div, footer')).find(el => {
            const rect = el.getBoundingClientRect();
            return rect.bottom >= window.innerHeight - 5 && rect.height > 40 && rect.width > 500;
        });
        if (footer) {
            // Find button with SVG or dots near the right
            const btns = Array.from(footer.querySelectorAll('button'));
            // Look for more button (often has aria-label="More" or is near the right before volume/info)
            const moreBtn = btns.find(b => {
                const a = (b.getAttribute('aria-label') || '').toLowerCase();
                const t = (b.getAttribute('title') || '').toLowerCase();
                return a.includes('more') || t.includes('more') || a.includes('action');
            });
            if (moreBtn) moreBtn.click();
            else if (btns.length >= 6) {
                // Click the button before volume
                btns[btns.length - 3].click();
            }
        }
    }""")
    time.sleep(1.5)
    driver.page.screenshot(path="footer_more_menu.png")
    
    menu_texts = driver.page.evaluate("""() => {
        return Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], [data-radix-collection-item], button')).map(e => e.innerText).filter(t => t && t.trim().length > 0);
    }""")
    print("[*] Menu items open:", menu_texts)
    
    # Look for Download
    dl_btn = driver.page.locator("text='Download', text='Download Audio', text='Audio'").first
    if dl_btn.count() > 0:
        print("[*] Found download option! Clicking...")
        try:
            with driver.page.expect_download(timeout=10000) as dl_info:
                dl_btn.click()
            dl = dl_info.value
            os.makedirs("generated_music", exist_ok=True)
            p = "generated_music/频率共振_Part1.mp3"
            dl.save_as(p)
            print(f"[🎉 DOWNLOAD SUCCESS] Saved to {p} ({os.path.getsize(p)} bytes)!")
        except Exception as e:
            print("Download trigger info:", e)
            driver.page.screenshot(path="after_dl_click.png")

finally:
    driver.quit()
