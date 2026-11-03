"""Deterministic post-processing of model output.

The LLM proposes values; this module enforces the contract, normalises units,
and drops evidence that cannot be grounded in the page text.  Keeping these
rules outside the prompt makes the output reproducible even if the model
changes.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from .pdf import PaperText, ground_quote
from .schema import FIELD_ALIASES, FIELD_KEYS, NULL_EQUIVALENTS


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u202f", " ").replace("\u00a0", " ").replace("∼", "~")
    text = text.replace("℃", "°C").replace("º", "°")
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower() in NULL_EQUIVALENTS or text in NULL_EQUIVALENTS:
        return None
    return text or None


def normalise_fields(raw: dict[str, Any]) -> tuple[dict[str, str | None], list[str]]:
    fields: dict[str, str | None] = {key: None for key in FIELD_KEYS}
    unknown: list[str] = []
    for raw_key, value in (raw or {}).items():
        key = FIELD_ALIASES.get(str(raw_key).strip())
        if key is None:
            unknown.append(str(raw_key))
            continue
        fields[key] = normalise_value(key, value)
    return fields, unknown


def normalise_value(key: str, value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None

    if key == "salt" and text.lower() in {"无", "0", "0.0", "none", "null"}:
        return None
    if key == "surfactant_concentration" and re.fullmatch(r"0?\.\d+", text):
        text = f"{float(text) * 100:g} w%"
    if key in {
        "surfactant_concentration",
        "acid_base_concentration",
        "silica_source",
        "salt",
    }:
        text = re.sub(r"(?<=\d)\s*(wt%|w%)", r" w%", text)
        text = text.replace("wt%", "w%")
    if key == "solution_system":
        text = re.sub(r"\s*:\s*", ":", text)
        text = re.sub(r"\s*=\s*", "=", text)
        text = re.sub(r"\s*;\s*", "; ", text)
    if key == "assembly_method":
        text = _canonical_assembly(text)
    if key == "mesostructure":
        text = text.replace("Im3hm", "Im3m").replace("Ia3hd", "Ia3d")
        text = _canonical_mesostructure(text)
    if key == "synthesis_temperature":
        text = text.replace("℃", "°C")
    if key == "pore_size":
        text = _normalise_pore_size(text)
    if key == "surface_area":
        text = _normalise_surface_area(text)
    return text or None


def _canonical_assembly(text: str) -> str:
    lowered = text.lower()
    tags: list[str] = []
    if any(token in lowered for token in ("sol-gel", "sol gel", "gel-strand", "gel strand", "hydrolysis", "condensation")):
        tags.append("sol-gel")
    if any(token in lowered for token in ("soft template", "templating", "surfactant", "micelle", "copolymer")):
        tags.append("soft template")
    if "hydrothermal" in lowered:
        tags.append("hydrothermal")
    if "interfacial" in lowered or "interface" in lowered:
        tags.append("interfacial")
    if "microemulsion" in lowered:
        tags.append("microemulsion")
    if "hard template" in lowered or "nanocasting" in lowered:
        tags.append("hard template")
    return "; ".join(dict.fromkeys(tags)) or text


def _canonical_mesostructure(text: str) -> str:
    lowered = text.lower()
    if "3d hexagonal" in lowered:
        return "3D hexagonal"
    if "2d hexagonal" in lowered or "p6mm" in lowered or "sba-15" in lowered:
        return "2D hexagonal (p6mm)"
    if "ia3d" in lowered:
        return "cubic Ia3d"
    if "im3m" in lowered or "sba-16" in lowered:
        return "cubic Im3m"
    if "lamellar" in lowered:
        return "lamellar"
    if "disordered" in lowered or "worm" in lowered:
        return "disordered"
    if "hexagonal" in lowered:
        return "2D hexagonal (p6mm)"
    return text


def _normalise_pore_size(text: str) -> str:
    pattern = re.compile(
        r"(?<![\d.])(\d+(?:\.\d+)?(?:\s*[-–~]\s*\d+(?:\.\d+)?)?)\s*(?:Å|angstroms?)\b",
        flags=re.I,
    )

    def convert(match: re.Match[str]) -> str:
        numbers = re.findall(r"\d+(?:\.\d+)?", match.group(1))
        converted = [f"{float(number) / 10:g}" for number in numbers]
        return "-".join(converted)

    text = pattern.sub(convert, text)
    text = re.sub(r"(?i)\s*nm\b", "", text).strip()
    # A bare value above 100 is almost certainly Å in this literature.
    if re.fullmatch(r"\d+(?:\.\d+)?", text) and float(text) > 100:
        text = f"{float(text) / 10:g}"
    return text


def _normalise_surface_area(text: str) -> str:
    text = re.sub(
        r"(?i)\s*(?:m\s*2\s*[/·]?\s*g(?:-1|−1)?|m²\s*[/·]?\s*g(?:-1|−1)?|m\^2\s*/\s*g)\b",
        "",
        text,
    )
    text = re.sub(r"(?i)\bBET\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ;,")
    return text


def normalise_evidence(
    raw: Any,
    pages: list[str],
    *,
    known_fields: dict[str, str | None] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    evidence: dict[str, list[dict[str, Any]]] = {}
    issues: list[str] = []
    if not isinstance(raw, dict):
        return evidence, issues
    for raw_key, entries in raw.items():
        key = FIELD_ALIASES.get(str(raw_key).strip())
        if key is None:
            issues.append(f"unknown evidence field: {raw_key}")
            continue
        if known_fields is not None and not known_fields.get(key):
            continue
        if isinstance(entries, (str, dict)):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, str):
                page, quote = None, entry
            elif isinstance(entry, dict):
                page = _as_page(entry.get("page") or entry.get("page_number"))
                quote = entry.get("quote") or entry.get("text") or entry.get("evidence")
            else:
                continue
            quote = clean_text(quote)
            if not quote:
                continue
            grounded, matches = ground_quote(pages, quote, page=page)
            if not grounded:
                issues.append(f"ungrounded evidence dropped: {key}")
                continue
            cleaned.append(
                {
                    "page": page if page in matches else matches[0],
                    "quote": quote[:500],
                }
            )
        if cleaned:
            evidence[key] = cleaned[:3]
    return evidence, issues


def normalise_confidence(raw: Any) -> dict[str, float]:
    confidence: dict[str, float] = {}
    if not isinstance(raw, dict):
        return confidence
    for raw_key, value in raw.items():
        key = FIELD_ALIASES.get(str(raw_key).strip())
        if key is None:
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        confidence[key] = max(0.0, min(1.0, score))
    return confidence


def records_from_response(
    paper: PaperText,
    parsed: dict[str, Any],
    *,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_records = parsed.get("records")
    if raw_records is None and isinstance(parsed.get("fields"), dict):
        raw_records = [parsed]
    if isinstance(raw_records, dict):
        raw_records = [raw_records]
    if not isinstance(raw_records, list):
        return [], ["response does not contain a `records` list"]

    records: list[dict[str, Any]] = []
    issues: list[str] = []
    inventory = parsed.get("inventory")
    if not isinstance(inventory, list):
        inventory = []
    if inventory and len(inventory) != len(raw_records):
        issues.append(
            f"inventory/records length mismatch: {len(inventory)} vs {len(raw_records)}"
        )
    for raw in raw_records:
        if not isinstance(raw, dict):
            issues.append("dropped a non-object record")
            continue
        fields, unknown = normalise_fields(raw.get("fields") or {})
        if unknown:
            issues.append(f"unknown fields ignored: {', '.join(sorted(unknown))}")
        if not any(value is not None for value in fields.values()):
            issues.append("dropped a record with no extracted fields")
            continue
        evidence, evidence_issues = normalise_evidence(
            raw.get("evidence"), paper.pages_with_tables, known_fields=fields
        )
        issues.extend(evidence_issues)
        confidence = normalise_confidence(raw.get("confidence"))
        label = clean_text(raw.get("label") or raw.get("sample") or raw.get("name"))
        if not label and len(inventory) == len(raw_records):
            label = clean_text(inventory[len(records)])
        role = (clean_text(raw.get("role")) or "parent").lower()
        if role not in {"parent", "derived", "literature"}:
            role = "parent"
        records.append(
            {
                "paper_id": paper.paper_id,
                "paper_key": paper.paper_key,
                "pdf_path": f"papers/{paper.paper_key}.pdf",
                "fields": fields,
                "evidence": evidence,
                "confidence": confidence,
                "label": label or f"record_{len(records) + 1:02d}",
                "role": role,
                "parent_label": clean_text(raw.get("parent_label") or raw.get("parent")),
                "model": model,
            }
        )
    records, role_issues, dropped_roles = _filter_derived_records(records)
    issues.extend(role_issues)
    if dropped_roles:
        issues.append(f"dropped {dropped_roles} derived/literature records")
    records, duplicate_count = _collapse_duplicate_synthesis(records)
    if duplicate_count:
        issues.append(f"collapsed {duplicate_count} duplicate synthesis records")
    for index, record in enumerate(records, 1):
        record["record_index"] = index
        record["record_id"] = f"{paper.paper_key}_r{index:02d}"
    return records, issues


SYNTHESIS_KEYS = (
    "assembly_method",
    "solution_system",
    "acid_base_concentration",
    "surfactant",
    "surfactant_concentration",
    "silica_source",
    "salt",
    "synthesis_temperature",
)


def _filter_derived_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Drop cited/derived samples when a parent silica record is present."""

    parents = [record for record in records if record.get("role") == "parent"]
    if not parents:
        return records, [], 0
    parent_labels = {str(record.get("label") or "").strip().lower() for record in parents}
    parent_keys = {
        (str(record["fields"].get("surfactant") or "").lower(), str(record["fields"].get("silica_source") or "").lower())
        for record in parents
    }
    kept: list[dict[str, Any]] = []
    dropped = 0
    for record in records:
        role = record.get("role")
        if role == "parent":
            kept.append(record)
            continue
        if role == "literature":
            dropped += 1
            continue
        if role == "derived":
            parent_label = str(record.get("parent_label") or "").strip().lower()
            key = (
                str(record["fields"].get("surfactant") or "").lower(),
                str(record["fields"].get("silica_source") or "").lower(),
            )
            if parent_label in parent_labels or ((key[0] or key[1]) and key in parent_keys):
                dropped += 1
                continue
        kept.append(record)
    if not kept:
        return records, ["kept derived records because no parent record survived"], 0
    return kept, [], dropped


def _collapse_duplicate_synthesis(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Keep one record per identical synthesis recipe.

    Several papers report many composite/derived samples made from one parent
    silica recipe.  The benchmark's record unit is the synthesis condition, so
    exact recipe duplicates are collapsed; the record with the richest
    property set is kept.
    """

    kept: dict[tuple[str, ...], dict[str, Any]] = {}
    duplicates = 0
    for record in records:
        fields = record["fields"]
        signature = tuple(str(fields.get(key) or "") for key in SYNTHESIS_KEYS)
        if not any(signature):
            kept[tuple([f"__record_{len(kept)}"])] = record
            continue
        existing = kept.get(signature)
        if existing is None:
            kept[signature] = record
            continue
        duplicates += 1
        if _property_score(record) > _property_score(existing):
            kept[signature] = record
    return list(kept.values()), duplicates


def _property_score(record: dict[str, Any]) -> int:
    fields = record["fields"]
    return sum(1 for key in ("mesostructure", "surface_area", "pore_size", "morphology") if fields.get(key))


def strip_internal_keys(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove agent-only keys before writing the prediction contract."""

    public = []
    for record in records:
        payload = {
            key: record[key]
            for key in (
                "paper_id",
                "paper_key",
                "record_id",
                "record_index",
                "pdf_path",
                "fields",
            )
        }
        if record.get("evidence"):
            payload["evidence"] = record["evidence"]
        if record.get("confidence"):
            payload["confidence"] = record["confidence"]
        public.append(payload)
    return public


def _as_page(value: Any) -> int | None:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page >= 1 else None
