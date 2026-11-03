# MesoScope · 介孔材料合成条件抽取与检索平台

> 黑客松参赛包装：在现有 `mkea-extract` 抽取 Agent 之上增加一层 Web 应用，
> 把命令行工具变成可演示、可交互、可复核的产品。

读英文化学论文，自动产出**带页码级引文**的中文结构化合成记录（12 字段），
支持跨论文检索比较与带引用问答。

---

## 1. 快速开始

```bash
# 安装依赖（含 Web 层）：项目用 uv 管理
uv sync --extra web

# 启动（默认 http://127.0.0.1:8000）
python webapp/run.py
# 或（推荐）
.venv\Scripts\python.exe webapp\run.py

# 自定义端口 / 开发热重载
python webapp/run.py --port 8800 --reload
```

> Web 依赖声明在 `pyproject.toml` 的 `web` extra 里。若用 pip：
> `pip install -r webapp/requirements.txt`。

打开浏览器后，若文献库为空，点击 **载入演示数据**：会即时生成 3 篇
演示论文 PDF 并自动跑完抽取，**无需 API Key、无需准备 PDF**。

### 配置真实 LLM（可选）

```bash
cp .env.example .env
# 编辑 .env，填入 MKEA_LLM_API_KEY
```

填入后侧栏会显示「LLM Agent 就绪」，抽取自动切换到 anchored 模式
（全文候选 + 工具 Agent 审核），AI 问答也会从抽取式升级为生成式。
**未配置时所有功能依然完整可用**，由确定性规则引擎承担抽取。

---

## 2. 四个功能模块

| 模块 | 说明 |
| --- | --- |
| **文献库** | 上传 / 拖拽 PDF、按状态分组（正在抽取 / 已完成 / 待抽取 / 失败）、进度百分比与已用时长、**全选与一键抽取 / 一键删除** |
| **提取结果** | 左右联动：点击字段 → 跳转证据页 → 高亮原文引文；可改、可标记已复核、可导出；**修改历史可查看并逐项撤销**；论文候选区默认一行、超出折叠 |
| **样品对比** | 跨论文勾选多条记录，生成条件矩阵，高亮差异项、标出共同条件，可导出对比 CSV |
| **跨论文检索** | 按字段值关键词 + 比表面积/孔径数值区间筛选，横向比较不同论文 |
| **任务与日志** | 任务状态、所用引擎与模型、运行提示（issues） |
| **AI 助手** | 常驻侧边栏，每条回答附页码引文，点击引文跳回原文 |

---

## 3. 架构

```text
浏览器（单页，无构建步骤）
  static/index.html + style.css + app.js
        │  fetch /api/*
        ▼
FastAPI（webapp/app/main.py）
  ├─ papers / jobs    文献与异步抽取任务（后台线程 + 进度轮询）
  ├─ records          复核读写与修改审计
  ├─ search / export  结构化检索与契约导出
  └─ ask              带引用问答
        │
        ├─ extraction.py   封装现有 Agent，不重写抽取逻辑
        │     └─ mkea_extract（PDFLoader / anchored / agent / postprocess）
        ├─ heuristic.py    确定性规则引擎（无 Key 时的抽取路径）
        └─ qa.py           BM25 页级检索 + 引文生成
        ▼
SQLite（webapp/data/mesoscope.db，首次启动自动建库）
```

### 设计要点

**1. 不重写抽取内核。** `extraction.py` 直接 `import mkea_extract`，
复用其 PDF 解析、anchored 模式、单位归一化与证据校验。Web 层只做编排。

**2. 双引擎 + 自动降级。** 有 Key 走 LLM Agent；无 Key、调用失败或返回空结果，
自动回退规则引擎并把原因写入任务 issues。演示现场不会因网络或额度中断。

**3. 证据先校验再入库。** 任何记录写入数据库前，每条引文都要通过
`verify_evidence()` 确认确实出现在所声称的那一页，匹配失败的引文被丢弃并计入
issues——界面上看到的每个 `p.N` 都是可点击验证的。

**4. 规则引擎刻意保守。** 它只在模式命中时填值，并且：
优先读结果/实验段而非引言（避免抽到文献综述里的数值）、
拒绝把别的试剂的浓度安到当前试剂上、
排除「球形胶束」这类描述模板而非产物的句子。
抽不到就留 `null`，不猜。

**5. 演示数据走真实链路。** 载入演示数据时会用 PyMuPDF 实际生成 PDF 文件，
再经过与用户上传完全相同的解析→抽取→校验流程，而不是预埋结果。

**6. 双栏论文按栏阅读。** `get_text("text", sort=True)` 只按纵坐标排序，
双栏论文会把左右栏的句子拼进同一行。`pdf.py` 先探测版心中间几乎无文本块
跨越的竖直空白带作为栏分隔，再按「跨栏块 → 左栏 → 右栏」重排，
使左栏读完才进入右栏。同时不再按字符总量在 PyMuPDF 与 `pdftotext` 之间择优
——`pdftotext -layout` 的空格填充会让字符数虚高从而总是胜出，
而它保留的正是导致错乱的视觉布局。

---

## 4. 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/status` | 引擎状态、统计、字段定义 |
| GET/POST | `/api/papers` | 列表 / 上传 PDF |
| GET | `/api/papers/{id}` | 详情（含记录与任务历史） |
| GET | `/api/papers/{id}/pdf` | 原始 PDF |
| GET | `/api/papers/{id}/pages/{page}` | 单页文本（供定位与高亮） |
| DELETE | `/api/papers/{id}` | 删除论文及其记录 |
| POST | `/api/jobs` | 创建抽取任务（`mode`: anchored/oneshot/agent/rules） |
| GET | `/api/jobs/{id}` | 任务进度 |
| PUT | `/api/records/{id}` | 修改字段 / 标记已复核 |
| GET | `/api/records/{id}/history` | 修改审计（含字段中文名） |
| POST | `/api/records/{id}/revert` | 撤销某一次字段修改 |
| POST | `/api/jobs/batch` | 批量创建抽取任务 |
| POST | `/api/papers/batch-delete` | 批量删除论文 |
| GET | `/api/search` | 结构化检索（关键词 + 数值区间） |
| GET | `/api/export?fmt=jsonl\|csv` | 导出，JSONL 符合 `docs/extraction-contract.md` |
| POST | `/api/ask` | 带引用问答 |
| POST | `/api/demo/seed` | 载入演示数据 |

导出的 JSONL 可直接交回 `mkea-bench` 评测：

```bash
PYTHONPATH=src python -m mkea_extract.cli validate --pred predictions.jsonl
```

---

## 5. 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

全部离线，不访问网络。`tests/test_webapp.py` 覆盖规则引擎的正确性边界
（引言 vs 结果、浓度串味、模板 vs 产物形貌、Å→nm 换算）、
证据 grounding、中文检索映射与导出契约。

---

## 6. 已知限制

- 双向定位到**页码 + 引文高亮**，未做字符级坐标框选。
- 栏检测针对常见的单栏 / 双栏科技论文；三栏或不规则分栏版式会退回按纵坐标排序。
- 规则引擎每篇只产出一条记录；多样品拆分依赖 LLM 模式。
- 任务用后台线程执行，适合演示规模；高并发需换 Celery/RQ 等队列。
- SQLite 单写入者，已用进程锁串行化。
