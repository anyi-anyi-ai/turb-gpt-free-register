import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts LIMIT 1')
jwt, cj = cur.fetchone()
cookies_dict = json.loads(cj)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    song_url = 'https://suno.com/song/d68c89c4-bbf5-41fc-bd44-29bbe75ecc91'
    print(f"Navigating to song page: {song_url}")
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get(song_url)
    time.sleep(6)
    driver.page.screenshot(path='final_song_page_view.png')
    
    details = driver.page.evaluate('''() => {
        return {
            title: document.title,
            h1: document.querySelector('h1')?.innerText,
            metaDesc: document.querySelector('meta[name="description"]')?.content,
            pageText: document.body.innerText.slice(0, 400)
        };
    }''')
    print("Song page verified:", json.dumps(details, indent=2, ensure_ascii=False))
finally:
    driver.quit()
