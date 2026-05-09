---
name: rizhao-price
description: 日照工程造价材料信息采集。从 58.59.43.227:81 抓取日照市材料价格数据，支持多tab、增量同步、断点续传。
---

# 日照工程造价材料信息采集

从 http://58.59.43.227:81/dist/#/index/priceDissemination 抓取日照市材料价格数据，支持增量同步、断点续传。

---

## 命令

```bash
cd ~/.openclaw/workspace/skills/rizhao-price

./run.sh preview              # 预览（不写入 ES）
./run.sh preview --pages 3    # 预览前 3 页
./run.sh preview --type 1     # 指定类别（1=建设工程材料,2=园林绿化苗木,3=区县材料）
./run.sh sync                 # 增量同步到 ES
./run.sh sync --dry-run       # 预览同步（不写入）
./run.sh sync --reset         # 重置进度，从头开始
./run.sh sync --force         # 强制全量同步（跳过增量检测，会覆盖已有数据）
./run.sh sync --type 1        # 指定类别同步
./run.sh sync --max-pages 5  # 限制最大页数
./run.sh sync --no-check      # 跳过增量检测，直接同步
./run.sh status               # 查看同步状态
./run.sh test                 # 测试 ES 连接和源站
./run.sh check                # 检查源站是否有新数据
```

> 三个类别需分别执行 sync：type 1（建设工程材料，109 页/1083 条）、type 2（园林绿化苗木，1 页/7 条）、type 3（区县材料，6 页/60 条）。

---

## 技术方案

**采用 Playwright 浏览器自动化 + 流式输出模式**：

- 目标站点为 Vue SPA，所有数据通过 JS 动态渲染
- `fetch_data.js` 提供 3 种模式：
  - `metadata`：获取 tabs 和当前期数
  - `paginate <type> <page>`：抓取单页（兼容旧调用）
  - `stream <type> <maxPages>`：**推荐** — 单次浏览器启动，连续翻页抓取，每页抓完立即输出 JSON Lines，subprocess 实时读取实现边抓边写
- `sync.py` 通过 `subprocess.Popen` + 行缓冲管道驱动 `stream` 模式
- 单次浏览器启动翻完所有页，109 页总耗时约 60s
- 每页写入 ES 后立即更新进度（ES 进度索引），支持中断续传

依赖：
- Node.js + playwright (`npm install playwright`)
- Chromium Headless（自动下载到 `~/Library/Caches/ms-playwright/`）

---

## 增量逻辑

按**最新发布期数**判断：程序自动获取源站当前最新期数（从页面日期选择器提取），与 config 中记录的 `last_period` 对比。

---

## 断点续传

进度自动保存到 `.rizhao_sync_progress.json`，中断后直接运行 `./run.sh sync` 自动续传：

```bash
./run.sh sync      # Ctrl+C 中断
./run.sh sync      # 重启后从上次位置继续
./run.sh sync --reset   # 清除进度，从头开始
```

---

## 数据结构

- **tabType=1**：建设工程材料（全市统一价格）
- **tabType=2**：园林绿化苗木
- **tabType=3**：区县建设工程材料（各区县独立价格）

每页数据为 Element UI Table 结构，包含材料名称/规格型号/单位/参考价格。

---

## 文档 ID（幂等写入）

```
_id = MD5(breed + "_" + spec + "_" + unit + "_" + period + "_" + str(price) + "_" + city + "_" + county)
```

同一材料在同一城市/区县、同一周期、同一价格下重复同步不会产生重复数据。

---

## 数据字段

| 字段 | 说明 |
|------|------|
| breed | 材料名称（clmc）|
| spec | 规格型号（ggxh）|
| unit | 单位（dw）|
| price | 参考价格 |
| period | 期数（如 2026-03）|
| province | 山东省 |
| city | 日照市 |
| county | 区县（tabType=3 时区分）|
| update_date | 更新日期（由 period 转换）|
| create_time | 入库时间（yyyy-MM-dd HH:mm:ss）|

---

## ES 索引与 Mapping

**索引**：`material_rizhao_price`（可配 `config.yml`）

> **重要**：ES 单机部署时会自动设置 `number_of_replicas=0`，避免 yellow 状态。

**Mapping**：
```json
{
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
```

---

## 进度监控

每次同步运行的所有进度写入 ES 索引 `material_rizhao_price_sync_progress`。

**进度记录结构**（`_id = run_id_type`）：

| 字段 | 说明 |
|------|------|
| run_id | 本次运行 ID（yyyy-MM-dd_HH-mm-ss）|
| status | running / completed / interrupted / error |
| tab_type | 当前类别（1/2/3）|
| tab_name | 类别名称 |
| period | 当前期数 |
| current_page | 当前页码 |
| total_pages | 总页数 |
| total_count | 总记录数 |
| docs_written | 已写入文档数 |
| percent | 完成百分比 |
| duration_sec | 已耗时（秒）|
| last_updated | 最后更新时间 |
| error | 错误信息 |

---

## API 结构

`fetch_data.js` 三种工作模式（Node.js + Playwright）：

```bash
# 获取 tabs 和期数
node commands/fetch_data.js metadata

# 抓取单页（兼容旧调用）
node commands/fetch_data.js paginate 1 5   # type=1, page=5

# 流式抓取全部（推荐，subprocess 管道驱动）
node commands/fetch_data.js stream 1 200    # type=1, maxPages=200
```

`stream` 模式输出 JSON Lines，每行格式：
```json
{"page":1,"rows":[...],"totalCount":1083,"totalPages":109,"periods":"2026-03","pageSize":10}
```
最后一行为 `{"done":true,...}` 标记结束。

`sync.py` 通过 `StreamFetcher` 类（Popen + fdopen 行缓冲）实时读取管道，实现边抓取边写入 ES。

---

## 区县列表

| 编码 | 名称 |
|------|------|
| 001001 | 日照市辖区 |
| 001002 | 东港区 |
| 001003 | 岚山区 |
| 001004 | 五莲县 |
| 001005 | 莒县 |

---

## 配置文件

`config.yml`：

```yaml
es:
  host: http://localhost:59200
  index: material_rizhao_price
  sync_progress_index: material_rizhao_price_sync_progress

site:
  base_url: http://58.59.43.227:81/EpointSDRZ
  price_page: http://58.59.43.227:81/dist/#/index/priceDissemination

sync:
  last_period: ""   # 上次同步期数（自动维护）
```

---

## 项目结构

```
rizhao-price/
├── SKILL.md
├── run.sh              # 入口脚本
├── config.yml          # ES/站点配置
├── package.json        # npm 依赖
├── node_modules/       # playwright（自动安装）
├── .rizhao_sync_progress.json  # 进度文件（自动生成）
└── commands/
    ├── sync.py         # 同步主程序
    ├── preview.py
    ├── status.py
    ├── test.py
    ├── check.py
    ├── fetch_data.js   # Playwright 浏览器抓取脚本
    └── utils.py        # 工具函数
```

---

## 依赖

- Python 3
- Node.js + npm
- playwright（`npm install playwright`，自动下载 Chromium）
- requests
- pyyaml
- Elasticsearch 7.x / 8.x
