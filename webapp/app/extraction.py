"""Extraction service: wraps the existing mkea_extract package.

Design rule: this module never reimplements extraction logic.  It loads PDFs
with ``mkea_extract.pdf.PDFLoader`` and, when an API key is present, calls the
real anchored agent from ``mkea_extract``.  Without a key it falls back to the
deterministic rule engine so the app stays fully usable offline.
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path
from typing import Any

# Make the sibling src/ layout importable without installing the package.
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mkea_extract.pdf import PDFError, PDFLoader, PaperText  # noqa: E402
from mkea_extract.schema import FIELD_KEYS, FIELD_LABELS  # noqa: E402

from .heuristic import extract_records_heuristic  # noqa: E402

PAPERS_DIR = _ROOT / "papers"
_LOADER_LOCK = threading.Lock()
_LOADER = PDFLoader(use_cache=True, ocr=False)

# Single source of truth for the mode picker: the UI renders these labels and
# descriptions, and run_extraction() below dispatches on the same ids.
EXTRACTION_MODES: list[dict[str, Any]] = [
    {
        "value": "anchored",
        "label": "推荐 · 通读全文再复核",
        "short": "推荐",
        "summary": "先通读全文给出草稿，再让 AI 逐页核对、补漏、合并重复样品。",
        "detail": "准确率最高，字段最全，适合正式整理数据。单篇约 1–3 分钟。",
        "needs_llm": True,
        "speed": "较慢",
        "recommended": True,
    },
    {
        "value": "oneshot",
        "label": "快速 · 通读全文一次",
        "short": "快速",
        "summary": "把论文全文交给 AI 读一遍，直接给出结果，不再复核。",
        "detail": "速度快、花费少，但偶尔会漏样品或填错字段。适合先快速看个大概。",
        "needs_llm": True,
        "speed": "快",
        "recommended": False,
    },
    {
        "value": "agent",
        "label": "深挖 · AI 自己翻页查表",
        "short": "深挖",
        "summary": "不预先给草稿，让 AI 自主翻页、读表格、做换算后再提交。",
        "detail": "适合样品都写在表格里、正文描述零散的论文。耗时与花费最高。",
        "needs_llm": True,
        "speed": "慢",
        "recommended": False,
    },
    {
        "value": "rules",
        "label": "离线 · 关键词规则匹配",
        "short": "离线",
        "summary": "完全不调用 AI，用内置化学关键词规则扫描全文。",
        "detail": "几秒出结果、不花钱、不联网，但每篇只给一条记录且字段较少。没有 API Key 时系统也会自动用它。",
        "needs_llm": False,
        "speed": "极快",
        "recommended": False,
    },
]

MODE_VALUES: set[str] = {item["value"] for item in EXTRACTION_MODES}


def load_paper(path: str | Path) -> PaperText:
    """Parse a PDF into pages and tables (cached by the existing loader)."""
    with _LOADER_LOCK:
        return _LOADER.load(Path(path))


def llm_status() -> dict[str, Any]:
    """Report whether the real agent can run, without exposing the key."""
    try:
        from mkea_extract.config import ConfigError, LLMConfig

        try:
            config = LLMConfig.from_env()
        except ConfigError as exc:
            return {"available": False, "reason": str(exc), "model": "", "base_url": ""}
        return {
            "available": True,
            "reason": "",
            "model": config.model,
            "base_url": config.base_url,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "reason": f"配置读取失败: {exc}", "model": "", "base_url": ""}


def _normalize(records: list[dict[str, Any]], paper_key: str) -> list[dict[str, Any]]:
    """Force both engines onto the same record contract."""
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(records, 1):
        fields = {key: raw.get("fields", {}).get(key) for key in FIELD_KEYS}
        evidence_in = raw.get("evidence") or {}
        evidence: dict[str, list[dict[str, Any]]] = {}
        for key, items in evidence_in.items():
            if key not in FIELD_KEYS or not isinstance(items, list):
                continue
            cleaned = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                page = item.get("page")
                quote = item.get("quote")
                if isinstance(page, int) and isinstance(quote, str) and quote.strip():
                    cleaned.append({"page": page, "quote": quote.strip()})
            if cleaned:
                evidence[key] = cleaned
        confidence = {
            key: float(value)
            for key, value in (raw.get("confidence") or {}).items()
            if key in FIELD_KEYS and isinstance(value, (int, float))
        }
        out.append(
            {
                "record_id": f"{paper_key}_r{index:02d}",
                "record_index": index,
                "fields": fields,
                "evidence": evidence,
                "confidence": confidence,
            }
        )
    return out


def run_extraction(
    paper: PaperText,
    *,
    mode: str = "anchored",
    max_steps: int = 8,
    max_chars: int = 200000,
    progress=None,
) -> dict[str, Any]:
    """Extract records for one already-parsed paper.

    Returns a dict with records, engine, model, usage, issues.  Any LLM failure
    degrades to the rule engine instead of failing the job, so a demo never
    dead-ends on a network error.
    """

    def notify(stage: str, percent: int) -> None:
        if progress:
            progress(stage, percent)

    status = llm_status()
    issues: list[str] = []

    if mode != "rules" and status["available"]:
        notify("调用 LLM Agent", 45)
        from mkea_extract.agent import AgentError, extract_paper_agent
        from mkea_extract.anchored import extract_paper_anchored
        from mkea_extract.config import LLMConfig
        from mkea_extract.llm import LLMError, OpenAICompatibleLLM
        from mkea_extract.pipeline import extract_paper as extract_oneshot

        try:
            llm = OpenAICompatibleLLM(LLMConfig.from_env())
            if mode == "oneshot":
                run = extract_oneshot(paper, llm, max_chars=max_chars)
            elif mode == "agent":
                run = extract_paper_agent(paper, llm, max_steps=max_steps)
            else:
                run = extract_paper_anchored(
                    paper, llm, max_steps=max_steps, max_chars=max_chars
                )
            notify("后处理与证据校验", 85)
            if run.records:
                return {
                    "records": _normalize(run.records, paper.paper_key),
                    "engine": f"llm:{run.mode}",
                    "model": run.model or status["model"],
                    "usage": run.usage,
                    "issues": list(run.issues),
                }
            issues.append("LLM 返回 0 条记录，已回退规则引擎")
        except (LLMError, AgentError) as exc:
            issues.append(f"LLM 调用失败，已回退规则引擎：{exc}")
        except Exception as exc:  # pragma: no cover - defensive
            issues.append(f"Agent 异常，已回退规则引擎：{exc}")
            traceback.print_exc()
    elif mode != "rules":
        issues.append(f"未配置 LLM：{status['reason']}；已使用规则引擎")

    notify("规则引擎抽取", 60)
    records, rule_issues = extract_records_heuristic(paper.pages_with_tables)
    notify("后处理与证据校验", 85)
    return {
        "records": _normalize(records, paper.paper_key),
        "engine": "rules",
        "model": "deterministic-rules",
        "usage": {},
        "issues": issues + rule_issues,
    }


def verify_evidence(paper: PaperText, page: int, quote: str) -> bool:
    """Deterministic grounding check: does the quote really occur on that page?"""
    if not quote:
        return False
    text = " ".join(paper.page_with_tables(page).split()).lower()
    needle = " ".join(quote.split()).lower()
    if needle in text:
        return True
    # Tolerate whitespace/ligature noise by checking a long prefix.
    head = needle[: max(24, len(needle) // 2)]
    return bool(head) and head in text


__all__ = [
    "EXTRACTION_MODES",
    "FIELD_KEYS",
    "FIELD_LABELS",
    "MODE_VALUES",
    "PAPERS_DIR",
    "PDFError",
    "load_paper",
    "llm_status",
    "run_extraction",
    "verify_evidence",
]
