"""日照工程造价信息 - 同步主程序（Playwright 版）"""
import sys, os, re, hashlib, json, time, signal, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
import requests
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')
from commands.utils import (
    BrowserSession, get_metadata, ensure_index, ensure_progress_index, load_config,
    TAB_TYPES, doc_id,
)

interrupted = False


def _signal_handler(signum, frame):
    global interrupted
    interrupted = True
    print("\n[!] 接收到中断信号，正在保存进度...")


def _print_progress(page, total_pages, docs_written, dry_run):
    pct = page / total_pages * 100 if total_pages else 0
    done = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    status = f"✓{docs_written}" if not dry_run else f"预览{docs_written}"
    sys.stdout.write(f"\r  [页 {page}/{total_pages}] {status} |{done}| {pct:.0f}%   ")
    sys.stdout.flush()


def _make_doc(row: Dict, city: str, county: str, period: str) -> Dict:
    """从一行数据构造 ES 文档"""
    price_str = str(row.get('price') or '')
    try:
        price_val = float(re.sub(r'[￥,，元\-\s]', '', price_str))
    except Exception:
        price_val = 0.0

    did = doc_id(
        row.get('clmc', ''), row.get('ggxh', ''),
        row.get('dw', ''), period, price_val,
        city, county
    )
    update_date = period + '-01' if period else datetime.now().strftime('%Y-%m-%d')
    return {
        '_id': did,
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
    """批量写入 ES"""
    if not docs:
        return 0
    if dry_run:
        return len(docs)
    bulk = ''
    for doc in docs:
        doc_id_val = doc.pop('_id')
        bulk += json.dumps({"index": {"_index": es_index, "_id": doc_id_val}}, ensure_ascii=False) + '\n'
        bulk += json.dumps(doc, ensure_ascii=False) + '\n'
    try:
        resp = requests.post(
            f"{es_host}/_bulk",
            data=bulk.encode('utf-8'),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=60, verify=False
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
            "total_count": 0,
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
        doc_id_val = f"{self.run_id}_{doc.get('tab_type', 'unknown')}"
        try:
            requests.post(
                f"{self.es_host}/{self.index}/_doc/{doc_id_val}",
                json=doc, timeout=15, verify=False)
        except Exception:
            pass

    def set_status(self, status: str, error: str = ''):
        self.state["status"] = status
        if error:
            self.state["error"] = error
        self._upsert()

    def update(self, page: int, total_pages: int, total_count: int, docs: int, elapsed: float):
        self.state.update(
            current_page=page,
            total_pages=total_pages,
            total_count=total_count,
            docs_written=docs,
            percent=round(page / total_pages * 100, 2) if total_pages else 0,
            duration_sec=round(elapsed, 2),
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        self._upsert()

    def set_tab_period(self, tab_type: str, tab_name: str, period: str):
        self.state.update(tab_type=tab_type, tab_name=tab_name, period=period)
        self._upsert()

    def finish(self, docs: int):
        self.state.update(
            status=self.STATUS_COMPLETED,
            docs_written=docs,
            percent=100.0,
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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

    def save(self, tab_type: str, period: str, page: int, total_pages: int):
        self.data['tab_type'] = tab_type
        self.data['period'] = period
        self.data['page'] = page
        self.data['total_pages'] = total_pages
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False)

    def get(self) -> Tuple[str, str, int, int]:
        return (
            self.data.get('tab_type', ''),
            self.data.get('period', ''),
            self.data.get('page', 1),
            self.data.get('total_pages', 0),
        )

    def clear(self):
        self.data = {}
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({}, f)


def main():
    global interrupted
    parser = argparse.ArgumentParser(description='日照工程造价信息同步 (Playwright)')
    parser.add_argument('--reset', action='store_true', help='重置进度，重新开始')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不写入 ES')
    parser.add_argument('--force', action='store_true', help='强制全量同步')
    parser.add_argument('--type', default='1', help='类别: 1=建设工程材料, 2=园林绿化苗木, 3=区县材料')
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

    tab_name = TAB_TYPES.get(args.type, '未知')

    # 获取当前期数
    print("[i] 获取源站元数据...")
    try:
        meta = get_metadata()
        period_name = meta.get('periods', '')
    except Exception as e:
        print(f"[!] 获取元数据失败: {e}")
        period_name = datetime.now().strftime('%Y-%m')

    if not period_name:
        print("[!] 无法获取期数")
        return

    print(f"[i] 当前期数: {period_name}, 类别: {tab_name} ({args.type})")

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

    saved_type, saved_period, saved_page, saved_total_pages = progress.get()

    logger = ProgressLogger(es_host, progress_index)
    logger.set_tab_period(args.type, tab_name, period_name)
    start_time = time.time()
    total_docs = 0

    # 确定起始页
    start_page = saved_page if (saved_type == args.type and saved_period == period_name and saved_page > 1) else 1
    if start_page > 1:
        print(f"[i] 续传：从第 {start_page} 页开始")

    # 通过 Playwright 获取全量数据（一次性抓取，由 JS 负责翻页）
    print(f"\n[▼] 通过浏览器抓取数据 (type={args.type})...")
    try:
        data = BrowserSession(tab_type=args.type).get_data(max_pages=args.max_pages)
    except Exception as e:
        print(f"[!] 抓取失败: {e}")
        logger.set_status(ProgressLogger.STATUS_ERROR, str(e))
        return

    rows, total_count, fetched_period = data.get('rows', []), data.get('totalCount', 0), data.get('periods', period_name)
    page_size = data.get('pageSize', 10)
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

    print(f"[i] 共 {total_count} 条记录，约 {total_pages} 页，每页 {page_size} 条")

    if total_count == 0 or not rows:
        print("[!] 无数据可同步")
        return

    # 按页分组处理
    docs = []
    page_docs = 0
    current_page = 1

    for i, row in enumerate(rows):
        if interrupted:
            print(f"\n  [!] 第 {current_page} 页中断，已保存进度")
            logger.set_status(ProgressLogger.STATUS_INTERRUPTED)
            progress.save(args.type, fetched_period, current_page, total_pages)
            return

        row_page = i // page_size + 1

        if row_page > current_page or i == 0:
            # 换页：写入上一页的 docs
            if docs:
                written = _write_docs(es_host, es_index, docs, args.dry_run)
                _print_progress(current_page, total_pages, written, args.dry_run)
                page_docs += written
                total_docs += written

                if not args.dry_run:
                    elapsed = time.time() - start_time
                    logger.update(current_page, total_pages, total_count, total_docs, elapsed)
                    progress.save(args.type, fetched_period, current_page, total_pages)

            docs = []
            current_page = row_page

        if row_page < start_page:
            continue

        if not row.get('clmc'):
            continue

        # 区县材料(tabType=3)需要从数据中区分区县
        if args.type == '3':
            county = row.get('county', '日照市')
        else:
            county = '日照市'

        docs.append(_make_doc(row, '日照市', county, fetched_period))

    # 写入最后一页
    if docs:
        if not interrupted:
            written = _write_docs(es_host, es_index, docs, args.dry_run)
            _print_progress(current_page, total_pages, written, args.dry_run)
            page_docs += written
            total_docs += written

            if not args.dry_run:
                elapsed = time.time() - start_time
                logger.update(current_page, total_pages, total_count, total_docs, elapsed)
                progress.save(args.type, fetched_period, current_page, total_pages)

    print(f"\n\n[✓] 类别 {tab_name} 完成，共写入 {total_docs} 条文档")

    if not interrupted:
        elapsed = time.time() - start_time
        print(f"[i] 耗时: {elapsed:.1f}s")
        logger.finish(total_docs)
        logger.set_status(ProgressLogger.STATUS_COMPLETED)

        # 更新 config.yml
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault('sync', {})['last_period'] = fetched_period
        with open(CONFIG_PATH, 'w') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print(f"[i] 已更新 last_period: {fetched_period}")


if __name__ == '__main__':
    main()
