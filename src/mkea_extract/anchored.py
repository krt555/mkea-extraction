"""Anchored extraction: full-text candidates followed by an agent audit.

The one-shot pass gives broad field coverage; the agent starts from that
candidate list and uses tools to merge over-split records, add missing samples
and correct weak fields.  This avoids asking the agent to discover everything
from scratch under a small step budget.
"""

from __future__ import annotations

from .agent import AgentError, extract_paper_agent
from .llm import OpenAICompatibleLLM
from .pdf import PaperText
from .pipeline import ExtractionRun, extract_paper, merge_usage


def extract_paper_anchored(
    paper: PaperText,
    llm: OpenAICompatibleLLM,
    *,
    max_steps: int = 8,
    max_chars: int = 200000,
) -> ExtractionRun:
    base = extract_paper(paper, llm, max_chars=max_chars)
    if not base.records:
        return base
    try:
        run = extract_paper_agent(
            paper,
            llm,
            max_steps=max_steps,
            candidates=base.records,
        )
    except AgentError as exc:
        base.mode = "anchored-fallback"
        base.issues.insert(0, f"anchored agent fallback: {exc}")
        return base

    if not run.records:
        base.mode = "anchored-fallback"
        base.issues.insert(0, "anchored agent returned no records; using full-text candidates")
        return base

    run.mode = "anchored"
    run.steps = base.steps + run.steps
    run.usage = merge_usage([base.usage, run.usage])
    run.issues = base.issues + run.issues
    run.context["candidates"] = len(base.records)
    return run
