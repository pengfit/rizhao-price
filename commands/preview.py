#!/usr/bin/env python3
"""预览模式"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
from commands.utils import BrowserSession, TAB_TYPES, get_metadata

parser = argparse.ArgumentParser()
parser.add_argument('--pages', type=int, default=2)
parser.add_argument('--type', default='1', help='类别: 1=建设工程材料, 2=园林绿化苗木, 3=区县材料')
args = parser.parse_args()

tab_name = TAB_TYPES.get(args.type, '未知')
print(f"[i] 类别: {tab_name} ({args.type})")

# 显示元数据
try:
    meta = get_metadata()
    print(f"[i] 当前期数: {meta.get('periods', '未知')}")
    print(f"[i] 可用类别: {[t.get('name','') for t in meta.get('tabs', [])]}")
except Exception as e:
    print(f"[!] 获取元数据失败: {e}")

# 获取数据（只抓取前 N 页）
session = BrowserSession(tab_type=args.type)
data = session.get_data(max_pages=args.pages)

rows = data.get('rows', [])
total = data.get('totalCount', 0)
periods = data.get('periods', '')
page_size = data.get('pageSize', 10)
total_pages = (total + page_size - 1) // page_size if total > 0 else 1

print(f"\n[i] 总记录: {total}, 期数: {periods}")
print(f"[i] 每页 {page_size} 条，共约 {total_pages} 页")
print(f"\n前 {min(args.pages * page_size, len(rows))} 条预览:")

shown = 0
for row in rows:
    if shown >= args.pages * page_size:
        break
    print(f"  {row.get('clmc')} | {row.get('ggxh')} | {row.get('dw')} | {row.get('price')}")
    shown += 1
