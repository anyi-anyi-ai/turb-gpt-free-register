import urllib.request
import re

req = urllib.request.Request("https://www.runninghub.ai", headers={"User-Agent": "Mozilla/5.0"})
html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
scripts = re.findall(r'src=["\'](/_nuxt/[^"\']+\.js)["\']', html)
print(f"Found scripts: {len(scripts)}")

for s in scripts:
    js_url = "https://www.runninghub.ai" + s
    try:
        req_js = urllib.request.Request(js_url, headers={"User-Agent": "Mozilla/5.0"})
        js = urllib.request.urlopen(req_js, timeout=10).read().decode("utf-8", errors="ignore")
        if "tianai" in js.lower() or "captcha" in js.lower():
            print(f"Match script: {s}")
            # 搜索与 check, track, verify, data 相关的调用
            for m in re.finditer(r"(.{0,80}(?:captcha|tianai|track|slider).{0,80})", js, re.IGNORECASE):
                snippet = m.group(0).strip()
                if any(w in snippet for w in ["check", "verify", "data", "post", "fetch", "api"]):
                    print("   ->", repr(snippet))
    except Exception as e:
        pass
