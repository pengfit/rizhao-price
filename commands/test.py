#!/usr/bin/env python3
"""测试 ES 连接和 API 接口"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
from commands.utils import load_config, SiteSession

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')

def main():
    config = load_config(CONFIG_PATH)
    es_host = config.get('es', {}).get('host', 'http://localhost:59200')
    es_index = config.get('es', {}).get('index', 'material_rizhao_price')

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

    print("\n=== 源站 API 测试 ===")
    session = SiteSession()

    print("gettabcolumn...")
    tabs, heads = session.get_tabs()
    if tabs:
        print(f"[✓] 获取到 {len(tabs)} 个类别")
        for t in tabs:
            print(f"  - {t.get('name')} (id={t.get('id')})")
    else:
        print("[✗] 获取类别失败")

    print("\ngetleftcolumn (type=1)...")
    tree = session.get_left_column('1')
    if tree is not None:
        print(f"[✓] 材料分类树: {len(tree)} 个节点")
    else:
        print("[✗] 材料分类树为空")

    print("\ngetreleaseprice (第1页)...")
    data = session.get_release_price('1', 1, 10)
    if data:
        from commands.utils import parse_page
        rows, total, periods, remark = parse_page(data)
        print(f"[✓] 数据: total={total}, periods={periods}, rows={len(rows)}")
        if rows:
            r = rows[0]
            print(f"  示例: clmc={r.get('clmc')}, ggxh={r.get('ggxh')}, dw={r.get('dw')}, price={r.get('price')}")
        if remark:
            print(f"  备注: {remark[:80]}...")
    else:
        print("[✗] 获取价格数据失败")


if __name__ == '__main__':
    main()
