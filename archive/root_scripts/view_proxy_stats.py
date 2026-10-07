import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from core.ip_quota_manager import ip_quota_manager

def main():
    summary = ip_quota_manager.get_summary()
    proxies = summary.get('proxy_counts', {})
    if not proxies:
        print('目前没有成功注册的IP')
        return
    print('总计跟踪了', summary.get('tracked_proxies_count', 0), '个代理 IP\n')
    print('%-25s | %-6s | %-6s | %-20s | %s' % ('Session', 'Count', 'Country', 'Last Used', 'Email'))
    print('-'*90)
    sorted_proxies = sorted(proxies.items(), key=lambda x: x[1].get('count', 0), reverse=True)
    for url, data in sorted_proxies:
        try:
            session_id = url.split(':')[1].split('.')[1] if '.' in url.split(':')[1] else url
        except:
            session_id = url[:20]
        country = data.get('country', '')
        count = data.get('count', 0)
        last_used = data.get('last_used', '')[:19]
        email = data.get('last_email', '')
        display_name = country + '.' + session_id if country else session_id
        print('%-25s | %-6s | %-6s | %-20s | %s' % (display_name, count, country, last_used, email))

if __name__=='__main__':
    main()
