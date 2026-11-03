"""Tests for the bounded tool-using extraction agent."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.agent import AgentError, extract_paper_agent
from mkea_extract.llm import LLMResponse
from mkea_extract.pdf import PaperTable, PaperText


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, messages, **kwargs):
        self.calls += 1
        payload = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        return payload, LLMResponse(
            text="{}",
            model="fake-agent",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            latency_s=0.01,
        )


def paper() -> PaperText:
    return PaperText(
        paper_id=3,
        path=Path("papers/paper_003.pdf"),
        pages=[
            "Synthesis table and experimental details.\n\nThe BET surface area was 720 m2/g.",
            "Pore size was 4.3 nm.",
        ],
        tables=[
            PaperTable(
                page=1,
                index=1,
                rows=[["Sample", "BET", "Pore"], ["S1", "720", "4.3"]],
            )
        ],
    )


def submit_payload():
    return {
        "thought": "finished",
        "action": "submit",
        "action_input": {
            "inventory": ["S1"],
            "records": [
                {
                    "label": "S1",
                    "role": "parent",
                    "fields": {
                        "surfactant": "P123",
                        "surface_area": "720",
                        "pore_size": "4.3",
                    },
                    "evidence": {
                        "surface_area": [
                            {"page": 1, "quote": "BET surface area was 720 m2/g"}
                        ],
                        "surfactant": [
                            {"page": 1, "quote": "Synthesis table and experimental details"}
                        ],
                        "pore_size": [
                            {"page": 2, "quote": "Pore size was 4.3 nm"}
                        ]
                    },
                    "confidence": {"surface_area": 0.9},
                }
            ],
        },
    }


class AgentTests(TestCase):
    def test_agent_uses_tools_then_submits(self):
        llm = ScriptedLLM(
            [
                {"thought": "inspect tables", "action": "list_tables", "action_input": {}},
                {
                    "thought": "read table",
                    "action": "read_table",
                    "action_input": {"table_id": "1.1"},
                },
                submit_payload(),
            ]
        )
        run = extract_paper_agent(paper(), llm, max_steps=5)  # type: ignore[arg-type]
        self.assertEqual(run.mode, "agent")
        self.assertEqual(run.steps, 3)
        self.assertEqual(len(run.tool_calls), 2)
        self.assertEqual(run.records[0]["fields"]["surface_area"], "720")
        self.assertEqual(llm.calls, 3)

    def test_submit_before_inspection_is_rejected(self):
        llm = ScriptedLLM(
            [
                submit_payload(),
                {"thought": "inspect", "action": "read_page", "action_input": {"page": 1}},
                submit_payload(),
            ]
        )
        run = extract_paper_agent(paper(), llm, max_steps=4)  # type: ignore[arg-type]
        self.assertEqual(run.steps, 3)
        self.assertEqual(len(run.records), 1)

    def test_max_steps_raises(self):
        llm = ScriptedLLM([{"action": "list_pages", "action_input": {}}])
        with self.assertRaises(AgentError):
            extract_paper_agent(paper(), llm, max_steps=2)  # type: ignore[arg-type]

    def test_tool_error_is_observed(self):
        llm = ScriptedLLM(
            [
                {"action": "read_table", "action_input": {"table_id": "9.9"}},
                {"action": "read_page", "action_input": {"page": 1}},
                submit_payload(),
            ]
        )
        run = extract_paper_agent(paper(), llm, max_steps=4)  # type: ignore[arg-type]
        self.assertTrue(run.tool_calls[0].get("error"))
        self.assertEqual(len(run.records), 1)
