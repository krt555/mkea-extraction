"""Record-discovery phase for hybrid extraction.

The inventory agent only decides *which samples exist* and their role.  Field
values are extracted later by a full-context one-shot pass.  This keeps the
agentic part focused on record granularity while preserving field completeness.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMError, OpenAICompatibleLLM
from .pdf import PaperText
from .pipeline import merge_usage, usage_dict
from .tools import ToolBox, ToolError, tool_json

INVENTORY_SYSTEM = """You are a sample-inventory agent for mesoporous-materials papers.
Use tools to inspect the paper and return the sample skeleton only. Do not extract
field values. Never invent a sample, table or page. Return exactly one JSON object per turn."""

INVENTORY_TOOLS = """- `list_pages`: page numbers, headings, table IDs.
- `search_pages`: args `{"query": "...", "top_k": 5}`.
- `read_page`: args `{"page": 3, "max_chars": 12000}`.
- `list_tables`: table IDs, dimensions and header rows.
- `read_table`: args `{"table_id": "3.1"}`.
"""


@dataclass
class InventoryResult:
    samples: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    elapsed_s: float = 0.0


class InventoryError(RuntimeError):
    pass


def build_inventory_task(paper: PaperText, *, step: int, max_steps: int) -> str:
    first_page = paper.page_text(1)
    title = next((line.strip() for line in first_page.splitlines() if line.strip()), "")
    tables = ", ".join(table.table_id for table in paper.tables) or "none"
    return f"""Build the sample inventory for {paper.paper_key} ({paper.path.name}).

Pages: {len(paper.pages)}
Detected tables: {tables}
First page heading: {title[:200]}

## Rules
- Include only materials actually synthesized in this paper.
- Exclude literature examples, review tables, commercial/purchased supports, and cited procedures without synthesis details.
- Exclude downstream metal-loaded/grafted derivatives when a parent silica record exists.
- If the paper's target is a final functionalized/activated material, mark that final material as `parent` and do not include both precursor and product.
- One record = one distinct sample/condition. Use synthesis-table rows as the skeleton.
- Include prose-only conditions only when they have a distinct mesostructure or numeric property.
- `role` is `parent`, `derived`, or `literature`; set `parent_label` for derived samples.
- Each sample needs `label`, `role`, optional `parent_label`, `conditions`, `pages`, `tables`.

## Tools
{INVENTORY_TOOLS}

## Action format
Tool call:
{{"thought": "why", "action": "read_table", "action_input": {{"table_id": "3.1"}}}}

Finish:
{{"thought": "inventory complete", "action": "submit_inventory", "action_input": {{
  "inventory": [
    {{"label": "S1", "role": "parent", "conditions": "P123, 55 °C", "pages": [3], "tables": ["3.1"]}}
  ]
}}}}

Inspect at least one page or table before submitting. You are on step {step} of at most {max_steps}.
"""


def discover_inventory(
    paper: PaperText,
    llm: OpenAICompatibleLLM,
    *,
    max_steps: int = 8,
) -> InventoryResult:
    toolbox = ToolBox(paper)
    messages = [
        {"role": "system", "content": INVENTORY_SYSTEM},
        {"role": "user", "content": build_inventory_task(paper, step=1, max_steps=max_steps)},
    ]
    tool_calls: list[dict[str, Any]] = []
    usage_items: list[dict[str, Any]] = []
    issues: list[str] = []
    inspected = False
    start = time.time()
    model = ""

    for step in range(1, max_steps + 1):
        try:
            parsed, response = llm.complete_json(messages, max_tokens=32768)
        except LLMError as exc:
            raise InventoryError(f"inventory LLM failed: {exc}") from exc
        model = response.model
        usage_items.append(usage_dict(response))
        action = parsed.get("action")
        arguments = parsed.get("action_input") or parsed.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        if action in {"submit_inventory", "submit"}:
            if not inspected:
                messages += [
                    {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": "Inspect at least one page or table before submitting the inventory.",
                    },
                ]
                continue
            samples = _normalise_inventory(
                arguments.get("inventory") or arguments.get("samples") or []
            )
            if not samples and len(tool_calls) < 2:
                messages += [
                    {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": "No samples found after too little inspection. Read the synthesis table/pages and retry.",
                    },
                ]
                continue
            return InventoryResult(
                samples=samples,
                issues=issues,
                steps=step,
                tool_calls=tool_calls,
                usage=merge_usage(usage_items),
                model=model,
                elapsed_s=time.time() - start,
            )

        if not isinstance(action, str):
            observation = "invalid action: expected a string"
        else:
            try:
                result = toolbox.call(action, arguments)
                if action in {"read_page", "read_table", "search_pages", "list_tables"}:
                    inspected = True
                tool_calls.append(
                    {"step": step, "tool": action, "arguments": arguments}
                )
                observation = tool_json(result)
            except ToolError as exc:
                observation = f"tool error: {exc}"
                tool_calls.append(
                    {"step": step, "tool": action, "arguments": arguments, "error": str(exc)}
                )
        messages += [
            {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
            {
                "role": "user",
                "content": (
                    f"TOOL_RESULT (step {step}):\n{observation}\n\n"
                    f"Continue. You have {max_steps - step} step(s) left."
                ),
            },
        ]

    raise InventoryError(f"inventory agent did not submit within {max_steps} steps")


def _normalise_inventory(raw: Any) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return samples
    for index, item in enumerate(raw, 1):
        if isinstance(item, str):
            item = {"label": item, "role": "parent"}
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("sample") or f"sample_{index}").strip()
        role = str(item.get("role") or "parent").lower()
        if role not in {"parent", "derived", "literature"}:
            role = "parent"
        samples.append(
            {
                "label": label,
                "role": role,
                "parent_label": item.get("parent_label") or item.get("parent"),
                "conditions": item.get("conditions") or item.get("condition"),
                "pages": item.get("pages") or [],
                "tables": item.get("tables") or [],
            }
        )
    return samples
