#!/usr/bin/env python3
"""查看同步状态"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
from commands.utils import load_config

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')

def main():
    config = load_config(CONFIG_PATH)
    es_host = config.get('es', {}).get('host', 'http://localhost:59200')
    es_index = config.get('es', {}).get('index', 'material_rizhao_price')
    progress_index = config.get('es', {}).get('sync_progress_index', 'material_rizhao_price_sync_progress')
    last_period = config.get('sync', {}).get('last_period', '')

    # 本地进度文件
    progress_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        '.rizhao_sync_progress.json'
    )
    local = {}
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            local = json.load(f)

    print("=== 日照材料价格同步状态 ===")
    print(f"ES 索引: {es_index}")
    print(f"进度索引: {progress_index}")
    print(f"上次同步期数: {last_period or '(未同步)'}")
    if local:
        print(f"本地进度: 类别={local.get('tab_type')}, 期数={local.get('period')}, 页={local.get('page')}")
    else:
        print("本地进度: (无)")

    # ES 中的最近运行记录
    try:
        import requests
        resp = requests.get(
            f"{es_host}/{progress_index}/_search",
            json={"size": 3, "sort": [{"last_updated": "desc"}]},
            timeout=15, verify=False
        )
        if resp.status_code == 200:
            hits = resp.json().get('hits', {}).get('hits', [])
            if hits:
                print(f"\nES 中最近 {len(hits)} 条运行记录:")
                for h in hits:
                    s = h.get('_source', {})
                    print(f"  run_id={s.get('run_id')} status={s.get('status')} "
                          f"type={s.get('tab_type')} page={s.get('current_page')}/{s.get('total_pages')} "
                          f"docs={s.get('docs_written')} percent={s.get('percent')}%")
            else:
                print("\nES 中无运行记录")
    except Exception as e:
        print(f"\n[!] 无法查询 ES: {e}")


if __name__ == '__main__':
    main()
