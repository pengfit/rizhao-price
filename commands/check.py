#!/usr/bin/env python3
"""检查源站是否有新数据"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
from commands.utils import SiteSession, parse_page, load_config

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')

def main():
    session = SiteSession()
    data = session.get_release_price('1', 1, 10)
    if not data:
        print("[!] 无法连接到源站")
        return

    _, _, periods, _ = parse_page(data)
    config = load_config(CONFIG_PATH)
    last_period = config.get('sync', {}).get('last_period', '') or ''

    print(f"源站最新期数: {periods}")
    print(f"本地记录期数: {last_period or '(未同步)'}")

    if last_period == periods:
        print("[—] 无新数据")
    elif not last_period:
        print("[i] 首次同步可用")
    else:
        print(f"[i] 有新数据: {last_period} → {periods}")


if __name__ == '__main__':
    main()
