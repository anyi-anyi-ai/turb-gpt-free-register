import sqlite3
import json
import time
import base64
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
    
    clip_ids = [
        ("Clip1", "c861bf72-f4dd-4671-86e3-4a397efb264b"),
        ("Clip2", "2630eaad-1654-4064-bca6-b616ca84c286")
    ]
    
    os.makedirs("generated_music", exist_ok=True)
    
    for label, cid in clip_ids:
        url = f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{cid}.m4a"
        print(f"[*] Extracting and decoding {label} ({cid}) via in-browser AudioContext...")
        
        # In-browser decoding into 16-bit WAV ArrayBuffer
        wav_b64 = driver.page.evaluate(r"""async (audioUrl) => {
            try {
                const resp = await fetch(audioUrl);
                const arrayBuf = await resp.arrayBuffer();
                
                const actx = new (window.AudioContext || window.webkitAudioContext)();
                const audioBuf = await actx.decodeAudioData(arrayBuf);
                
                // Encode to 16-bit PCM WAV
                const numChannels = audioBuf.numberOfChannels;
                const sampleRate = audioBuf.sampleRate;
                const format = 1; // PCM
                const bitDepth = 16;
                
                const bytesPerSample = bitDepth / 8;
                const blockAlign = numChannels * bytesPerSample;
                const numSamples = audioBuf.length;
                const dataSize = numSamples * blockAlign;
                const buffer = new ArrayBuffer(44 + dataSize);
                const view = new DataView(buffer);
                
                // Helper to write string
                function writeString(view, offset, string) {
                    for (let i = 0; i < string.length; i++) {
                        view.setUint8(offset + i, string.charCodeAt(i));
                    }
                }
                
                // RIFF header
                writeString(view, 0, 'RIFF');
                view.setUint32(4, 36 + dataSize, true);
                writeString(view, 8, 'WAVE');
                
                // fmt subchunk
                writeString(view, 12, 'fmt ');
                view.setUint32(16, 16, true);
                view.setUint16(20, format, true);
                view.setUint16(22, numChannels, true);
                view.setUint32(24, sampleRate, true);
                view.setUint32(28, sampleRate * blockAlign, true);
                view.setUint16(32, blockAlign, true);
                view.setUint16(34, bitDepth, true);
                
                // data subchunk
                writeString(view, 36, 'data');
                view.setUint32(40, dataSize, true);
                
                // Interleave channel samples
                const channels = [];
                for (let i = 0; i < numChannels; i++) {
                    channels.push(audioBuf.getChannelData(i));
                }
                
                let offset = 44;
                for (let i = 0; i < numSamples; i++) {
                    for (let ch = 0; ch < numChannels; ch++) {
                        let sample = Math.max(-1, Math.min(1, channels[ch][i]));
                        sample = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
                        view.setInt16(offset, sample, true);
                        offset += 2;
                    }
                }
                
                // Convert to base64 in chunks
                let binary = '';
                const bytes = new Uint8Array(buffer);
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                }
                return {
                    ok: true,
                    b64: btoa(binary),
                    sampleRate: sampleRate,
                    channels: numChannels,
                    duration: audioBuf.duration
                };
            } catch (e) {
                return { ok: false, error: String(e) };
            }
        }""", url)
        
        if wav_b64.get("ok"):
            raw_bytes = base64.b64decode(wav_b64["b64"])
            wav_path = f"generated_music/极光变奏_星穹的极昼_{label}.wav"
            with open(wav_path, "wb") as f:
                f.write(raw_bytes)
            print(f"[✅ SUCCESS] Decoded {label}: {wav_b64['duration']:.2f}s, {wav_b64['sampleRate']}Hz, {wav_b64['channels']}ch -> {wav_path} ({len(raw_bytes)} bytes)")
            
            # Convert WAV to 320k MP3 via ffmpeg
            mp3_path = f"generated_music/极光变奏_星穹的极昼_{label}.mp3"
            os.system(f'ffmpeg -y -i "{wav_path}" -c:a libmp3lame -b:a 320k "{mp3_path}" >nul 2>&1')
            if os.path.exists(mp3_path):
                print(f"[✅ SUCCESS] Converted to High-Quality 320k MP3 -> {mp3_path} ({os.path.getsize(mp3_path)} bytes)")
        else:
            print(f"Error decoding {label}: {wav_b64.get('error')}")

finally:
    driver.quit()
