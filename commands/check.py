"""日照工程造价材料信息 - 增量检测与触发同步"""
import sys, os, yaml, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from commands.utils import TAB_TYPES, load_config

ES_HOST = 'http://localhost:59200'
JS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'commands', 'fetch_data.js')


def get_website_counts():
    """通过 Playwright 获取各 tab 的 totalCount"""
    counts = {}
    for tab_type, tab_name in TAB_TYPES.items():
        try:
            proc = subprocess.run(
                ['node', JS_PATH, 'paginate', tab_type, '1'],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, 'PATH': os.environ.get('PATH', '')}
            )
            first_line = proc.stdout.strip().split('\n')[0]
            data = json.loads(first_line)
            counts[tab_type] = {
                'tab_name': tab_name,
                'total': data.get('totalCount', 0),
                'period': data.get('periods', ''),
            }
        except Exception:
            counts[tab_type] = {'tab_name': tab_name, 'total': 0, 'period': ''}
    return counts


def get_es_counts(es_host, es_index):
    """从 ES 查询各 tab 的文档数"""
    counts = {}
    for tab_type, tab_name in TAB_TYPES.items():
        try:
            r = requests.post(
                f'{es_host}/{es_index}/_count',
                json={'query': {'bool': {'must': [
                    {'term': {'tab_type': tab_type}},
                    {'term': {'period': _get_current_period()}}
                ]}}},
                timeout=15, verify=False
            )
            counts[tab_type] = {
                'tab_name': tab_name,
                'es_count': r.json().get('count', 0),
            }
        except Exception:
            counts[tab_type] = {'tab_name': tab_name, 'es_count': 0}
    return counts


def _get_current_period():
    """获取网站当前期数"""
    try:
        proc = subprocess.run(
            ['node', JS_PATH, 'metadata'],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, 'PATH': os.environ.get('PATH', '')}
        )
        data = json.loads(proc.stdout)
        return data.get('periods', '')
    except Exception:
        return ''


def main():
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(script_dir, 'config.yml')
    cfg = load_config(cfg_path)

    print('[i] 增量检测开始...')

    # 网站各 tab 总数
    web_counts = get_website_counts()
    current_period = web_counts.get('1', {}).get('period', '') or _get_current_period()
    saved_period = cfg.get('sync', {}).get('last_period', '')

    print(f'[i] 网站当前期数: {current_period}')

    # 情况1：新周期出现
    if current_period and saved_period and current_period != saved_period:
        print(f'[i] 新周期出现: {current_period} (上次: {saved_period})')
        print('[→] 触发全量同步...')
        os.system(f'cd {script_dir} && python3 commands/sync.py --force')
        return

    # 情况2：同周期，检测各 tab 是否有新增
    print(f'[i] 当前周期无变化: {current_period}，检测各 tab 增量...')

    es_host = cfg.get('es', {}).get('host', 'http://localhost:59200')
    es_index = cfg.get('es', {}).get('index', 'material_rizhao_price')

    changed = []
    for tab_type, info in web_counts.items():
        web_total = info.get('total', 0)
        # ES 中该 tab 该周期的记录数
        try:
            r = requests.post(
                f'{es_host}/{es_index}/_count',
                json={
                    'query': {
                        'bool': {
                            'must': [
                                {'term': {'tab_type': tab_type}},
                                {'term': {'period': current_period}} if current_period else {'match_all': {}}
                            ]
                        }
                    }
                },
                timeout=15, verify=False
            )
            es_count = r.json().get('count', 0)
        except Exception:
            es_count = 0

        diff = web_total - es_count
        if diff > 0:
            changed.append({
                'tab_type': tab_type,
                'tab_name': info['tab_name'],
                'web_total': web_total,
                'es_count': es_count,
                'diff': diff,
            })

    if not changed:
        print('[—] 无新增记录')
        return

    print(f'[i] 发现 {len(changed)} 个 tab 有新数据:')
    for c in changed:
        print(f'  {c["tab_name"]}: 网站 {c["web_total"]} > ES {c["es_count"]}  (+{c["diff"]})')

    # 增量同步：跑 sync.py，幂等写入会自动补漏
    print('[→] 触发增量同步...')
    os.system(f'cd {script_dir} && python3 commands/sync.py --force')


if __name__ == '__main__':
    main()
