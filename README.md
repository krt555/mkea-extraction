# MKEA 抽取 Agent（独立包）

> 状态：v0.1 已实现。  
> 本包是从 MKEA workspace 中独立出来的 `extraction/` 子项目，可直接 `uv sync` 使用。  
> 目标：从 `papers/` 的 PDF 中抽取每一条合成记录，输出符合
> [`docs/extraction-contract.md`](docs/extraction-contract.md) 的 JSONL。  
> 本包不包含 `benchmark/`、`golden.jsonl`、`papers/` 原始 PDF、`outputs/` 运行结果或 `.env`。

---

## 1. 职责边界

**做什么**

- 解析 PDF 文本/表格；扫描页自动回退到 OCR（`pdftoppm` + `tesseract`）；
- 用一次 schema-guided LLM 调用识别论文中所有可区分样品/合成条件；
- 按 12 字段 schema 抽取值，并做确定性的单位/空值/格式归一化；
- 为每个非空字段提供页码 + 原文引文，并验证引文确实出现在该页；
- 输出 JSONL、运行 manifest、token/费用统计，不读取 `golden.jsonl`。

**不做什么**

- 不评估自己（评估由 `benchmark/` 负责）；
- 不修改 `golden.jsonl`；
- 不针对 test split 手工调参；
- 不把 gold 的 note/行备注复制进预测。

---

## 2. 快速开始

```bash
# 1. 安装独立包
uv sync

# 2. 配置 LLM：默认 OpenCode Go + DeepSeek V4.1 Flash
cp .env.example .env
# 编辑 .env，填入 MKEA_LLM_API_KEY

# 3. 验证 endpoint / key / model
uv run mkea-extract check

# 4. 先看 PDF 解析和 context 选择，不调用 LLM
make extract-dry

# 5. 抽 5 篇做 smoke test
make extract-smoke

# 6. 全量抽取（支持 --resume）
make extract
```

把 PDF 放到 `papers/`，文件名使用 `paper_001.pdf`、`paper_002.pdf`……；也可以
用 `--paper-id` 或直接传 PDF 路径。

系统依赖：Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)；PyMuPDF 由 `uv sync`
安装。扫描页 OCR 是可选的，需要 `pdftotext` / `pdftoppm`（poppler）和
`tesseract`；没有这些工具时用 `--no-ocr` 跳过扫描页。

输出：

- `outputs/predictions.jsonl` — 契约预测；
- `outputs/predictions.manifest.json` — 每篇状态、context、token、费用、
  校验结果（不含 API Key）。

输出自检（不依赖 benchmark）：

```bash
uv run mkea-extract validate --pred outputs/predictions.jsonl
```

> 本包不含 `benchmark/` 和 `golden.jsonl`。如需 Field F1 / Record F1 等评测，
> 请把预测 JSONL 交回原 MKEA workspace 的 `mkea-bench`，或按
> [`docs/extraction-contract.md`](docs/extraction-contract.md) 接入其他评估器。

---

## 3. LLM 配置

默认值（见 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MKEA_LLM_API_KEY` | 空 | 必填；OpenCode Console 的 API Key |
| `MKEA_LLM_BASE_URL` | `https://opencode.ai/inference/go/openai/v1` | OpenAI-compatible base URL |
| `MKEA_LLM_MODEL` | `deepseek-v4.1-flash` | 默认模型 |
| `MKEA_LLM_TIMEOUT` | `600` | 单请求超时（秒）；长论文/推理需要更久 |
| `MKEA_LLM_MAX_TOKENS` | `65536` | 输出 token 上限（含 reasoning/证据） |
| `MKEA_LLM_TEMPERATURE` | `0` | 抽取使用确定性解码 |
| `MKEA_LLM_SESSION_ID` | 空 | 可选；OpenCode Go prompt-cache session |

`.env` 已被 `.gitignore` 排除。也可以直接用进程环境变量覆盖。切换到其他
OpenAI-compatible 服务只需改 `MKEA_LLM_BASE_URL` / `MKEA_LLM_MODEL`。

如果接回原 workspace 的 `benchmark/`，其 judge 复用同一组变量（`MKEA_JUDGE_*`
仍作为兼容覆盖）。

---

## 4. 架构

```text
PDF
 ├─ pdf.py          PyMuPDF 文本 + 表格；pdftotext 兜底；扫描页 OCR；缓存
 ├─ context.py      长文按 synthesis/property 关键词选段，保留 [[PAGE n]] 标记
 ├─ tools.py        Agent 工具：页面/检索/表格/计算器/证据校验
 ├─ agent.py        有界 ReAct 循环：选择工具 → 观察 → 继续/提交
 ├─ prompts.py      one-shot 抽取 prompt（--no-agent 或 fallback）
 ├─ llm.py          OpenAI-compatible /chat/completions + JSON repair + retry
 ├─ postprocess.py   单位换算、null 语义、证据校验、role 过滤、去重
 ├─ pipeline.py      one-shot / agent 单篇端到端；usage/manifest
 ├─ validation.py    契约/重复 ID/证据覆盖率自检
 └─ cli.py           check / extract / validate
```

### 4.1 两层设计

**确定性层**

- `pdf.py`：PyMuPDF 提取页文本和表格；PyMuPDF 文本不足时用 `pdftotext -layout`
  兜底；空白/扫描页用 `pdftoppm + tesseract` OCR；结果缓存。
- `postprocess.py`：字段 key、null、单位、Å→nm、`wt%`→`w%`、证据 grounding、
  `parent/derived/literature` 过滤、重复合成配方合并。
- `validation.py`：结构、record_id、重复 ID、证据覆盖率。
- `tools.py`：页面读取、全文检索、表格读取、算术计算和证据校验都是确定性实现。

**Agent 层**

默认模式是 **anchored**：先用 one-shot 全文抽取得到候选记录，再让有界工具 Agent
审核候选、合并过拆分、补漏和修正字段。

```text
Phase 1  one-shot(full text)            # 候选 records + 字段
Phase 2  list_pages / list_tables       # 了解论文结构
Phase 3  read_page / read_table         # 核对 Experimental / 合成表
Phase 4  search_pages / calculate       # 补字段、换算
Phase 5  verify_evidence                # 提交前检查页码/引文
Phase 6  submit                         # 审核后的最终 records
```

纯 `--mode agent` 则是从零开始发现记录，适合做对照；`--mode oneshot` 是纯全文
抽取；`--mode hybrid` 是 inventory + 全文，目前因 inventory 过拆分不作为默认。

Agent 只能通过工具观察论文，不能直接“看到全文”；这减少了长上下文里的
“顺手编造”，也让每次读取都有页码可追溯。循环默认最多 10 步；超时、JSON 结构错误
或未提交时自动回退到 one-shot 抽取。

### 4.2 为什么这样分工

- LLM 擅长：读段落、发现样品、判断字段语义、处理模糊描述；
- 确定性代码擅长：PDF/表格访问、单位换算、证据存在性校验、schema、去重；
- 因此 Agent 负责“看什么、抽什么、何时提交”，代码负责“怎么访问、怎么规范化、
  怎么保证输出可评估”。

### 4.3 证据与记录策略

- 每个非空字段要求一条页码级引文；引文必须能在该页文本/表格中匹配；
- 匹配失败会丢弃该证据并记录 issue，不静默放过；
- `parent` 是直接合成的介孔材料，`derived` 是负载/复合/后处理变体，
  `literature` 是文献/商业样品；
- 存在 parent 时，后处理会过滤 derived/literature；完全相同合成配方的重复记录会合并；
- Agent 提交时若证据覆盖率低于 70%，会被要求先补证据再提交。

关键设计：

- **整篇上下文 vs 工具检索**：anchored 先用最多 200k 字符的全文候选，再由 Agent
  按需读取页面/表格审核；`--mode oneshot` 完全走全文，`--mode agent` 完全走工具。
- **可恢复**：`--resume` 根据 manifest 跳过已完成论文；PDF 文本、表格、OCR 缓存到
  `data/cache/extraction/pdf_text/`。
- **不泄漏**：不读取 `golden.jsonl`、`data/manifests/` 或 benchmark 的 oracle 预测。

---

## 5. CLI

```bash
# 验证 LLM
uv run mkea-extract check

# 指定论文 / 范围
uv run mkea-extract extract papers/paper_001.pdf --out /tmp/p1.jsonl
uv run mkea-extract extract --paper-id 1 --paper-id 2 --out /tmp/p12.jsonl
uv run mkea-extract extract --limit 10 --out outputs/dev.jsonl

# 解析预览（不调用 LLM，不需要 key）
uv run mkea-extract extract --dry-run --limit 5

# 校验已有预测
uv run mkea-extract validate --pred outputs/predictions.jsonl

# 其他 provider
uv run mkea-extract extract \
  --base-url https://api.openai.com/v1 \
  --model gpt-4.1-mini \
  --api-key-env OPENAI_API_KEY
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--mode` | `anchored` / `agent` / `oneshot` / `hybrid`，默认 `anchored` |
| `--agent` / `--no-agent` | 兼容别名：`--no-agent` = oneshot |
| `--agent-steps` | Agent / inventory 单篇最大步数，默认 10 |
| `--resume` | 跳过 manifest 中已成功的论文 |
| `--dry-run` | 只解析 PDF / 选 context |
| `--no-cache` | 忽略 PDF 文本缓存 |
| `--no-ocr` | 禁用扫描页 OCR |
| `--max-chars` | one-shot 上下文字符预算，默认 200000 |
| `--fail-fast` | 遇到论文级错误立即停止 |
| `--strict` | 校验有 error 时返回非 0 |

---

## 6. 输出与质量检查

每行一条记录：

```json
{"paper_id": 1, "paper_key": "paper_001", "record_id": "paper_001_r01", "record_index": 1, "pdf_path": "papers/paper_001.pdf", "fields": {"assembly_method": "sol-gel", "surface_area": "720", "...": "..."}, "evidence": {"surface_area": [{"page": 4, "quote": "The BET surface area was 720 m2/g."}]}, "confidence": {"surface_area": 0.95}}
```

`mkea-extract validate` 检查：

- 12 个字段 key 是否齐全；
- `paper_key` / `record_id` 格式与唯一性；
- `pdf_path` 是否与 `paper_key` 一致；
- 非空字段的证据覆盖率；
- 空记录、重复 ID、未知字段。

`--strict` 适合 CI；日常抽取出错时先看 manifest 中每篇的 `issues`。

---

## 7. 测试

```bash
uv run python -m unittest discover -s tests -v
# 或
make test
```

测试全部离线，使用 scripted fake LLM，不访问网络。覆盖：单位归一化、证据 grounding、
record_id、context 选择、JSON 解析、.env 配置、端到端 pipeline。

---

## 8. 当前基线（2026-10-26）

| Split | Papers | Field F1 | Soft F1 | Record F1 | Composite |
| --- | ---: | ---: | ---: | ---: | ---: |
| smoke（one-shot 基线） | 6 | 61.81% | 71.21% | 93.33% | 57.1 |
| dev（one-shot，role filter 前） | 20 | 44.14% | 55.04% | 82.76% | 43.1 |
| diagnostic（one-shot，9 篇） | 9 | 42.98% | 55.59% | 82.76% | 44.1 |
| diagnostic（agent，9 篇） | 9 | 38.22% | 51.15% | 86.79% | 43.4 |
| diagnostic（anchored，9 篇） | 9 | 42.45% | 55.58% | 85.71% | 45.1 |

详细分析、逐篇记录数和已知问题见原 MKEA workspace 的
`reports/extraction-baseline.md`（不随本独立包分发）。

diagnostic 是专门挑出的困难小样本（8/10/14/24/53/56/61/70/86），用于快速回归，
不要和 smoke/dev 的绝对分数直接比较。

当前默认是 **anchored**（one-shot 全文候选 + Agent 审核）：

| Paper | Gold | Anchored | Evidence |
| ---: | ---: | ---: | ---: |
| 3 | 12 | 12 | 99.1% |
| 4 | 7 | 6 | 100.0% |
| 10 | 4 | 4 | 100.0% |

三项合计 22/23，Field F1 **74.29%**，Soft F1 77.09%，Record F1 **97.78%**，
Composite **65.1**。这是定向 probe，不是随机 split 分数。

纯 agent 的对照：21/23，Field F1 71.57%，Record F1 95.45%，Composite 64.5；
另一次运行 23/23、Field F1 68.15%，说明单次结果有模型非确定性。

当前主要瓶颈是记录粒度：派生样品、煅烧变体和 pH/老化序列容易被拆成额外记录；
BET/孔径/表面活性剂字段较好，酸/碱浓度和形貌仍偏弱。

---

## 9. 已知限制

- **PDF 依赖**：文本/表格解析使用 PyMuPDF（AGPL-3.0 或商业许可）；如果项目需要
  更宽松的分发许可，需要替换为 MIT/BSD 解析器或把 PyMuPDF 作为可选依赖。
- 表格检测对无边框、跨页、双栏表格仍可能失败；此时会退回页文本，由 Agent 阅读。
- 单次 Agent 依赖模型发现记录；矩阵/跨表样本可能漏抽，建议用 dev split 检查记录数。
- 没有针对单篇做多轮 self-consistency；成本敏感场景可先跑 smoke/dev。
- LLM 输出非确定性（temperature=0 仍可能有细微差异）；manifest 记录模型和 token，
  但不保证跨 provider 完全一致。
