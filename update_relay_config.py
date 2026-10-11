config_content = """# -*- coding: utf-8 -*-
mixed-port: 28890
allow-lan: false
external-controller: 127.0.0.1:28891
mode: rule
log-level: info
ipv6: false

dns:
  enable: true
  listen: 127.0.0.1:28853
  enhanced-mode: fake-ip
  nameserver:
    - 223.5.5.5
    - 119.29.29.29
    - 8.8.8.8

# 1. 本地 88.88 节点 (Resin 出口)
proxies:
  - name: "Resin-88.88-Primary"
    type: http
    server: 127.0.0.1
    port: 2260
    username: "FAST_UPSTREAM"
    password: "resin_proxy_c3d7b8e1f0a2496582d9183b"

# 2. 外部订阅储备池
proxy-providers:
  liuwan-subscription:
    type: file
    path: ./data/relay_providers/liuwan_sub.yaml
    health-check:
      enable: true
      url: http://www.gstatic.com/generate_204
      interval: 60
  mcii-subscription:
    type: http
    url: "https://proxy.mcii.cc/sub/617238894e43a32f47361a5f271c4308"
    path: ./data/relay_providers/mcii_sub.yaml
    interval: 3600
    health-check:
      enable: true
      url: http://www.gstatic.com/generate_204
      interval: 300

# 3. 自动故障转移与测速策略组
proxy-groups:
  - name: "AUTO-RELAY"
    type: url-test
    url: "http://www.gstatic.com/generate_204"
    interval: 60
    tolerance: 150
    proxies:
      - "Resin-88.88-Primary"
    use:
      - liuwan-subscription
      - mcii-subscription

rules:
  - MATCH,AUTO-RELAY
"""

with open("core/relay/config.yaml", "w", encoding="utf-8", newline="\n") as f:
    f.write(config_content.strip() + "\n")

print("Fixed config.yaml!")
