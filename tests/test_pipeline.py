"""End-to-end pipeline test with a scripted backend (offline)."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.llm import LLMResponse
from mkea_extract.pdf import PaperText
from mkea_extract.pipeline import extract_paper


class FakeLLM:
    def __init__(self, parsed):
        self.parsed = parsed
        self.messages = None

    def complete_json(self, messages):
        self.messages = messages
        return self.parsed, LLMResponse(
            text="{}",
            model="fake-model",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            latency_s=0.01,
        )


class RetryLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, messages):
        self.calls += 1
        if self.calls == 1:
            return {"records": []}, LLMResponse(text="{}", model="fake-model")
        return {
            "inventory": ["S1"],
            "records": [{"label": "S1", "fields": {"surface_area": "720"}}],
        }, LLMResponse(text="{}", model="fake-model")



class PipelineTests(TestCase):
    def test_extract_paper(self):
        paper = PaperText(
            paper_id=1,
            path=Path("papers/paper_001.pdf"),
            pages=[
                "Synthesis: Pluronic P123 (1.92 g) was dissolved in 45 g water and 30 g 4 M HCl.",
                "The BET surface area was 780 m2/g and the pore size was 6.4 nm.",
            ],
        )
        parsed = {
            "records": [
                {
                    "label": "P103",
                    "fields": {
                        "assembly_method": "sol-gel",
                        "surfactant": "Pluronic P103",
                        "surface_area": "780 m2/g",
                        "pore_size": "64 Å",
                    },
                    "evidence": {
                        "surface_area": [{"page": 2, "quote": "BET surface area was 780 m2/g"}],
                        "pore_size": [{"page": 2, "quote": "pore size was 6.4 nm"}],
                    },
                    "confidence": {"surface_area": 0.9},
                }
            ]
        }
        llm = FakeLLM(parsed)
        run = extract_paper(paper, llm)  # type: ignore[arg-type]
        self.assertEqual(len(run.records), 1)
        record = run.records[0]
        self.assertEqual(record["record_id"], "paper_001_r01")
        self.assertEqual(record["fields"]["pore_size"], "6.4")
        self.assertEqual(record["fields"]["surface_area"], "780")
        self.assertIn("P123", llm.messages[1]["content"])
        self.assertEqual(run.usage["total_tokens"], 150)
        self.assertEqual(run.model, "fake-model")

    def test_retry_on_empty_response(self):
        paper = PaperText(paper_id=2, path=Path("papers/paper_002.pdf"), pages=["text"])
        llm = RetryLLM()
        run = extract_paper(paper, llm)  # type: ignore[arg-type]
        self.assertEqual(llm.calls, 2)
        self.assertEqual(len(run.records), 1)
        self.assertTrue(any("retried" in issue for issue in run.issues))
