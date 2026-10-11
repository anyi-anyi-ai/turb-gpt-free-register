import requests
import os

clip1_id = "c861bf72-f4dd-4671-86e3-4a397efb264b"
clip2_id = "2630eaad-1654-4064-bca6-b616ca84c286"

urls = [
    (f"clip1_m4a", f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{clip1_id}.m4a"),
    (f"clip1_mp3", f"https://cdn1.suno.ai/{clip1_id}.mp3"),
    (f"clip2_m4a", f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{clip2_id}.m4a"),
    (f"clip2_mp3", f"https://cdn1.suno.ai/{clip2_id}.mp3"),
]

os.makedirs("generated_music", exist_ok=True)

for name, u in urls:
    try:
        r = requests.get(u, proxies={'https': 'http://127.0.0.1:7890'}, timeout=30)
        print(f"[*] {name} ({u}) -> Status {r.status_code}, Length: {len(r.content)} bytes")
        if r.status_code == 200:
            ext = ".m4a" if "m4a" in name else ".mp3"
            target = f"generated_music/{name}{ext}"
            with open(target, "wb") as f:
                f.write(r.content)
            print(f"    [SAVED] {target}")
    except Exception as e:
        print(f"Error {name}: {e}")
