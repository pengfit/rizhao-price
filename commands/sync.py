"""日照工程造价信息 - 同步主程序"""
import sys, os, re, hashlib, json, time, signal, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
import requests
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')
from commands.utils import (
    SiteSession, parse_page, ensure_index, ensure_progress_index, load_config,
    TAB_TYPES, AREA_CODES,
)

interrupted = False


def _signal_handler(signum, frame):
    global interrupted
    interrupted = True
    print("\n[!] 接收到中断信号，正在保存进度...")


def _print_page(page, total_pages, docs_written, dry_run):
    pct = page / total_pages * 100 if total_pages else 0
    done = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    status = f"✓{docs_written}" if not dry_run else f"预览{docs_written}"
    sys.stdout.write(f"\r  [页 {page}/{total_pages}] {status} |{done}| {pct:.0f}%   ")
    sys.stdout.flush()


def _doc_id_key(breed, spec, unit, period, price, city, county):
    raw = f"{breed}_{spec}_{unit}_{period}_{price}_{city}_{county}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _make_doc(row: Dict, city: str, county: str, period: str) -> Dict:
    """从一行数据构造 ES 文档"""
    price_val = 0.0
    price_str = str(row.get('price') or row.get('price', ''))
    try:
        price_val = float(re.sub(r'[￥,，元\-\s]', '', price_str))
    except Exception:
        pass

    doc_id = _doc_id_key(
        row.get('clmc', ''), row.get('ggxh', ''),
        row.get('dw', ''), period, str(price_val),
        city, county
    )
    update_date = period + '-01' if period else datetime.now().strftime('%Y-%m-%d')
    return {
        '_id': doc_id,
        'breed': row.get('clmc', ''),
        'spec': row.get('ggxh', ''),
        'unit': row.get('dw', ''),
        'price': price_val,
        'period': period,
        'province': '山东省',
        'city': city,
        'county': county,
        'update_date': update_date,
        'create_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def _write_docs(es_host: str, es_index: str, docs: list, dry_run: bool) -> int:
    """批量写入 ES，返回写入数量"""
    if not docs:
        return 0
    if dry_run:
        return len(docs)
    bulk = ''
    for doc in docs:
        doc_id = doc.pop('_id')
        bulk += json.dumps({"index": {"_index": es_index, "_id": doc_id}}, ensure_ascii=False) + '\n'
        bulk += json.dumps(doc, ensure_ascii=False) + '\n'
    try:
        resp = requests.post(
            f"{es_host}/_bulk",
            data=bulk.encode('utf-8'),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=30, verify=False
        )
        if resp.status_code in (200, 201):
            items = resp.json().get('items', [])
            return sum(1 for it in items if it.get('index', {}).get('result') in ('created', 'updated'))
    except Exception:
        pass
    return 0


class ProgressLogger:
    """同步进度写入 ES"""
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_INTERRUPTED = 'interrupted'
    STATUS_ERROR = 'error'

    def __init__(self, es_host: str, index_name: str):
        self.es_host = es_host
        self.index = index_name
        self.run_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.state = {
            "run_id": self.run_id,
            "status": self.STATUS_RUNNING,
            "tab_type": "",
            "tab_name": "",
            "period": "",
            "current_page": 0,
            "total_pages": 0,
            "docs_written": 0,
            "percent": 0.0,
            "duration_sec": 0.0,
            "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "error": "",
        }
        ensure_progress_index(es_host, index_name)
        self._upsert()

    def _upsert(self):
        doc = dict(self.state)
        doc_id = f"{self.run_id}_{doc.get('tab_type', 'unknown')}"
        try:
            requests.post(
                f"{self.es_host}/{self.index}/_doc/{doc_id}",
                json=doc, timeout=15, verify=False)
        except Exception:
            pass

    def set_status(self, status: str):
        self.state["status"] = status
        self._upsert()

    def page_progress(self, page: int, total: int, docs: int, elapsed: float):
        self.state.update(
            current_page=page,
            total_pages=total,
            docs_written=docs,
            percent=round(page / total * 100, 2) if total else 0,
            duration_sec=round(elapsed, 2),
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        self._upsert()

    def set_tab_period(self, tab_type: str, tab_name: str, period: str):
        self.state.update(tab_type=tab_type, tab_name=tab_name, period=period)
        self._upsert()

    def finish_tab(self, page: int, total: int, docs: int):
        self.state.update(
            status=self.STATUS_COMPLETED,
            current_page=page,
            total_pages=total,
            docs_written=docs,
            percent=100.0,
        )
        self._upsert()


class ProgressStore:
    """本地进度存储"""
    def __init__(self):
        self.path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            '.rizhao_sync_progress.json'
        )
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save(self, tab_type: str, period: str, page: int):
        self.data['tab_type'] = tab_type
        self.data['period'] = period
        self.data['page'] = page
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False)

    def get(self) -> Tuple[str, str, int]:
        return (
            self.data.get('tab_type', ''),
            self.data.get('period', ''),
            self.data.get('page', 1)
        )

    def clear(self):
        self.data = {}
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({}, f)


def get_latest_period(session: SiteSession) -> str:
    """从第一页数据中获取当前期数"""
    data = session.get_release_price('1', 1, 10)
    if data:
        _, _, periods, _ = parse_page(data)
        if periods:
            return periods
    return datetime.now().strftime('%Y-%m')


def main():
    global interrupted
    parser = argparse.ArgumentParser(description='日照工程造价信息同步')
    parser.add_argument('--reset', action='store_true', help='重置进度，重新开始')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不写入 ES')
    parser.add_argument('--force', action='store_true', help='强制全量同步')
    parser.add_argument('--type', default='1', help='类别: 1=建设工程材料,2=园林绿化苗木,3=区县材料')
    parser.add_argument('--max-pages', type=int, default=2000, help='最大页数')
    parser.add_argument('--no-check', action='store_true', help='跳过增量检测')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = load_config(os.path.join(script_dir, 'config.yml'))
    es_host = config.get('es', {}).get('host', 'http://localhost:59200')
    es_index = config.get('es', {}).get('index', 'material_rizhao_price')
    progress_index = config.get('es', {}).get('sync_progress_index', 'material_rizhao_price_sync_progress')

    signal.signal(signal.SIGINT, _signal_handler)

    if not args.dry_run:
        ensure_index(es_host, es_index)

    session = SiteSession()

    # 获取最新期数
    period_name = get_latest_period(session)
    print(f"[i] 当前期数: {period_name}")

    # 增量检测
    if not args.no_check:
        cfg = load_config(CONFIG_PATH)
        last_period = cfg.get('sync', {}).get('last_period', '') or ''
        if last_period == period_name and not args.force:
            print(f"[—] 上次已同步至 {period_name}，无新数据。如需强制同步，加 --force")
            return
        elif last_period and last_period > period_name:
            print(f"[!] config 中记录的期数 {last_period} 晚于目标期数 {period_name}")
            return
        else:
            print(f"[i] 增量检测通过: {last_period or '(首次)'} → {period_name}")

    progress = ProgressStore()
    if args.reset:
        print("[i] 重置进度...")
        progress.clear()

    saved_type, saved_period, saved_page = progress.get()

    logger = ProgressLogger(es_host, progress_index)
    tab_name = TAB_TYPES.get(args.type, '未知')
    logger.set_tab_period(args.type, tab_name, period_name)

    start_time = time.time()
    total_docs = 0

    # 确定起始页码（断点续传）
    start_page = saved_page if (saved_type == args.type and saved_period == period_name) else 1
    if start_page > 1:
        print(f"[i] 续传：从第 {start_page} 页开始")

    # 获取总记录数以确定页数
    data = session.get_release_price(args.type, 1, 10)
    if not data:
        print("[!] 无法获取数据，请检查网络连接")
        return

    rows, total_records, _, _ = parse_page(data)
    page_size = 10
    total_pages = (total_records + page_size - 1) // page_size if total_records > 0 else 1
    print(f"[i] 类别: {tab_name}，总记录: {total_records}，总页数: {total_pages}")

    page_docs = 0
    for page in range(start_page, min(total_pages + 1, args.max_pages + 1)):
        if interrupted:
            print(f"\n  [!] 页 {page} 中断，已保存进度")
            logger.set_status(ProgressLogger.STATUS_INTERRUPTED)
            progress.save(args.type, period_name, page)
            return

        data = session.get_release_price(args.type, page, page_size)
        if not data:
            print(f"\n  [!] 页 {page} 获取数据失败")
            break

        rows, _, _, _ = parse_page(data)
        # 城市/区县：统一用日照市（tabType=1/2为全市；tabType=3区县材料按 county 列区分）
        docs = []
        for row in rows:
            if not row.get('clmc'):
                continue
            if args.type == '3':
                # tabType=3 区县材料，按 county 字段区分
                county = row.get('county', '日照市')
                docs.append(_make_doc(row, '日照市', county, period_name))
            else:
                docs.append(_make_doc(row, '日照市', '日照市', period_name))

        written = _write_docs(es_host, es_index, docs, args.dry_run)
        _print_page(page, total_pages, written, args.dry_run)
        page_docs += written
        total_docs += written

        if not args.dry_run:
            progress.save(args.type, period_name, page)
            elapsed = time.time() - start_time
            logger.page_progress(page, total_pages, total_docs, elapsed)

        time.sleep(0.8)

    logger.finish_tab(total_pages, total_pages, page_docs)

    if not interrupted:
        elapsed = time.time() - start_time
        print(f"\n\n[✓] 完成，类别 {tab_name}，共写入 {total_docs} 条文档")
        print(f"[i] 耗时: {elapsed:.1f}s")

        # 更新 config.yml
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault('sync', {})['last_period'] = period_name
        with open(cfg_path, 'w') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print(f"[i] 已更新 last_period: {period_name}")


if __name__ == '__main__':
    main()
