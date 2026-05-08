"""日照工程造价信息 - SiteSession 和解析函数"""
import sys, os, re, yaml, warnings, requests
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from bs4 import BeautifulSoup

warnings.filterwarnings('ignore')

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Content-Type': 'application/json',
    'Origin': 'http://58.59.43.227:81',
    'Referer': 'http://58.59.43.227:81/dist/',
}

# 日照市辖区县
AREA_CODES = {
    '001001': '日照市辖区',
    '001002': '东港区',
    '001003': '岚山区',
    '001004': '五莲县',
    '001005': '莒县',
}

# 三个类别
TAB_TYPES = {
    '1': '建设工程材料',
    '2': '园林绿化苗木',
    '3': '区县建设工程材料',
}


class SiteSession:
    """日照 EpointSDRZ 站点 Session"""

    def __init__(self, max_retries: int = 5, timeout: int = 60):
        self.base_url = 'http://58.59.43.227:81/EpointSDRZ'
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        # 获取初始 sid cookie
        self._fetch_init()

    def _fetch_init(self):
        """获取初始 session cookie（GET 主页面产生 302 -> sid cookie）"""
        try:
            self.session.get(
                f"{self.base_url}/rest/zjzmaterialpriceserver/gettabcolumn",
                json={"body": {}},
                timeout=self.timeout,
                verify=False,
                allow_redirects=True
            )
            # 强制 GET 访问主页以获取 sid cookie
            self.session.get(
                f"{self.base_url}/frame/pages/index/priceDissemination",
                timeout=self.timeout,
                verify=False,
                allow_redirects=False
            )
        except Exception:
            pass

    def _do_post(self, endpoint: str, data: Dict) -> Optional[Dict]:
        """POST JSON 到指定端点"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        for attempt in range(self.max_retries):
            try:
                resp = self.session.post(
                    url, json=data, timeout=self.timeout, verify=False
                )
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except Exception:
                        pass
            except Exception:
                if attempt < self.max_retries - 1:
                    import time; time.sleep(2 * (attempt + 1))
        return None

    def get_tabs(self) -> Tuple[List[Dict], List[List[str]]]:
        """获取三个类别 tab 及树表头"""
        data = self._do_post(
            'rest/zjzmaterialpriceserver/gettabcolumn',
            {"body": {}}
        )
        if not data:
            return [], []
        custom = data.get('custom', {})
        arr = custom.get('data', {})
        tab_list = arr.get('data', [])
        tree_head_list = arr.get('treeHeadList', [])
        return tab_list, tree_head_list

    def get_left_column(self, tab_type: str) -> List[Dict]:
        """获取材料分类树"""
        data = self._do_post(
            'rest/zjzmaterialpriceserver/getleftcolumn',
            {"body": {"tabType": tab_type}}
        )
        if not data:
            return []
        custom = data.get('custom', {})
        arr = custom.get('data', {})
        return arr if isinstance(arr, list) else arr.get('data', [])

    def get_release_price(self, tab_type: str, page_index: int, page_size: int,
                          material_id: str = '', condition: str = '', periods: str = '') -> Optional[Dict]:
        """获取价格列表数据"""
        data = self._do_post(
            'rest/zjzmaterialpriceserver/getreleaseprice',
            {
                "body": {
                    "pageIndex": page_index,
                    "pageSize": page_size,
                    "tabType": tab_type,
                    "id": material_id,
                    "condition": condition,
                    "periods": periods,
                }
            }
        )
        return data

    def get_material_description(self) -> str:
        """获取材料价格说明"""
        data = self._do_post(
            'rest/zjzmaterialpriceserver/getmaterialdescription',
            {"body": {}}
        )
        if not data:
            return ''
        custom = data.get('custom', {})
        return custom.get('data', {}).get('explain', '')


def parse_page(data: Dict) -> Tuple[List[Dict], int, int]:
    """
    解析 getreleaseprice 返回的 JSON 数据。
    返回: (rows: List[Dict], total: int, page_size: int)

    价格列结构（动态，根据 isFirst/isSecond/isThird）：
    - isFirst=true: price 列（单一价格）
    - isSecond=true: price(上半月) + secondPrice(下半月)
    - isThird=true: price(上旬) + secondPrice(中旬) + thirdPrice(下旬)
    - tax=true: 还有税率列
    """
    custom = data.get('custom', {})
    result_data = custom.get('data', {})

    rows = result_data.get('data', [])
    total = result_data.get('count', 0)
    periods = result_data.get('periods', '')
    remark = result_data.get('remark', '')

    return rows, total, periods, remark


def ensure_index(es_host: str, es_index: str):
    """确保 ES 索引存在"""
    try:
        resp = requests.head(f"{es_host}/{es_index}", timeout=10, verify=False)
        if resp.status_code == 200:
            return
    except Exception:
        pass
    mapping = {
        "mappings": {
            "properties": {
                "breed":       {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
                "spec":        {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
                "unit":        {"type": "keyword"},
                "price":       {"type": "float"},
                "period":      {"type": "keyword"},
                "province":    {"type": "keyword"},
                "city":        {"type": "keyword"},
                "county":      {"type": "keyword"},
                "update_date": {"type": "date", "format": "yyyy-MM-dd"},
                "create_time": {"type": "date", "format": "yyyy-MM-dd HH:mm:ss||yyyy-MM-dd||strict_date_optional_time"}
            }
        }
    }
    requests.put(f"{es_host}/{es_index}", json=mapping, timeout=30, verify=False)


def ensure_progress_index(es_host: str, idx: str):
    """确保同步进度索引存在"""
    mapping = {
        "mappings": {"properties": {
            "run_id": {"type": "keyword"},
            "status": {"type": "keyword"},
            "tab_type": {"type": "keyword"},
            "tab_name": {"type": "keyword"},
            "period": {"type": "keyword"},
            "current_page": {"type": "integer"},
            "total_pages": {"type": "integer"},
            "docs_written": {"type": "integer"},
            "percent": {"type": "float"},
            "duration_sec": {"type": "float"},
            "last_updated": {"type": "date", "format": "yyyy-MM-dd HH:mm:ss"},
            "error": {"type": "text"},
        }}
    }
    try:
        resp = requests.head(f"{es_host}/{idx}", timeout=10, verify=False)
        if resp.status_code == 200:
            return
    except Exception:
        pass
    requests.put(f"{es_host}/{idx}", json=mapping, timeout=30, verify=False)


def load_config(path: str) -> Dict[str, Any]:
    """加载 YAML 配置"""
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}
