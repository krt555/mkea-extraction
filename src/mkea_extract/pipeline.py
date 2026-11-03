"""End-to-end per-paper extraction pipeline."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import PaperContext, build_context
from .llm import LLMResponse, OpenAICompatibleLLM
from .pdf import PaperText
from .postprocess import records_from_response, strip_internal_keys
from .prompts import build_messages


@dataclass
class ExtractionRun:
    records: list[dict[str, Any]]
    issues: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    model: str = ""
    mode: str = "oneshot"
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def as_meta(self) -> dict[str, Any]:
        return {
            "records": len(self.records),
            "issues": self.issues,
            "context": self.context,
            "usage": self.usage,
            "elapsed_s": round(self.elapsed_s, 3),
            "model": self.model,
            "mode": self.mode,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
        }


def extract_paper(
    paper: PaperText,
    llm: OpenAICompatibleLLM,
    *,
    max_chars: int = 200000,
    inventory: list[dict[str, Any]] | None = None,
) -> ExtractionRun:
    context: PaperContext = build_context(paper, max_chars=max_chars)
    messages = build_messages(paper, context, inventory=inventory)
    start = time.time()
    usage_items: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    issues: list[str] = []
    model = ""
    for attempt in range(2):
        parsed, response = llm.complete_json(messages)
        model = response.model
        usage_items.append(usage_dict(response))
        batch, batch_issues = records_from_response(paper, parsed, model=response.model)
        if batch:
            records = batch
            issues = batch_issues
            if attempt:
                issues.insert(0, "retried after empty or structurally invalid first response")
            break
        issues = batch_issues
        if attempt == 0:
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "The previous answer produced no usable record objects. "
                        "Retry once. Return strict JSON with `inventory` (one label per sample) "
                        "and `records` (one object per inventory item, each with the 12 fields). "
                        "Do not return strings in the `records` array."
                    ),
                }
            ]
    public = strip_internal_keys(records)
    return ExtractionRun(
        records=public,
        issues=issues,
        context=context.stats(),
        usage=merge_usage(usage_items),
        elapsed_s=time.time() - start,
        model=model,
        mode="oneshot",
        steps=len(usage_items),
    )


def write_predictions(records: list[dict[str, Any]], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    tmp.replace(path)
    return path


def write_manifest(meta: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def usage_dict(response: LLMResponse) -> dict[str, Any]:
    usage = dict(response.usage or {})
    result = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cost": response.cost,
        "latency_s": round(response.latency_s, 3),
    }
    return result


def merge_usage(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = {
        "calls": len(items),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "latency_s": 0.0,
    }
    for item in items:
        total["prompt_tokens"] += int(item.get("prompt_tokens") or 0)
        total["completion_tokens"] += int(item.get("completion_tokens") or 0)
        total["total_tokens"] += int(item.get("total_tokens") or 0)
        total["cost"] += float(item.get("cost") or 0.0)
        total["latency_s"] += float(item.get("latency_s") or 0.0)
    total["latency_s"] = round(total["latency_s"], 3)
    return total
