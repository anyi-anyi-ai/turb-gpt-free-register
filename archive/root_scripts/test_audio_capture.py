import sqlite3
import json
import time
import base64
import os
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
    
    # Check what happens in JS when audio plays
    js_test = driver.page.evaluate('''async () => {
        // Inspect window objects, MediaSource, AudioContext
        const results = {
            hasAudioContext: typeof window.AudioContext !== 'undefined',
            hasMediaSource: typeof window.MediaSource !== 'undefined'
        };
        
        // Find audio elements
        const audios = Array.from(document.querySelectorAll('audio'));
        results.audioCount = audios.length;
        
        // Check if we can intercept AudioContext decodeAudioData
        return results;
    }''')
    print("[*] JS inspection:", js_test)
    
    # Now let's see how the frontend plays audio:
    # Hook fetch / XMLHttpRequest to see what headers or responses happen when audio is requested
    driver.page.evaluate('''() => {
        window.__audio_chunks = [];
        window.__audio_headers = [];
        const origFetch = window.fetch;
        window.fetch = async function(...args) {
            const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
            if (url.includes('.m4a') || url.includes('/clip/')) {
                console.log("[HOOKED FETCH]", url, args[1]);
                const resp = await origFetch.apply(this, args);
                const clone = resp.clone();
                const buf = await clone.arrayBuffer();
                window.__audio_chunks.push({
                    url: url,
                    status: resp.status,
                    size: buf.byteLength,
                    headerType: resp.headers.get('content-type'),
                    headerEncoding: resp.headers.get('content-encoding')
                });
                return resp;
            }
            return origFetch.apply(this, args);
        };
    }''')
    
    # Click play on the first song
    print("[*] Clicking play...")
    driver.page.evaluate('''() => {
        const img = document.querySelector("img[src*='image_c861bf72']");
        if (img) {
            const btn = img.closest('button') || img.parentElement;
            btn.click();
        }
    }''')
    time.sleep(6)
    
    captured = driver.page.evaluate('''() => {
        return {
            chunks: window.__audio_chunks,
            audios: Array.from(document.querySelectorAll('audio')).map(a => ({
                src: a.src,
                duration: a.duration,
                currentTime: a.currentTime,
                paused: a.paused
            }))
        };
    }''')
    print("[*] Captured audio fetch:", json.dumps(captured, indent=2))
    
finally:
    driver.quit()
