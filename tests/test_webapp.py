"""Tests for the MesoScope web layer.

Offline only: no network, no LLM.  Covers the rule engine's correctness
guarantees, evidence grounding, retrieval ranking and the export contract.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from webapp.app.demo import DEMO_PAPERS
from webapp.app.heuristic import extract_records_heuristic
from webapp.app.qa import Retriever, best_snippet, expand_query


SBA15 = DEMO_PAPERS[0]["pages"]
SBA16 = DEMO_PAPERS[1]["pages"]
FILM = DEMO_PAPERS[2]["pages"]


class RuleEngineTest(unittest.TestCase):
    def fields(self, pages):
        records, _ = extract_records_heuristic(pages)
        self.assertTrue(records, "rule engine returned no record")
        return records[0]["fields"]

    def test_identifies_surfactant_and_silica_source(self):
        fields = self.fields(SBA15)
        self.assertEqual(fields["surfactant"], "Pluronic P123")
        self.assertEqual(fields["silica_source"], "TEOS")

    def test_reads_results_not_introduction(self):
        """The intro mentions "below 4 nm" for CTAB; the product is 7.8 nm."""
        fields = self.fields(SBA15)
        self.assertEqual(fields["pore_size"], "7.8")
        self.assertEqual(fields["surface_area"], "820")

    def test_temperature_programme_is_ordered_with_durations(self):
        value = self.fields(SBA15)["synthesis_temperature"]
        self.assertIn("550 °C calcination 6 h", value)
        self.assertIn("aging 20 h", value)

    def test_concentration_is_not_cross_assigned(self):
        """"120 g of 2.0 M HCl" must not become the surfactant concentration."""
        fields = self.fields(SBA15)
        self.assertEqual(fields["acid_base_concentration"], "HCl 2.0 M")
        self.assertIsNone(fields["surfactant_concentration"])

    def test_mass_fraction_is_preferred_and_attributed(self):
        fields = self.fields(FILM)
        self.assertEqual(fields["surfactant_concentration"], "4.5 w%")
        self.assertEqual(fields["surfactant"], "CTAB")

    def test_salt_with_concentration(self):
        self.assertEqual(self.fields(SBA16)["salt"], "KCl 2.0 w%")

    def test_no_salt_reported_stays_null(self):
        self.assertIsNone(self.fields(SBA15)["salt"])

    def test_mesostructure_and_named_family(self):
        self.assertEqual(self.fields(SBA16)["mesostructure"], "cubic Im3m")
        self.assertEqual(self.fields(FILM)["mesostructure"], "2D hexagonal (p6mm)")

    def test_morphology_describes_product_not_micelles(self):
        """F127 favours "spherical micelles"; the product is spherical particles."""
        value = self.fields(SBA16)["morphology"]
        self.assertIn("spheres", value)

    def test_solution_system_volume_ratio(self):
        self.assertEqual(self.fields(SBA16)["solution_system"], "H2O:EtOH = 1:1")

    def test_every_filled_field_has_grounded_evidence(self):
        for pages in (SBA15, SBA16, FILM):
            records, _ = extract_records_heuristic(pages)
            record = records[0]
            joined = [" ".join(page.split()).lower() for page in pages]
            for key, value in record["fields"].items():
                if not value:
                    continue
                items = record["evidence"].get(key)
                self.assertTrue(items, f"{key} has a value but no evidence")
                for item in items:
                    quote = " ".join(item["quote"].split()).lower()
                    self.assertIn(
                        quote,
                        joined[item["page"] - 1],
                        f"{key} quote not found on page {item['page']}",
                    )

    def test_non_chemistry_text_yields_no_record(self):
        records, issues = extract_records_heuristic(
            ["This paper discusses macroeconomic policy and interest rates."]
        )
        self.assertEqual(records, [])
        self.assertTrue(issues)

    def test_angstrom_is_converted_to_nanometres(self):
        pages = [
            "Experimental. CTAB and TEOS were used. "
            "The calcined sample has a BET surface area of 900 m2/g. "
            "The BJH pore size distribution is centred at a pore diameter of 38 A."
        ]
        self.assertEqual(extract_records_heuristic(pages)[0][0]["fields"]["pore_size"], "3.8")


class RetrievalTest(unittest.TestCase):
    def build(self):
        chunks = []
        for index, item in enumerate(DEMO_PAPERS, 1):
            for page, text in enumerate(item["pages"], 1):
                chunks.append(
                    {
                        "paper_fk": index,
                        "paper_key": f"paper_{index:03d}",
                        "title": item["title"],
                        "page": page,
                        "text": text,
                    }
                )
        return Retriever(chunks)

    def test_chinese_query_maps_to_english_terms(self):
        tokens = expand_query("煅烧温度是多少？")
        self.assertIn("calcined", tokens)
        self.assertIn("temperature", tokens)

    def test_calcination_question_hits_experimental_page(self):
        hits = self.build().search("煅烧温度和时间是多少？", top_k=1)
        self.assertEqual(hits[0]["page"], 2)
        self.assertIn("calcined", hits[0]["snippet"].lower())

    def test_bet_question_returns_the_measured_value(self):
        hits = self.build().search("比表面积是多少？", top_k=1)
        self.assertIn("surface area", hits[0]["snippet"].lower())

    def test_salt_question_finds_the_only_paper_using_salt(self):
        hits = self.build().search("KCl 起什么作用？", top_k=1)
        self.assertEqual(hits[0]["paper_key"], "paper_002")

    def test_unrelated_question_returns_nothing(self):
        self.assertEqual(self.build().search("股票市场的收益率", top_k=3), [])

    def test_snippet_centres_on_the_query_terms(self):
        text = ("filler " * 200) + "the BET surface area was 820 m2/g" + (" filler" * 200)
        snippet = best_snippet(text, ["bet", "surface", "area", "820"])
        self.assertIn("820", snippet)


class ExportContractTest(unittest.TestCase):
    def test_records_validate_against_the_project_contract(self):
        from mkea_extract.validation import validate_records

        records = []
        for index, item in enumerate(DEMO_PAPERS, 1):
            extracted, _ = extract_records_heuristic(item["pages"])
            key = f"paper_{index:03d}"
            for position, record in enumerate(extracted, 1):
                records.append(
                    {
                        "paper_id": index,
                        "paper_key": key,
                        "record_id": f"{key}_r{position:02d}",
                        "record_index": position,
                        "pdf_path": f"papers/{key}.pdf",
                        "fields": record["fields"],
                        "evidence": record["evidence"],
                        "confidence": record["confidence"],
                    }
                )
        report = validate_records(records)
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["ok"])
        self.assertEqual(report["evidence_coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
