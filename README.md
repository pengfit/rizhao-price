# rizhao-price

日照工程造价材料信息采集工具。从 `http://58.59.43.227:81/dist/#/index/priceDissemination` 抓取材料价格数据，写入本地 ES。

---

## 快速开始

```bash
cd ~/.openclaw/workspace/skills/rizhao-price

# 增量同步（全 tab）
./run.sh sync --force

# 增量检测（不写入，查看是否有新数据）
python3 commands/check.py

# 按类别同步（单 tab）
./run.sh sync --type 2
```

---

## 增量机制

每 30 分钟自动检测一次，无需人工干预：

```bash
# 手动触发增量检测
python3 commands/check.py
```

**检测逻辑**（分两级）：

1. **周期维度**：网站当前期数 vs config `last_period` → 不同则触发全量同步
2. **tab 维度**：同周期内，逐 tab 对比网站 totalCount vs ES doc_count（按 `tab_type` 过滤）→ 有差异则触发该 tab 增量同步

**触发同步时逐 tab 执行**，每个 tab 幂等写入自动补漏：

```
[→] 同步 tab 2 园林绿化苗木...
[✓] tab 2 完成
[→] 同步 tab 3 区县建设工程材料...
[✓] tab 3 完成
[i] 增量同步全部完成
```

---

## 命令参考

| 命令 | 说明 |
|------|------|
| `./run.sh sync` | 增量同步（当前 tab） |
| `./run.sh sync --force` | 全量同步（全 tab，逐 tab 补漏）|
| `./run.sh sync --type N` | 指定 tab 同步（N=1/2/3）|
| `./run.sh sync --no-check` | 跳过增量检测直接同步 |
| `./run.sh sync --dry-run` | 预览模式，不写入 ES |
| `./run.sh sync --reset` | 重置进度，从头开始 |
| `./run.sh preview` | 预览数据 |
| `./run.sh status` | 查看同步进度 |
| `./run.sh test` | 测试连接 |
| `python3 commands/check.py` | 手动增量检测 |

---

## 三个类别

| tab | 名称 | 数据量 |
|-----|------|--------|
| 1 | 建设工程材料 | ~1083 条 |
| 2 | 园林绿化苗木 | ~7 条 |
| 3 | 区县建设工程材料 | ~60 条 |

---

## 数据输出

**ES 索引**：`material_rizhao_price`

**进度索引**：`material_rizhao_price_sync_progress`

每条材料记录字段：breed（材料名称）、spec（规格型号）、unit（单位）、price（参考价格）、period（期数）、tab_type（类别 ID）、tab_name（类别名称）、province、city、county、update_date、create_time 等。

---

## 依赖

- Python 3.14
- Node.js + playwright
- 本地 ES：`http://localhost:59200`
