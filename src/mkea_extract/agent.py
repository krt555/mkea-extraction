"""Bounded tool-using extraction agent.

The deterministic layer owns PDF parsing, table access, arithmetic, schema
validation and post-processing.  This module only owns the decision loop: the
model chooses which pages/tables to inspect and when to submit records.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .llm import LLMError, OpenAICompatibleLLM
from .pdf import PaperText
from .pipeline import ExtractionRun, merge_usage, usage_dict
from .postprocess import records_from_response, strip_internal_keys
from .schema import FIELD_DESCRIPTIONS, FIELD_KEYS, FIELD_LABELS
from .tools import ToolBox, ToolError, tool_json

AGENT_SYSTEM = """You are an autonomous mesoporous-materials extraction agent.
You work step by step, can call tools to inspect the paper, and must finish by
submitting structured records. Return exactly one JSON object per turn.
Never invent a page, table, value or evidence quote. Use null for unreported fields."""


def _field_lines() -> str:
    return "\n".join(
        f"- {key} ({FIELD_LABELS[key]}): {FIELD_DESCRIPTIONS[key]}" for key in FIELD_KEYS
    )


def _candidate_block(candidates: list[dict[str, Any]] | None) -> str:
    if not candidates:
        return ""
    lines = []
    for index, record in enumerate(candidates[:30], 1):
        fields = record.get("fields") or {}
        compact = {
            key: (str(value)[:120] if value is not None else None)
            for key, value in fields.items()
        }
        lines.append(
            f"{index}. {record.get('label') or record.get('record_id') or 'candidate'} | "
            f"role={record.get('role') or 'parent'} | "
            f"{json.dumps(compact, ensure_ascii=False)}"
        )
    return (
        "\n## Candidate records (full-text pass)\n"
        + "\n".join(lines)
        + "\nStart from these candidates. Prefer keeping/merging correct candidates over "
        "re-extracting from scratch. Add a missing sample only when the paper clearly "
        "contains it; drop/merge only with evidence. Correct weak fields with tools.\n"
    )


def build_agent_task(
    paper: PaperText,
    *,
    step: int,
    max_steps: int,
    candidates: list[dict[str, Any]] | None = None,
) -> str:
    first_page = paper.page_text(1)
    title = next((line.strip() for line in first_page.splitlines() if line.strip()), "")
    tables = ", ".join(table.table_id for table in paper.tables) or "none"
    candidate_block = _candidate_block(candidates)
    return f"""Extract the synthesis records from {paper.paper_key} ({paper.path.name}).

## Paper
- pages: {len(paper.pages)}
- detected tables: {tables}
- first page heading: {title[:200]}
{candidate_block}
## Your job
1. Inspect the synthesis table(s) and/or Experimental section with tools.
2. Build a sample inventory. One record = one distinct synthesized sample/condition.
3. Fill the 12 fields for every record, with page-level evidence.
4. Use the calculator for mass fractions, ratios and unit conversions when useful.
5. Submit only when the inventory and records are complete.

## Scope
- Only materials synthesized in this paper with a mesoporous silica/organosilica framework.
- Exclude literature examples, review tables, commercial supports, and cited procedures with no synthesis details.
- Exclude downstream metal-loaded/grafted catalysts made from an already-counted support.
- One sample = one record; do not split characterization conditions.
- `role` must be `parent`, `derived`, or `literature`; set `parent_label` for derived samples.

## Fields (use exactly these keys)
{_field_lines()}

## Value rules
- Keep values compact; no explanations, masses or calculation narratives in fields.
- Prefer `w%` for concentrations; convert `wt%` to `w%`; convert Å to nm.
- `salt` is only deliberately added salt; use null when absent/unreported.
- Do not write 0 for an unmeasured property; use null.
- For every non-null field provide one evidence entry:
  `{{"page": <page>, "quote": "<verbatim text from that page>"}}`.

## Tools
- `list_pages`: page numbers, headings, table IDs.
- `search_pages`: args `{{"query": "...", "top_k": 5}}`; returns matching paragraphs with pages.
- `read_page`: args `{{"page": 3, "max_chars": 12000}}`; returns page text and tables.
- `list_tables`: table IDs, dimensions and header rows.
- `read_table`: args `{{"table_id": "3.1"}}`; returns a markdown table.
- `read_table` and `read_page` are the authoritative sources for table fields.
- `calculate`: args `{{"expression": "(8.54/141.39)*100"}}`; safe arithmetic.
- `verify_evidence`: args `{{"records": [ ... ]}}`; checks proposed page/quote pairs before submit.
  Use it once before submitting and fix any `ungrounded` entries.

## Action format
Return only one JSON object. To call a tool:
{{"thought": "why this step", "action": "read_table", "action_input": {{"table_id": "3.1"}}}}

To finish:
{{"thought": "inventory is complete", "action": "submit", "action_input": {{
  "inventory": ["sample label 1", "sample label 2"],
  "records": [
    {{
      "label": "sample label 1",
      "role": "parent",
      "parent_label": null,
      "fields": {{
        "assembly_method": "sol-gel",
        "solution_system": "H2O:EtOH=1:1",
        "acid_base_concentration": "HCl 1.0 w%",
        "surfactant": "Pluronic P123",
        "surfactant_concentration": "2.35 w%",
        "silica_source": "TEOS 6.04 w%",
        "salt": null,
        "synthesis_temperature": "55 °C 24 h + 80 °C 48 h",
        "mesostructure": "2D hexagonal (p6mm)",
        "surface_area": "720",
        "pore_size": "4.3",
        "morphology": "fibers"
      }},
      "evidence": {{"surface_area": [{{"page": 4, "quote": "The BET surface area was 720 m2/g."}}]}},
      "confidence": {{"surface_area": 0.95}}
    }}
  ]
}}}}

You are on step {step} of at most {max_steps}. Inspect at least one page or table
before submitting. Call `verify_evidence` on your final records, then submit. If no
synthesis exists, submit an empty records list after checking.
"""


class AgentError(RuntimeError):
    """Raised when the bounded agent cannot produce a submission."""


def extract_paper_agent(
    paper: PaperText,
    llm: OpenAICompatibleLLM,
    *,
    max_steps: int = 8,
    candidates: list[dict[str, Any]] | None = None,
) -> ExtractionRun:
    toolbox = ToolBox(paper)
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {
            "role": "user",
            "content": build_agent_task(
                paper, step=1, max_steps=max_steps, candidates=candidates
            ),
        },
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
            raise AgentError(f"agent LLM failed: {exc}") from exc
        model = response.model
        usage_items.append(usage_dict(response))
        action = parsed.get("action")
        arguments = parsed.get("action_input") or parsed.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        thought = str(parsed.get("thought") or "")[:500]

        if action == "submit":
            if not inspected:
                messages += [
                    {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            "You must inspect at least one page or table before submitting. "
                            "Call list_tables/read_page/search_pages, then submit."
                        ),
                    },
                ]
                continue
            records, record_issues = records_from_response(
                paper, arguments, model=response.model
            )
            if not records and len(tool_calls) < 2:
                messages += [
                    {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            "No records were submitted after too little inspection. "
                            "Check the synthesis table/pages and submit again."
                        ),
                    },
                ]
                continue
            coverage = _evidence_coverage(records)
            if coverage < 0.7 and step < max_steps:
                messages += [
                    {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            f"Evidence coverage is only {coverage:.0%}. Every non-null field "
                            "needs a page-level quote. Re-read the relevant pages/tables with tools "
                            "and resubmit with exact evidence for all non-null fields."
                        ),
                    },
                ]
                issues.append(f"agent evidence coverage before repair: {coverage:.0%}")
                continue
            issues.extend(record_issues)
            if coverage < 0.7:
                issues.append(f"agent submitted with low evidence coverage: {coverage:.0%}")
            return ExtractionRun(
                records=strip_internal_keys(records),
                issues=issues,
                context={"chars": len(paper.full_text), "pages": list(range(1, len(paper.pages) + 1)), "tables": len(paper.tables)},
                usage=merge_usage(usage_items),
                elapsed_s=time.time() - start,
                model=model,
                mode="agent",
                steps=step,
                tool_calls=tool_calls,
            )

        if not isinstance(action, str):
            observation = "invalid action: expected a string"
        else:
            try:
                result = toolbox.call(action, arguments)
                if action in {"read_page", "read_table", "search_pages", "list_tables"}:
                    inspected = True
                tool_calls.append(
                    {
                        "step": step,
                        "tool": action,
                        "arguments": arguments,
                        "thought": thought,
                    }
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

    raise AgentError(f"agent did not submit within {max_steps} steps")


def _evidence_coverage(records: list[dict[str, Any]]) -> float:
    non_null = 0
    covered = 0
    for record in records:
        fields = record.get("fields") or {}
        evidence = record.get("evidence") or {}
        for key, value in fields.items():
            if value is not None:
                non_null += 1
                if key in evidence:
                    covered += 1
    return covered / non_null if non_null else 1.0
