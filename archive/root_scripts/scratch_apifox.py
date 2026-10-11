# -*- coding: utf-8 -*-
from curl_cffi import requests
import json, re

r = requests.get('https://www.runninghub.cn/runninghub-api-doc-cn', impersonate='chrome120', timeout=20)
html = r.text

m = re.search(r'window\.__remixContext\s*=\s*(.*?);?</script>', html)
if m:
    ctx = json.loads(m.group(1))
    print("Keys in remixContext:", list(ctx.keys()))
    # dump state or route data
    state = ctx.get('state') or {}
    loader_data = state.get('loaderData') or {}
    print("LoaderData keys:", list(loader_data.keys()))
    for k, v in loader_data.items():
        if isinstance(v, dict):
            print(f"--- Key: {k} ---")
            print("  keys:", list(v.keys()))
            if 'project' in v:
                p = v['project']
                print("  Project name:", p.get('name'))
            if 'tree' in v:
                print("  Tree items:", len(v['tree']))
                for t in v['tree'][:10]:
                    print("   ", t.get('name'), t.get('type'), t.get('api', {}).get('method'), t.get('api', {}).get('path'))
            # search for api list
            dumped = json.dumps(v, ensure_ascii=False)
            apis = re.findall(r'"path":"([^"]+)"', dumped)
            if apis:
                print("  Found API paths:", list(dict.fromkeys(apis)))
