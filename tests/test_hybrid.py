"""Tests for the hybrid inventory + full-context extraction path."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.anchored import extract_paper_anchored
from mkea_extract.hybrid import extract_paper_hybrid
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
            model="fake-hybrid",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            latency_s=0.01,
        )


def paper() -> PaperText:
    return PaperText(
        paper_id=3,
        path=Path("papers/paper_003.pdf"),
        pages=["Synthesis table and details.\n\nThe BET surface area was 720 m2/g.", "Pore size was 4.3 nm."],
        tables=[PaperTable(page=1, index=1, rows=[["Sample", "BET"], ["S1", "720"]])],
    )


class HybridTests(TestCase):
    def test_inventory_then_full_context_fields(self):
        inventory = {
            "action": "submit_inventory",
            "action_input": {
                "inventory": [
                    {"label": "S1", "role": "parent", "conditions": "P123", "pages": [1]}
                ]
            },
        }
        fields = {
            "inventory": ["S1"],
            "records": [
                {
                    "label": "S1",
                    "role": "parent",
                    "fields": {"surfactant": "P123", "surface_area": "720", "pore_size": "4.3"},
                    "evidence": {
                        "surfactant": [{"page": 1, "quote": "Synthesis table and details"}],
                        "surface_area": [{"page": 1, "quote": "BET surface area was 720 m2/g"}],
                        "pore_size": [{"page": 2, "quote": "Pore size was 4.3 nm"}],
                    },
                    "confidence": {"surface_area": 0.9},
                }
            ],
        }
        llm = ScriptedLLM(
            [
                {"action": "list_tables", "action_input": {}},
                inventory,
                fields,
            ]
        )
        run = extract_paper_hybrid(paper(), llm, max_steps=4)  # type: ignore[arg-type]
        self.assertEqual(run.mode, "hybrid")
        self.assertEqual(run.records[0]["fields"]["surface_area"], "720")
        self.assertEqual(run.context["inventory"], 1)
        self.assertEqual(llm.calls, 3)
        self.assertEqual(run.steps, 3)

    def test_empty_inventory_falls_back_to_one_shot(self):
        empty = {
            "action": "submit_inventory",
            "action_input": {"inventory": []},
        }
        fields = {
            "records": [
                {
                    "label": "S9",
                    "fields": {"surface_area": "720"},
                    "evidence": {"surface_area": [{"page": 1, "quote": "BET surface area was 720 m2/g"}]},
                }
            ]
        }
        llm = ScriptedLLM(
            [
                {"action": "list_tables", "action_input": {}},
                {"action": "read_page", "action_input": {"page": 1}},
                empty,
                fields,
            ]
        )
        run = extract_paper_hybrid(paper(), llm, max_steps=4)  # type: ignore[arg-type]
        self.assertEqual(run.mode, "hybrid")
        self.assertEqual(len(run.records), 1)

    def test_anchored_agent_starts_from_candidates(self):
        base = {
            "records": [
                {
                    "label": "S1",
                    "role": "parent",
                    "fields": {"surface_area": "720"},
                    "evidence": {"surface_area": [{"page": 1, "quote": "BET surface area was 720 m2/g"}]},
                }
            ]
        }
        final = {
            "inventory": ["S1"],
            "records": [
                {
                    "label": "S1",
                    "role": "parent",
                    "fields": {"surface_area": "720", "pore_size": "4.3"},
                    "evidence": {
                        "surface_area": [{"page": 1, "quote": "BET surface area was 720 m2/g"}],
                        "pore_size": [{"page": 2, "quote": "Pore size was 4.3 nm"}],
                    },
                }
            ],
        }
        llm = ScriptedLLM(
            [
                base,
                {"action": "list_tables", "action_input": {}},
                {"action": "submit", "action_input": final},
            ]
        )
        run = extract_paper_anchored(paper(), llm, max_steps=4)  # type: ignore[arg-type]
        self.assertEqual(run.mode, "anchored")
        self.assertEqual(run.context["candidates"], 1)
        self.assertEqual(run.records[0]["fields"]["pore_size"], "4.3")
        self.assertEqual(run.steps, 3)

    def test_inventory_failure_falls_back(self):
        from mkea_extract.llm import LLMError

        class BrokenInventory:
            def __init__(self):
                self.calls = 0

            def complete_json(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise LLMError("inventory down")
                return {"records": []}, LLMResponse(text="{}", model="fake")

        run = extract_paper_hybrid(paper(), BrokenInventory(), max_steps=2)  # type: ignore[arg-type]
        self.assertEqual(run.mode, "hybrid-fallback")
        self.assertTrue(any("inventory agent fallback" in issue for issue in run.issues))
