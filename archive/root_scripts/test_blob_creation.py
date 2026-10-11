import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
email, jwt, cookies_json = cur.fetchone()
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    # Hook createObjectURL and Web Crypto / decrypt
    driver.page.evaluate('''() => {
        window.__blobs = [];
        const origCreate = URL.createObjectURL;
        URL.createObjectURL = function(obj) {
            if (obj instanceof Blob) {
                window.__blobs.push({
                    type: obj.type,
                    size: obj.size,
                    isMediaSource: false
                });
            } else if (typeof MediaSource !== 'undefined' && obj instanceof MediaSource) {
                window.__blobs.push({
                    type: 'MediaSource',
                    isMediaSource: true
                });
            }
            return origCreate.apply(this, arguments);
        };
        
        // Also check if subtle crypto decrypt is called
        if (window.crypto && window.crypto.subtle) {
            const origDecrypt = window.crypto.subtle.decrypt;
            window.crypto.subtle.decrypt = function(...args) {
                window.__crypto_called = true;
                return origDecrypt.apply(this, args);
            };
        }
    }''')
    
    # Click play
    driver.page.evaluate('''() => {
        const img = document.querySelector("img[src*='image_c861bf72']");
        if (img) (img.closest('button') || img.parentElement).click();
    }''')
    time.sleep(5)
    
    res = driver.page.evaluate('''() => {
        return {
            blobs: window.__blobs,
            cryptoCalled: window.__crypto_called || false
        };
    }''')
    print('Blob hooks result:', json.dumps(res, indent=2))
finally:
    driver.quit()
