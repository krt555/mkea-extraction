# 知识抽取输出契约（v0.1）

> 适用对象：`extraction/` 子项目中的抽取 Agent，以及任何要接入 MKEA-Bench 的抽取系统。  
> 原则：一条记录 = 一个可区分的合成条件/样品；字段值可以是字符串/数字/null；建议带页码级证据。

---

## 1. 文件格式

### 1.1 JSONL（推荐）

每行一条记录：

```json
{
  "paper_id": 1,
  "paper_key": "paper_001",
  "record_id": "paper_001_r01",
  "record_index": 1,
  "pdf_path": "papers/paper_001.pdf",
  "fields": {
    "assembly_method": "溶胶-凝胶",
    "solution_system": "EtOH:H2O=5.47",
    "acid_base_concentration": "HCl 0.078 w%",
    "surfactant": "Pluronic P123",
    "surfactant_concentration": "14.26 w%",
    "silica_source": "TEOS 44.55 w%",
    "salt": null,
    "synthesis_temperature": "80 °C aging 4 h; room-temperature drying 1 week; 450 °C calcination",
    "mesostructure": "2D hexagonal (p6mm)",
    "surface_area": "720",
    "pore_size": "4.3",
    "morphology": "fibers; axially aligned pores; submicron to hundreds of microns"
  },
  "evidence": {
    "surfactant": [
      {"page": 1, "quote": "Pluronic P123 (EO20PO70EO20) was used as the structure-directing agent."}
    ],
    "surface_area": [
      {"page": 4, "quote": "The BET surface area was 720 m2 g-1."}
    ]
  },
  "confidence": {"surfactant": 0.95}
}
```

### 1.2 每篇论文一个 JSON

```json
{
  "paper_id": 1,
  "paper_key": "paper_001",
  "pdf_path": "papers/paper_001.pdf",
  "records": [
    {
      "record_id": "paper_001_r01",
      "record_index": 1,
      "fields": {"...": "..."},
      "evidence": {"...": []}
    }
  ]
}
```

### 1.3 目录

`--pred` 可以指向目录，评估器会递归读取 `*.json` / `*.jsonl`，按 `paper_id` 合并。

### 1.4 字段名

推荐使用英文 key：`assembly_method`、`surface_area` 等（见下表）。为了兼容旧版
gold/人工表格，也接受中文 label：`组装方式`、`比表面积(m²/g)` 等。新代码请统一用
英文 key。

未知字段会被忽略并记录警告（`validate` 命令可查看）。缺失字段视为 `null`。

---

## 2. 字段定义

| 英文 key | 中文 label | 类型 | 单位/格式 | 说明 |
| --- | --- | --- | --- | --- |
| `assembly_method` | 组装方式 | string | 自由文本 | 主导路线，如溶胶-凝胶、软模板、酸性/碱性、水热、双相界面、微乳液、后铝化、SAH 扩孔 |
| `solution_system` | 合成溶液体系（体积比） | string | `H2O:EtOH=1:1` | 溶剂体系与体积比；单溶剂写 `H2O`；可带后处理介质说明 |
| `acid_base_concentration` | 酸/碱浓度 | string | `HCl 5.45 w%` / `pH ≈1` / `NH4OH 3.8 w%` | 酸/碱实体 + 浓度；多个用 `；` 分隔 |
| `surfactant` | 表面活性剂 | string | 名称/混合物 | P123、F127、CTAB、SDS 等；EO 式与商品名均可 |
| `surfactant_concentration` | 表面活性剂浓度 | string | `2.35 w%` 或 `0.0235` | 优先质量分数；bare fraction 明确是分数还是百分数 |
| `silica_source` | 硅源/浓度 | string | `TEOS 44.55 w%` | TEOS/TMOS/水玻璃/Ludox/硅溶胶/HNT/kanemite 等；可含多种 |
| `salt` | 盐种类/浓度 | string/null | `NaCl 5.11 w%` / `无` / null | 额外加入的盐；`无`/`0` 与 null 等价 |
| `synthesis_temperature` | 合成温度 | string | `80 °C aging 4 h; 450 °C calcination` | 多步温度程序；保留时间、气氛、后处理 |
| `mesostructure` | 产物介孔结构 | string | `2D hexagonal (p6mm)` / `cubic Im3m` / `lamellar` | 相/空间群/材料家族；可写 MCM-41、SBA-15 等 |
| `surface_area` | 比表面积(m²/g) | number/string | `720` / `>1000` / `2–8` | BET 比表面积；区间/上下界保留原样 |
| `pore_size` | 孔径分布(nm) | number/string | `4.3` / `2–8` / `3.0, 5.8` | 孔径或分布；统一 nm；Å 请换算 |
| `morphology` | 产物形态 | string | 自由文本 | 形状、尺寸、取向、核壳/空心/分级结构 |

### 2.1 字段填写优先级

1. **论文明确给出的值**优先；
2. 论文只给原始投料、gold 要求质量分数时，按论文的密度/摩尔质量计算，并在 `notes` 或 evidence 中记录；
3. 论文未给、也无法从论文内部数据计算 → `null`；
4. 不要从外部知识补全，不要猜测；
5. 同一字段有多个值时，用 `；` 分隔，保留全部（不要只取第一个）；
6. **不要输出 `同上` / `同主样品`**：请把表格中的引用展开为实际值（gold 在内存中也会
   展开 24 处此类引用，原文件不改）。

### 2.2 单位约定

- 浓度优先 `w%`（质量分数）；`wt%` 等价；
- 摩尔浓度写 `M` 或 `N`；pH 写 `pH ≈ x`；
- 比表面积 `m²/g`；孔径 `nm`；温度 `°C`；
- 体积比 `A:B = x:y`；质量比请注明 `w/w`；
- 区间写 `2–8`；下界写 `>1000`；近似写 `≈`；均值±标准差写 `720 ± 20`。

### 2.3 null 语义

`null` 表示以下任一情况：

- 论文没有报道；
- 论文明确说不适用；
- 论文信息不足，无法计算；
- 该字段对该样品不适用。

不要把以下内容当 null：

- `"无"` / `"0"`：对盐字段表示“没有加盐”，评估器会与 null 等价；
- `"未形成有效沉淀"`：这是产物结构信息，不是 null；
- `"0"` 比表面积/孔径：如果论文明确报道为 0，保留 0（不要改成 null）。

建议在 `notes` 中说明 null 原因，例如：

```json
"notes": "HCl concentration not reported; pH only."
```

---

## 3. 记录拆分规则

一条记录对应一个**可区分样品/合成条件**：

| 变化 | 是否新记录 |
| --- | --- |
| 不同表面活性剂（P123 vs F127） | 是 |
| 不同温度/时间（35 vs 55 °C） | 是 |
| 不同盐（NaCl vs NaI） | 是 |
| 不同硅源（TEOS vs TMOS） | 是 |
| 不同后处理（煅烧 vs 萃取）但配方相同 | 通常是同一记录的不同表征状态，除非论文把它作为独立样品 |
| 同一配方的重复实验/误差棒 | 否 |
| 只改变搅拌速度/粒径 | 是（如果论文把它作为独立样品并给出不同孔结构） |
| 多步温度（老化/干燥/焙烧） | 否，放在 `synthesis_temperature` |

**不要**把一篇论文压成一条“平均记录”；也**不要**把同一配方的不同表征拆成多条。
如果论文有一张 12 行的条件矩阵，通常应输出 12 条记录（除非某些行只是重复/对照）。

---

## 4. 证据格式

```json
"evidence": {
  "字段 label 或 key": [
    {"page": 3, "quote": "原文引文，尽量短，保留数字和单位"},
    {"page": 4, "quote": "另一条支持证据"}
  ]
}
```

要求：

- `page` 从 1 开始；
- `quote` 是 PDF 中可定位的原文（允许空白/标点归一化）；
- 派生值请引用原始投料（质量/体积/摩尔数），而不是只引用最终 w%；
- 一个字段可有多条证据；
- 没有证据的字段可以省略，但会降低 `evidence_coverage`；
- 不要把 gold 的 note 当作证据。

评估器会检查：

- 引文是否能在 PDF 中找到（grounding）；
- 页码是否匹配；
- Agent judge 会进一步判断引文是否真的支持该值。

---

## 5. 置信度（可选）

```json
"confidence": {"surfactant": 0.95, "surface_area": 0.8}
```

0–1；不直接进入官方 P/R/F1。报告会计算 Brier score、ECE、overconfidence 等
校准指标；低置信度字段建议优先人工复核。

---

## 6. 最小示例

```json
{"paper_id": 999, "paper_key": "paper_999", "record_id": "paper_999_r01", "record_index": 1, "pdf_path": "papers/paper_999.pdf", "fields": {"assembly_method": "溶胶-凝胶", "solution_system": "H2O:EtOH=1:1", "acid_base_concentration": "HCl 1.0 w%", "surfactant": "Pluronic P123", "surfactant_concentration": "2.0 w%", "silica_source": "TEOS 5.0 w%", "salt": null, "synthesis_temperature": "40 °C 24 h; 550 °C calcination 6 h", "mesostructure": "2D hexagonal (p6mm)", "surface_area": "700", "pore_size": "5.0", "morphology": "spherical particles, 200-500 nm"}}
```

（`paper_id=999` 是示例占位；真实评估请使用 1–100。）

---

## 7. 验证与评估

> 本独立包不包含 `mkea-bench`。下面的 benchmark 命令需要在原 MKEA workspace
> 中运行；在本包内可先用
> `uv run mkea-extract validate --pred path/to/predictions.jsonl` 做结构自检。

```bash
# 检查覆盖、字段名、记录数
uv run mkea-bench validate \
  --golden golden.jsonl \
  --pred path/to/predictions.jsonl \
  --split test

# 评估
uv run mkea-bench evaluate \
  --golden golden.jsonl \
  --pred path/to/predictions.jsonl \
  --out reports/runs/my-agent \
  --split test \
  --no-evidence
```

常见警告：

- `missing_papers`：某些 test 论文没有预测；这些论文的所有 gold 非空字段会记为 omission；
- `empty_papers`：有文件但没有记录；
- `out_of_split_papers`：预测了 dev/smoke 等非当前 split 的论文，评估时会忽略；
- `extra_papers`：预测了 golden 中不存在的论文；
- `unknown_field_labels`：字段名拼错或不在 schema 中；
- 记录数明显少于 gold：检查是否把条件矩阵压成了一条。

---

## 8. 反泄漏要求

- 抽取 Agent 不得读取 `golden.jsonl`、`data/manifests/` 中的 gold 统计、或 `reports/selftest/predictions/oracle.jsonl`；
- 不得针对 test split 手工调参；
- 不得把 gold 的 note/行备注复制进预测；
- Prompt/示例中不要包含 test 论文的 gold 值；
- 如果使用 few-shot，示例应来自 dev split 或合成示例。

评估器不检查泄漏，但 leaderboard 结果会因此失效。建议在抽取 Agent 仓库中把
`golden.jsonl` 路径加入禁止读取列表，并记录每次运行的数据版本。
