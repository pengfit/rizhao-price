#!/usr/bin/env python3
"""预览模式"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
from commands.utils import SiteSession, parse_page, TAB_TYPES

parser = argparse.ArgumentParser()
parser.add_argument('--pages', type=int, default=2)
parser.add_argument('--type', default='1', help='类别: 1=建设工程材料,2=园林绿化苗木,3=区县材料')
parser.add_argument('--material', default='', help='材料ID（左侧树节点）')
args = parser.parse_args()

session = SiteSession()
tab_name = TAB_TYPES.get(args.type, '未知类别')

print(f"[i] 类别: {tab_name} ({args.type})")

# 获取材料分类树
tree = session.get_left_column(args.type)
if tree:
    print(f"[i] 材料分类共 {len(tree)} 个节点")
    for node in tree[:5]:
        label = node.get('label', '')
        node_id = node.get('id', '')
        print(f"  - {label} ({node_id})")
    if len(tree) > 5:
        print(f"  ... 共 {len(tree)} 个")
else:
    print("[!] 材料分类树为空")

# 获取价格数据（第一页）
print(f"\n[--] 第 1 页预览 --")
data = session.get_release_price(args.type, 1, args.pages * 10, args.material)
if not data:
    print("[!] 获取数据失败")
else:
    rows, total, periods, remark = parse_page(data)
    print(f"[i] 总记录: {total}, 期数: {periods}")
    if remark:
        print(f"[i] 备注: {remark}")
    print(f"\n前 {min(args.pages, 2)} 页数据:")
    for page in range(1, min(args.pages + 1, 3)):
        if page > 1:
            data = session.get_release_price(args.type, page, 10, args.material)
            if data:
                rows, _, _, _ = parse_page(data)
        print(f"\n  -- 第 {page} 页 --")
        if not rows:
            print("  (无数据)")
            continue
        for row in rows[:5]:
            print(f"    {row.get('clmc','')} | {row.get('ggxh','')} | {row.get('dw','')} | price={row.get('price','')}")
        if len(rows) > 5:
            print(f"  ... 共 {len(rows)} 条")
