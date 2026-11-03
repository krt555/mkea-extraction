"""Self-checks for extraction output (contract, duplicates, evidence coverage)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .schema import FIELD_KEYS

REQUIRED_TOP_LEVEL = {
    "paper_id",
    "paper_key",
    "record_id",
    "record_index",
    "pdf_path",
    "fields",
}


def validate_records(
    records: list[dict[str, Any]],
    *,
    expected_papers: set[int] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    papers: set[int] = set()
    field_non_null = Counter()
    evidence_fields = 0
    non_null_fields = 0

    for index, record in enumerate(records, 1):
        missing = REQUIRED_TOP_LEVEL - set(record)
        if missing:
            errors.append(f"record {index}: missing top-level keys {sorted(missing)}")
            continue
        record_id = str(record["record_id"])
        if record_id in seen_ids:
            errors.append(f"duplicate record_id: {record_id}")
        seen_ids.add(record_id)
        if not re.fullmatch(r"paper_\d{3}_r\d{2}", record_id):
            errors.append(f"bad record_id format: {record_id}")
        paper_key = str(record["paper_key"])
        if not re.fullmatch(r"paper_\d{3}", paper_key):
            errors.append(f"bad paper_key format: {paper_key}")
        if str(record["pdf_path"]) != f"papers/{paper_key}.pdf":
            warnings.append(f"{record_id}: pdf_path does not match paper_key")
        papers.add(int(record["paper_id"]))
        fields = record.get("fields")
        if not isinstance(fields, dict):
            errors.append(f"{record_id}: fields is not an object")
            continue
        unknown = set(fields) - set(FIELD_KEYS)
        missing_fields = set(FIELD_KEYS) - set(fields)
        if unknown:
            errors.append(f"{record_id}: unknown field keys {sorted(unknown)}")
        if missing_fields:
            warnings.append(f"{record_id}: missing field keys {sorted(missing_fields)}")
        evidence = record.get("evidence") or {}
        if not isinstance(evidence, dict):
            warnings.append(f"{record_id}: evidence is not an object")
            evidence = {}
        for key in FIELD_KEYS:
            if fields.get(key) is not None:
                field_non_null[key] += 1
                non_null_fields += 1
        for key in evidence:
            if key in FIELD_KEYS and fields.get(key) is not None:
                evidence_fields += 1

    if expected_papers is not None:
        missing_papers = expected_papers - papers
        if missing_papers:
            warnings.append(f"papers without records: {sorted(missing_papers)}")

    coverage = evidence_fields / non_null_fields if non_null_fields else 0.0
    return {
        "records": len(records),
        "papers": len(papers),
        "unique_record_ids": len(seen_ids),
        "non_null_fields": non_null_fields,
        "evidence_fields": evidence_fields,
        "evidence_coverage": round(coverage, 4),
        "field_support": dict(sorted(field_non_null.items())),
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }
