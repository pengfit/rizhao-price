"""日照工程造价信息 - 同步主程序（流式版）"""
import sys, os, re, hashlib, json, time, signal, argparse, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings('ignore')
import requests
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')
from commands.utils import (
    ensure_index, ensure_progress_index, load_config,
    TAB_TYPES, doc_id,
)

interrupted = False


def _signal_handler(signum, frame):
    global interrupted
    interrupted = True


def _print_progress(page, total_pages, written, failed, dry_run):
    pct = page / total_pages * 100 if total_pages else 0
    done = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    ok_mark = "✓" if failed == 0 else "✗"
    status = f"{ok_mark}{written}/{failed}" if not dry_run else f"预览{written}"
    sys.stdout.write(f"\r  [页 {page}/{total_pages}] {status} |{done}| {pct:.0f}%   ")
    sys.stdout.flush()


def _make_doc(row: dict, city: str, county: str, period: str) -> dict:
    price_str = str(row.get('price') or '')
    try:
        price_val = float(re.sub(r'[￥,，元\-\s]', '', price_str))
    except Exception:
        price_val = 0.0
    did = doc_id(row.get('clmc', ''), row.get('ggxh', ''), row.get('dw', ''), period, price_val, city, county)
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


def _bulk_write_with_retry(es_host: str, es_index: str, docs: list, dry_run: bool, max_retries: int = 3) -> dict:
    if not docs:
        return {'written': 0, 'failed': 0, 'errors': []}
    if dry_run:
        return {'written': len(docs), 'failed': 0, 'errors': []}
    bulk = ''
    for doc in docs:
        doc_id_val = doc.pop('_id')
        bulk += json.dumps({"index": {"_index": es_index, "_id": doc_id_val}}, ensure_ascii=False) + '\n'
        bulk += json.dumps(doc, ensure_ascii=False) + '\n'
    last_error = ''
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{es_host}/_bulk", data=bulk.encode('utf-8'),
                headers={"Content-Type": "application/x-ndjson"}, timeout=60, verify=False
            )
            if resp.status_code in (200, 201):
                items = resp.json().get('items', [])
                written = sum(1 for it in items if it.get('index', {}).get('result') in ('created', 'updated'))
                errors = []
                failed = 0
                for it in items:
                    err = it.get('index', {}).get('error', {})
                    if err:
                        failed += 1
                        errors.append(f"{it['index'].get('_id')}: {err.get('reason', str(err))}")
                return {'written': written, 'failed': failed, 'errors': errors}
            else:
                last_error = f"HTTP {resp.status_code}"
        except requests.exceptions.Timeout:
            last_error = f"超时 (attempt {attempt + 1}/{max_retries})"
        except requests.exceptions.ConnectionError as e:
            last_error = f"连接错误: {e}"
        except Exception as e:
            last_error = f"未知错误: {e}"
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return {'written': 0, 'failed': len(docs), 'errors': [f"重试耗尽: {last_error}"]}


class ProgressLogger:
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_INTERRUPTED = 'interrupted'
    STATUS_ERROR = 'error'

    def __init__(self, es_host: str, index_name: str):
        self.es_host = es_host
        self.index = index_name
        self.run_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.state = {
            "run_id": self.run_id, "status": self.STATUS_RUNNING,
            "tab_type": "", "tab_name": "", "period": "",
            "current_page": 0, "total_pages": 0, "total_count": 0,
            "docs_written": 0, "docs_failed": 0, "pages_completed": 0,
            "percent": 0.0, "duration_sec": 0.0,
            "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "error": "", "page_errors": [],
        }
        ensure_progress_index(es_host, index_name)
        try:
            requests.delete(f"{self.es_host}/{self.index}/_doc/{self.run_id}", timeout=10, verify=False)
        except Exception:
            pass
        self._upsert()

    def _upsert(self):
        try:
            requests.post(
                f"{self.es_host}/{self.index}/_doc/{self.run_id}",
                json=dict(self.state), timeout=15, verify=False)
        except Exception:
            pass

    def set_status(self, status: str, error: str = ''):
        self.state["status"] = status
        if error:
            self.state["error"] = error
        self._upsert()

    def update(self, page: int, total_pages: int, total_count: int,
               docs_written: int, docs_failed: int, elapsed: float):
        self.state.update(
            current_page=page, total_pages=total_pages, total_count=total_count,
            docs_written=docs_written, docs_failed=docs_failed,
            pages_completed=page,
            percent=round(page / total_pages * 100, 2) if total_pages else 0,
            duration_sec=round(elapsed, 2),
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        self._upsert()

    def log_page_error(self, page: int, written: int, failed: int, errors: list):
        err = f"页 {page}: 成功{written} 失败{failed}"
        if errors:
            err += f" | {'; '.join(errors[:3])}"
        self.state["page_errors"].append(err)
        if len(self.state["page_errors"]) > 50:
            self.state["page_errors"] = self.state["page_errors"][-50:]

    def set_tab_period(self, tab_type: str, tab_name: str, period: str):
        self.state.update(tab_type=tab_type, tab_name=tab_name, period=period)
        self._upsert()

    def finish(self, docs_written: int, docs_failed: int, total_count: int, elapsed: float):
        self.state.update(
            status=self.STATUS_COMPLETED, docs_written=docs_written, docs_failed=docs_failed,
            percent=100.0, duration_sec=round(elapsed, 2),
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        self._upsert()


class ProgressStore:
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

    def save(self, tab_type: str, period: str, page: int, total_pages: int,
             docs_written: int = 0, docs_failed: int = 0):
        self.data['tab_type'] = tab_type
        self.data['period'] = period
        self.data['page'] = page
        self.data['total_pages'] = total_pages
        self.data['docs_written'] = docs_written
        self.data['docs_failed'] = docs_failed
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False)

    def get(self) -> tuple:
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


SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(SCRIPT_DIR, 'commands', 'fetch_data.js')


class StreamFetcher:
    """
    通过 subprocess.Popen 启动 node fetch_data.js stream，
    实时逐行解析 JSON Lines，实现边抓边写。
    """
    def __init__(self, tab_type: str, max_pages: int = 2000):
        self.tab_type = tab_type
        self.max_pages = max_pages
        self.proc = None
        self.reader_file = None

    def start(self):
        self.proc = subprocess.Popen(
            ['node', JS_PATH, 'stream', self.tab_type, str(self.max_pages)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=os.path.dirname(JS_PATH),
            env={**os.environ, 'PATH': os.environ.get('PATH', '')},
            bufsize=1  # 行缓冲
        )
        # 在 Unix 下获取管道的 fd
        self.reader_file = os.fdopen(self.proc.stdout.fileno(), 'r', buffering=1)

    def next_page(self) -> dict:
        """读取下一行 JSON，返回 page data 或 None（流结束）"""
        line = self.reader_file.readline()
        if not line:
            return None
        try:
            obj = json.loads(line.strip())
        except json.JSONDecodeError:
            return None
        if obj.get('done'):
            return None
        return obj

    def close(self):
        rf = getattr(self, 'reader_file', None)
        self.reader_file = None
        if rf is not None:
            try:
                rf.close()
            except Exception:
                pass
        proc = getattr(self, 'proc', None)
        if proc is not None:
            self.proc = None
            try:
                if proc.poll() is None:
                    proc.stdout.close()
                    proc.wait()
            except Exception:
                pass


def main():
    global interrupted
    parser = argparse.ArgumentParser(description='日照工程造价信息同步 (流式版)')
    parser.add_argument('--reset', action='store_true', help='重置进度')
    parser.add_argument('--dry-run', action='store_true', help='预览模式')
    parser.add_argument('--force', action='store_true', help='强制全量同步')
    parser.add_argument('--type', default='1', help='类别')
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

    # 获取期数
    print("[i] 获取源站元数据...")
    try:
        meta_proc = subprocess.run(
            ['node', JS_PATH, 'metadata'],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, 'PATH': os.environ.get('PATH', '')}
        )
        period_name = json.loads(meta_proc.stdout).get('periods', '')
    except Exception as e:
        print(f"[!] 获取元数据失败: {e}")
        period_name = datetime.now().strftime('%Y-%m')

    if not period_name:
        print("[!] 无法获取期数")
        return

    print(f"[i] 当前期数: {period_name}, 类别: {tab_name} ({args.type})")

    if not args.no_check:
        cfg = load_config(CONFIG_PATH)
        last_period = cfg.get('sync', {}).get('last_period', '') or ''
        if last_period == period_name and not args.force:
            print(f"[—] 上次已同步至 {period_name}，无新数据。加 --force 可强制同步")
            return
        elif last_period and last_period > period_name:
            print(f"[!] 期数异常: {last_period} > {period_name}")
            return
        else:
            print(f"[i] 增量检测通过: {last_period or '(首次)'} → {period_name}")

    progress = ProgressStore()
    if args.reset:
        print("[i] 重置进度...")
        progress.clear()

    saved_type, saved_period, saved_page, saved_total_pages = progress.get()
    if args.force:
        saved_page = 0

    logger = ProgressLogger(es_host, progress_index)
    logger.set_tab_period(args.type, tab_name, period_name)
    start_time = time.time()
    total_docs_written = 0
    total_docs_failed = 0

    # 启动流式抓取
    print(f"\n[▼] 启动流式抓取 (type={args.type})...")
    fetcher = StreamFetcher(args.type, args.max_pages)
    try:
        fetcher.start()
    except Exception as e:
        print(f"[!] 启动浏览器失败: {e}")
        logger.set_status(ProgressLogger.STATUS_ERROR, str(e))
        return

    # 读第1页获取元信息
    page1_data = fetcher.next_page()
    if not page1_data:
        print("[!] 无数据")
        fetcher.close()
        return

    total_count = page1_data.get('totalCount', 0)
    total_pages = page1_data.get('totalPages', 0)
    fetched_period = page1_data.get('periods', period_name)
    page_size = page1_data.get('pageSize', 10)

    print(f"[i] 共 {total_count} 条记录，约 {total_pages} 页，每页 {page_size} 条")

    logger.state["total_pages"] = total_pages
    logger.state["total_count"] = total_count
    logger._upsert()

    start_page = saved_page if (saved_type == args.type and saved_period == fetched_period and saved_page > 1) else 1
    if start_page > 1:
        print(f"[i] 续传：从第 {start_page} 页开始")

    # 处理第1页（已抓取）
    current_page = 1
    if start_page <= 1:
        rows = page1_data.get('rows', [])
        docs = [_make_doc(r, '日照市', '日照市', fetched_period) for r in rows if r.get('clmc')]
        if docs:
            result = _bulk_write_with_retry(es_host, es_index, docs, args.dry_run, max_retries=3)
            total_docs_written += result['written']
            total_docs_failed += result['failed']
            _print_progress(1, total_pages, result['written'], result['failed'], args.dry_run)
            if result['failed'] > 0:
                print(f"\n  [!] 第1页: 写入成功{result['written']} 失败{result['failed']}")
                logger.log_page_error(1, result['written'], result['failed'], result['errors'])
            if not args.dry_run:
                elapsed = time.time() - start_time
                logger.update(1, total_pages, total_count, total_docs_written, total_docs_failed, elapsed)
                progress.save(args.type, fetched_period, 1, total_pages, total_docs_written, total_docs_failed)

    # 流式读取后续页面
    current_page = 2
    while True:
        if interrupted:
            print(f"\n[!] 第 {current_page} 页中断，已保存进度")
            logger.set_status(ProgressLogger.STATUS_INTERRUPTED)
            progress.save(args.type, fetched_period, current_page, total_pages,
                         total_docs_written, total_docs_failed)
            fetcher.close()
            return

        page_data = fetcher.next_page()
        if page_data is None:
            break

        page_num = page_data.get('page', current_page)
        rows = page_data.get('rows', [])
        docs = [_make_doc(r, '日照市', '日照市', fetched_period) for r in rows if r.get('clmc')]

        if docs:
            result = _bulk_write_with_retry(es_host, es_index, docs, args.dry_run, max_retries=3)
            total_docs_written += result['written']
            total_docs_failed += result['failed']
            _print_progress(page_num, total_pages, result['written'], result['failed'], args.dry_run)
            if result['failed'] > 0:
                print(f"\n  [!] 第{page_num}页: 成功{result['written']} 失败{result['failed']}")
                logger.log_page_error(page_num, result['written'], result['failed'], result['errors'])
            if not args.dry_run:
                elapsed = time.time() - start_time
                logger.update(page_num, total_pages, total_count, total_docs_written, total_docs_failed, elapsed)
                progress.save(args.type, fetched_period, page_num, total_pages,
                             total_docs_written, total_docs_failed)
        else:
            _print_progress(page_num, total_pages, 0, 0, args.dry_run)

        current_page = page_num + 1

    fetcher.close()

    print(f"\n\n[✓] 类别 {tab_name} 完成")

    missing = total_count - (total_docs_written + total_docs_failed)
    if missing > 0:
        print(f"[!] 数据遗漏: 源站{total_count}条 / 入库{total_docs_written+total_docs_failed}条，差距{missing}条")
    if total_docs_failed > 0:
        print(f"[!] 写入失败: {total_docs_failed} 条")
    if total_docs_failed == 0 and missing == 0:
        print(f"[✓] 写入成功: {total_docs_written} 条")

    print(f"[i] 抓取汇总: 源站{total_count}条 → 写入{total_docs_written}条 (失败{total_docs_failed}条)")

    if not interrupted:
        elapsed = time.time() - start_time
        print(f"[i] 耗时: {elapsed:.1f}s")
        logger.finish(total_docs_written, total_docs_failed, total_count, elapsed)
        logger.set_status(ProgressLogger.STATUS_COMPLETED)

        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault('sync', {})['last_period'] = fetched_period
        with open(CONFIG_PATH, 'w') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print(f"[i] 已更新 last_period: {fetched_period}")


if __name__ == '__main__':
    main()
