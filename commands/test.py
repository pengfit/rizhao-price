#!/usr/bin/env python3
"""测试 ES 连接和源站 API"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
from commands.utils import get_metadata, load_config

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')

def main():
    config = load_config(CONFIG_PATH)
    es_host = config.get('es', {}).get('host', 'http://localhost:59200')
    es_index = config.get('es', {}).get('index', 'material_rizhao_price')
    progress_index = config.get('es', {}).get('sync_progress_index', 'material_rizhao_price_sync_progress')

    print("=== ES 连接测试 ===")
    try:
        import requests
        resp = requests.get(es_host, timeout=10, verify=False)
        print(f"[✓] ES 可达: {es_host} ({resp.status_code})")
    except Exception as e:
        print(f"[✗] ES 连接失败: {e}")
        return

    print(f"\n=== 索引: {es_index} ===")
    try:
        resp = requests.head(f"{es_host}/{es_index}", timeout=10, verify=False)
        if resp.status_code == 200:
            print(f"[✓] 索引已存在")
        else:
            print(f"[—] 索引不存在（将自动创建），状态码: {resp.status_code}")
    except Exception as e:
        print(f"[!] 检查索引失败: {e}")

    print("\n=== 源站 API 测试 (Playwright) ===")
    try:
        meta = get_metadata()
        print(f"[✓] 元数据获取成功")
        print(f"  当前期数: {meta.get('periods', '未知')}")
        print(f"  类别: {[t.get('name', '') for t in meta.get('tabs', [])]}")
    except Exception as e:
        print(f"[✗] 源站连接失败: {e}")

    print("\n=== Node.js + Playwright ===")
    import subprocess
    result = subprocess.run(
        ['node', '--version'],
        capture_output=True, text=True, timeout=10
    )
    print(f"[✓] Node.js: {result.stdout.strip()}")

    # Check chrome path
    chrome = '/Users/pengfit/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
    if os.path.exists(chrome):
        print(f"[✓] Chromium: {chrome}")
    else:
        print(f"[✗] Chromium not found at: {chrome}")


if __name__ == '__main__':
    main()
