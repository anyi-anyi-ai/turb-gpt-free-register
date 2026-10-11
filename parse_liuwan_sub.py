import urllib.parse
import yaml
from pathlib import Path

b64_file = Path("core/relay/data/liuwan_raw.b64")
import base64
text = base64.b64decode(b64_file.read_bytes() + b'===').decode('utf-8', errors='ignore')

proxies = []

for line in text.splitlines():
    line = line.strip()
    if not line.startswith("vless://"):
        continue
    
    url = urllib.parse.urlparse(line)
    user_info = url.netloc.split("@")
    if len(user_info) != 2:
        continue
    uuid = user_info[0]
    host_port = user_info[1].split(":")
    server = host_port[0]
    port = int(host_port[1])
    
    # Skip loopback announcement nodes
    if server == "127.0.0.1":
        continue
        
    query = urllib.parse.parse_qs(url.query)
    name = urllib.parse.unquote(url.fragment) if url.fragment else f"vless-{server}"
    
    # Clean up name
    name = name.replace("[", "").replace("]", "").replace(" ", "_")
    
    sni = query.get("sni", [""])[0]
    host = query.get("host", [""])[0]
    path = query.get("path", ["/"])[0]
    security = query.get("security", ["none"])[0]
    net_type = query.get("type", ["tcp"])[0]
    
    node = {
        "name": name,
        "type": "vless",
        "server": server,
        "port": port,
        "uuid": uuid,
        "udp": True,
        "tls": security == "tls",
        "skip-cert-verify": True,
        "servername": sni,
        "network": net_type,
        "client-fingerprint": "chrome"
    }
    
    if net_type == "ws":
        node["ws-opts"] = {
            "path": path,
            "headers": {
                "Host": host or sni
            }
        }
        
    proxies.append(node)

out_file = Path("core/relay/data/relay_providers/liuwan_sub.yaml")
out_data = {"proxies": proxies}
with open(out_file, "w", encoding="utf-8") as f:
    yaml.dump(out_data, f, allow_unicode=True, sort_keys=False)

print(f"Generated {len(proxies)} vless proxies into {out_file}")
for p in proxies:
    print(f"  - {p['name']} ({p['server']}:{p['port']}, sni={p['servername']})")
