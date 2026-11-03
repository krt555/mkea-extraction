"""Hybrid extraction: agent discovers samples, full context fills fields."""

from __future__ import annotations

from .inventory_agent import InventoryError, discover_inventory
from .llm import OpenAICompatibleLLM
from .pdf import PaperText
from .pipeline import ExtractionRun, extract_paper, merge_usage


def extract_paper_hybrid(
    paper: PaperText,
    llm: OpenAICompatibleLLM,
    *,
    max_steps: int = 8,
    max_chars: int = 200000,
) -> ExtractionRun:
    try:
        inventory = discover_inventory(paper, llm, max_steps=max_steps)
        run = extract_paper(
            paper,
            llm,
            max_chars=max_chars,
            inventory=inventory.samples,
        )
        run.mode = "hybrid"
        run.steps = inventory.steps + run.steps
        run.tool_calls = inventory.tool_calls + run.tool_calls
        run.usage = merge_usage([inventory.usage, run.usage])
        run.issues = inventory.issues + run.issues
        run.context["inventory"] = len(inventory.samples)
        return run
    except InventoryError as exc:
        run = extract_paper(paper, llm, max_chars=max_chars)
        run.mode = "hybrid-fallback"
        run.issues.insert(0, f"inventory agent fallback: {exc}")
        return run
