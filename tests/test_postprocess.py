"""Tests for extraction-agent post-processing."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.pdf import PaperText
from mkea_extract.postprocess import (
    normalise_evidence,
    normalise_value,
    records_from_response,
    strip_internal_keys,
)
from mkea_extract.schema import FIELD_KEYS


class PostprocessTests(TestCase):
    def test_units_and_null(self):
        self.assertEqual(normalise_value("pore_size", "260 Å"), "26")
        self.assertEqual(normalise_value("pore_size", "64 Å"), "6.4")
        self.assertEqual(normalise_value("pore_size", "2-8 nm"), "2-8")
        self.assertEqual(normalise_value("pore_size", "4.3 nm"), "4.3")
        self.assertEqual(normalise_value("surface_area", "720 m2/g"), "720")
        self.assertEqual(normalise_value("surface_area", ">1000 m² g-1"), ">1000")
        self.assertEqual(normalise_value("surfactant_concentration", "0.0235"), "2.35 w%")
        self.assertEqual(normalise_value("surfactant_concentration", "2.35 wt%"), "2.35 w%")
        self.assertIsNone(normalise_value("salt", "无"))
        self.assertIsNone(normalise_value("salt", "0"))
        self.assertEqual(
            normalise_value("synthesis_temperature", "80 ℃ aging 4 h"),
            "80 °C aging 4 h",
        )
        self.assertEqual(
            normalise_value("mesostructure", "cubic (Im3hm)"),
            "cubic Im3m",
        )
        self.assertEqual(
            normalise_value("assembly_method", "sol-gel / block-copolymer self-assembly"),
            "sol-gel; soft template",
        )
        self.assertEqual(
            normalise_value("mesostructure", "2D hexagonal (SBA-15, p6mm)"),
            "2D hexagonal (p6mm)",
        )

    def test_evidence_is_grounded(self):
        pages = [
            "Title\n\nPluronic P123 (1.92 g) was dissolved in 45 g water.",
            "The BET surface area was 720 m2/g and the pore size was 4.3 nm.",
        ]
        evidence, issues = normalise_evidence(
            {
                "surfactant": [{"page": 1, "quote": "Pluronic P123 (1.92 g) was dissolved"}],
                "surface_area": [{"page": 2, "quote": "BET surface area was 720 m2/g"}],
                "pore_size": [{"page": 2, "quote": "not present in this paper"}],
            },
            pages,
        )
        self.assertEqual(set(evidence), {"surfactant", "surface_area"})
        self.assertEqual(evidence["surfactant"][0]["page"], 1)
        self.assertTrue(any("ungrounded" in issue for issue in issues))

    def test_records_are_contract_shaped(self):
        paper = PaperText(
            paper_id=999,
            path=Path("papers/paper_999.pdf"),
            pages=["The BET surface area was 720 m2/g."],
        )
        parsed = {
            "records": [
                {
                    "label": "S1",
                    "fields": {
                        "surface_area": "720 m2/g",
                        "pore_size": "4.3 nm",
                        "surfactant": "P123",
                    },
                    "evidence": {
                        "surface_area": [{"page": 1, "quote": "BET surface area was 720 m2/g"}],
                        "surfactant": [{"page": 1, "quote": "invented"}],
                    },
                    "confidence": {"surface_area": 1.5},
                }
            ]
        }
        records, issues = records_from_response(paper, parsed, model="test-model")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["record_id"], "paper_999_r01")
        self.assertEqual(set(record["fields"]), set(FIELD_KEYS))
        self.assertEqual(record["fields"]["surface_area"], "720")
        self.assertEqual(record["confidence"]["surface_area"], 1.0)
        self.assertIn("surface_area", record["evidence"])
        self.assertTrue(any("ungrounded" in issue for issue in issues))
        public = strip_internal_keys(records)[0]
        self.assertNotIn("label", public)
        self.assertNotIn("model", public)

    def test_empty_records_are_dropped(self):
        paper = PaperText(paper_id=998, path=Path("papers/paper_998.pdf"), pages=["text"])
        records, issues = records_from_response(
            paper, {"records": [{"fields": {"salt": None}}]}
        )
        self.assertEqual(records, [])
        self.assertTrue(any("no extracted fields" in issue for issue in issues))

    def test_duplicate_synthesis_records_are_collapsed(self):
        paper = PaperText(
            paper_id=997,
            path=Path("papers/paper_997.pdf"),
            pages=["The BET surface area was 720 m2/g."],
        )
        base = {
            "assembly_method": "sol-gel",
            "surfactant": "P123",
            "silica_source": "TEOS",
            "synthesis_temperature": "80 °C 24 h",
        }
        parsed = {
            "records": [
                {"label": "S1", "fields": {**base, "surface_area": None}},
                {"label": "S2", "fields": {**base, "surface_area": "720"}},
            ]
        }
        records, issues = records_from_response(paper, parsed)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["fields"]["surface_area"], "720")
        self.assertTrue(any("duplicate synthesis" in issue for issue in issues))

    def test_derived_and_literature_records_are_filtered(self):
        paper = PaperText(paper_id=996, path=Path("papers/paper_996.pdf"), pages=["text"])
        base = {"surfactant": "P123", "silica_source": "TEOS"}
        parsed = {
            "records": [
                {"label": "parent", "role": "parent", "fields": base},
                {
                    "label": "loaded",
                    "role": "derived",
                    "parent_label": "parent",
                    "fields": {**base, "morphology": "Au-loaded"},
                },
                {
                    "label": "commercial",
                    "role": "literature",
                    "fields": {"surfactant": "SBA-15"},
                },
            ]
        }
        records, issues = records_from_response(paper, parsed)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["fields"]["surfactant"], "P123")
        self.assertTrue(any("dropped 2" in issue for issue in issues))
