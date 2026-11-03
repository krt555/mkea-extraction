"""Field contract shared by the extraction prompt and the validator."""

from __future__ import annotations

FIELD_LABELS: dict[str, str] = {
    "assembly_method": "组装方式",
    "solution_system": "合成溶液体系（体积比）",
    "acid_base_concentration": "酸/碱浓度",
    "surfactant": "表面活性剂",
    "surfactant_concentration": "表面活性剂浓度",
    "silica_source": "硅源/浓度",
    "salt": "盐种类/浓度",
    "synthesis_temperature": "合成温度",
    "mesostructure": "产物介孔结构",
    "surface_area": "比表面积(m²/g)",
    "pore_size": "孔径分布(nm)",
    "morphology": "产物形态",
}

FIELD_DESCRIPTIONS: dict[str, str] = {
    "assembly_method": "Dominant assembly route (sol-gel, soft template, hydrothermal, interfacial, ...).",
    "solution_system": "Solvent system and volume ratios (e.g. H2O:EtOH=1:1).",
    "acid_base_concentration": "Acid/base identity and concentration, including pH if no concentration is given.",
    "surfactant": "Surfactant / structure-directing agent names, including mixtures and EO-PO formulas.",
    "surfactant_concentration": "Surfactant mass fraction, preferably as w%.",
    "silica_source": "Silica precursor identity and mass fraction or Si/Si-source ratio.",
    "salt": "Added salt identity and concentration; null when no salt is added or reported.",
    "synthesis_temperature": "Temperature programme with times/steps (aging, drying, hydrothermal, calcination).",
    "mesostructure": "Mesostructure / space group / material family (e.g. 2D hexagonal (p6mm), cubic Im3m).",
    "surface_area": "BET surface area in m2/g; preserve ranges and lower/upper bounds.",
    "pore_size": "Pore diameter or distribution in nm; convert Å to nm.",
    "morphology": "Particle shape, size, orientation, hollow/core-shell features.",
}

FIELD_KEYS: tuple[str, ...] = tuple(FIELD_LABELS)

FIELD_ALIASES: dict[str, str] = {
    key: key for key in FIELD_KEYS
}
for key, label in FIELD_LABELS.items():
    FIELD_ALIASES[label] = key

NULL_EQUIVALENTS = {
    "",
    "null",
    "none",
    "n/a",
    "na",
    "nan",
    "-",
    "--",
    "—",
    "not reported",
    "not measured",
    "未报道",
    "未给出",
    "未测",
    "无",
}
