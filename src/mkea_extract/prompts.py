"""Prompt construction for record discovery and schema-guided extraction."""

from __future__ import annotations

from .context import PaperContext
from .pdf import PaperText
from .schema import FIELD_DESCRIPTIONS, FIELD_KEYS, FIELD_LABELS

SYSTEM_PROMPT = """You are a materials-science knowledge extraction agent.
You read one paper and return structured synthesis records as strict JSON.
Never invent values, never read or assume a gold answer, and use null when the
paper does not report a field.  Evidence quotes must be verbatim text from the
provided page content."""


def build_messages(
    paper: PaperText,
    context: PaperContext,
    inventory: list[dict[str, object]] | None = None,
) -> list[dict[str, str]]:
    field_lines = "\n".join(
        f"- {key} ({FIELD_LABELS[key]}): {FIELD_DESCRIPTIONS[key]}" for key in FIELD_KEYS
    )
    skeleton = ""
    if inventory:
        lines = []
        for index, sample in enumerate(inventory, 1):
            if isinstance(sample, dict):
                label = sample.get("label") or f"sample_{index}"
                role = sample.get("role") or "parent"
                conditions = sample.get("conditions") or ""
                lines.append(f"{index}. {label} | role={role} | {conditions}")
            else:
                lines.append(f"{index}. {sample} | role=parent")
        skeleton = (
            "\n## Sample inventory (from the discovery pass)\n"
            + "\n".join(lines)
            + "\nReturn exactly one record per inventory item, with the same label and role. "
            "Do not add or remove samples unless the evidence clearly contradicts the inventory.\n"
        )

    user = f"""Extract every distinct synthesis/sample record from the paper below.

PAPER: {paper.paper_key} ({paper.path.name})
PAGES PROVIDED: {", ".join(str(p) for p in context.pages)}
{skeleton}

## Fields (use exactly these English keys)
{field_lines}

## Scope
- Extract only materials actually synthesized in this paper with a mesoporous silica/organosilica framework.
- Exclude literature examples, review tables, commercial/purchased supports, and supports prepared by a cited procedure when this paper gives no synthesis details.
- Exclude downstream metal-loaded or grafted catalysts made from an already-counted support.
- If the paper synthesizes a parent silica and then grafts/activates it, return one record for the final functionalized material; put the post-treatment in `synthesis_temperature`. Do not duplicate the parent and the product.
- Do not create a separate record for an isotopic solvent swap, in-situ measurement cell, or other characterization-only variant of the same sample.
- Do not create records for each characterization technique, measurement temperature, or property table; only distinct synthesis products.
- If the paper reports no new synthesis, return an empty `records` list.

## Record role
For every record, set `role`:
- `parent`: direct synthesis of a mesoporous silica/organosilica material in this work;
- `derived`: a composite, metal-loaded, grafted, or calcination/post-treatment variant of a parent record;
- `literature`: a cited or commercial sample that this paper did not synthesize.
For `derived`, set `parent_label` to the matching parent record label when possible.

## Record inventory
- First identify the primary synthesis table/figure and its sample IDs. Use one record per distinct sample/condition row in that inventory.
- If the paper has a table of samples with conditions and properties, use its rows as the record skeleton. Do not add extra prose-only temperatures/conditions unless the text explicitly reports a distinct mesostructure or numeric property (BET/pore size) for that condition.
- Do not create a record merely because one sample was measured by BET, XRD, TEM, or catalysis. One sample = one record.
- Every record must have a sample label from the paper and at least one synthesis condition.
- In the JSON, first output `inventory`: an array with one short label per sample. Then output `records` with exactly one object per inventory label. The two lengths must match. Build the inventory before filling fields; do not collapse a multi-row synthesis table into one record.

## Record splitting
- One record = one distinguishable sample or synthesis condition.
- Split condition matrices (different surfactant, salt, temperature, silica source, post-treatment, or sample ID) into separate records.
- Keep a multi-step temperature programme (aging + drying + calcination) inside one `synthesis_temperature` value; do not split it into records.
- A paper may report one sample in text and a table of many samples; use the table's sample IDs and rows as the authoritative record list.
- If the paper only reports a trend without a complete recipe, still create a record and use null for missing fields.

## Value rules
- Values must be compact database fields, not prose. Do not include explanations, citations, gram amounts, or calculation narratives in the field value.
- `assembly_method`: one short route label, e.g. "sol-gel", "acidic soft template", "alkaline soft template", "hydrothermal", "interfacial", "microemulsion".
- `surfactant`: canonical name only (e.g. "Pluronic P123", "CTAB"); omit masses and long EO/PO formulas.
- `solution_system`: ratio notation, e.g. "H2O:EtOH=1:1"; do not write reagent masses.
- `acid_base_concentration`: entity + value/unit or pH, e.g. "HCl 1.0 w%" / "pH ≈1"; no recipe narrative.
- `silica_source`: precursor + concentration, e.g. "TEOS 6.04 w%"; no masses.
- `synthesis_temperature`: compact programme, e.g. "55 °C 24 h + 80 °C 48 h + 550 °C 6 h".
- `mesostructure`: phase and space group only, e.g. "2D hexagonal (p6mm)", "cubic Ia3d"; no characterization description.
- `morphology`: shape + size only, e.g. "spherical particles, 200-500 nm".
- Preserve reported ranges, approximations, and inequalities (e.g. "2-8", ">1000").
- Prefer mass fractions in `w%`; convert `wt%` to `w%`. If only a bare mass fraction is given, multiply by 100 and append `w%`.
- Convert Å to nm for `pore_size` (1 Å = 0.1 nm).
- For `surface_area`, give m2/g without the unit text.
- Do not write 0 for an unmeasured property; use null.
- `salt` is only for deliberately added inorganic/organic salts; use null when no salt is added or reported.
- Keep entity names as written in the paper (English is fine). Do not translate chemical names.

## Evidence
For every non-null field, add one evidence entry (not a list of many passages):
`{{"page": <page number from [[PAGE n]]>, "quote": "<verbatim sentence or table row>"}}`
The quote must appear in the page text. Keep quotes short (<= 300 characters).
If a value is derived by calculation, quote the recipe/table rows used and set confidence lower.

## Output (strict JSON, no markdown)
{{
  "inventory": ["sample label 1", "sample label 2"],
  "records": [
    {{
      "label": "short sample label from the paper",
      "role": "parent",
      "parent_label": null,
      "fields": {{
        "assembly_method": "... or null",
        "solution_system": "... or null",
        "acid_base_concentration": "... or null",
        "surfactant": "... or null",
        "surfactant_concentration": "... or null",
        "silica_source": "... or null",
        "salt": "... or null",
        "synthesis_temperature": "... or null",
        "mesostructure": "... or null",
        "surface_area": "... or null",
        "pore_size": "... or null",
        "morphology": "... or null"
      }},
      "evidence": {{
        "surface_area": [{{"page": 4, "quote": "The BET surface area was 720 m2/g."}}]
      }},
      "confidence": {{"surface_area": 0.95}}
    }}
  ]
}}

Return an empty `records` list only when the paper contains no synthesis record.
Do not wrap the JSON in code fences.

## Paper text
{context.text}
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
