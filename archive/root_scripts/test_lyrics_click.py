import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
row = cur.fetchone()
email, jwt, cookies_json = row
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)

try:
    driver.get("https://suno.com/")
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get("https://suno.com/create")
    time.sleep(6)
    
    # Click near the lyrics area
    print("[*] Clicking on Lyrics text...")
    lyrics_area = driver.page.locator("text='Start writing lyrics'").first
    if lyrics_area.count() > 0:
        lyrics_area.click()
        time.sleep(1)
        print("[*] Clicked 'Start writing lyrics'")
    else:
        # Click on Lyrics header
        driver.page.locator("text='Lyrics'").first.click()
        time.sleep(1)
        print("[*] Clicked 'Lyrics'")
        
    driver.page.screenshot(path="after_lyrics_click.png")
    
    # Inspect focused element
    focused = driver.page.evaluate("""() => {
        const el = document.activeElement;
        return el ? {
            tag: el.tagName,
            type: el.type,
            placeholder: el.placeholder,
            className: el.className,
            contentEditable: el.isContentEditable
        } : null;
    }""")
    print("[*] Focused element after click:", focused)

finally:
    driver.quit()
