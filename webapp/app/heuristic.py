"""Deterministic rule-based extractor.

Purpose: the app must always produce a reviewable, evidence-backed result, even
without an LLM API key.  This module scans the parsed page text with chemistry
specific patterns and emits records in the same contract as the LLM agent
(``fields`` / ``evidence`` / ``confidence``), so the whole UI works unchanged.

It is intentionally conservative: a field is only filled when a pattern matches,
and every filled field carries the page number and the sentence it came from.
"""

from __future__ import annotations

import re
from typing import Any

from mkea_extract.schema import FIELD_KEYS

SURFACTANTS = [
    (r"\bP\s?123\b|Pluronic\s*P\s?123|EO\s?20\s?PO\s?70\s?EO\s?20", "Pluronic P123"),
    (r"\bF\s?127\b|Pluronic\s*F\s?127|EO\s?106\s?PO\s?70\s?EO\s?106", "Pluronic F127"),
    (r"\bF\s?108\b|Pluronic\s*F\s?108", "Pluronic F108"),
    (r"\bP\s?65\b|Pluronic\s*P\s?65", "Pluronic P65"),
    (r"\bCTAB\b|cetyltrimethylammonium bromide", "CTAB"),
    (r"\bCTAC\b|cetyltrimethylammonium chloride", "CTAC"),
    (r"\bSDS\b|sodium dodecyl sulfate", "SDS"),
    (r"Brij\s*\d+", "Brij"),
    (r"\bTween\s*\d+", "Tween"),
    (r"\bPEO-?b-?PS\b", "PEO-b-PS"),
    (r"\bPS-?b-?PEO\b", "PS-b-PEO"),
]

SILICA_SOURCES = [
    (r"\bTEOS\b|tetraethyl\s*orthosilicate|tetraethoxysilane", "TEOS"),
    (r"\bTMOS\b|tetramethyl\s*orthosilicate", "TMOS"),
    (r"sodium\s+silicate|water\s*glass", "sodium silicate"),
    (r"\bTMB\b", None),  # swelling agent, not a silica source: matched to avoid false hits
    (r"fumed\s+silica", "fumed silica"),
    (r"\bMTES\b", "MTES"),
    (r"\bAPTES\b", "APTES"),
]

ACIDS = [
    (r"\bHCl\b|hydrochloric\s+acid", "HCl"),
    (r"\bHNO3\b|\bHNO₃\b|nitric\s+acid", "HNO3"),
    (r"\bH2SO4\b|sulfuric\s+acid", "H2SO4"),
    (r"\bHF\b|hydrofluoric", "HF"),
    (r"acetic\s+acid|\bAcOH\b", "acetic acid"),
    (r"\bNaOH\b|sodium\s+hydroxide", "NaOH"),
    (r"\bNH4OH\b|ammonia\s+solution|ammonium\s+hydroxide|\bNH3·H2O\b", "NH4OH"),
    (r"\bTMAOH\b", "TMAOH"),
]

SALTS = [
    (r"\bNaCl\b", "NaCl"),
    (r"\bKCl\b", "KCl"),
    (r"\bNa2SO4\b", "Na2SO4"),
    (r"\bNaF\b", "NaF"),
    (r"\bKNO3\b", "KNO3"),
    (r"\bNH4F\b", "NH4F"),
    (r"\bNa2CO3\b", "Na2CO3"),
]

MESOSTRUCTURES = [
    (r"2D\s*hexagonal|two-?dimensional\s+hexagonal|\bp6mm\b|\bP6mm\b", "2D hexagonal (p6mm)"),
    (r"3D\s*hexagonal|\bP63/mmc\b", "3D hexagonal (P63/mmc)"),
    (r"\bIm3m\b|\bIm-?3m\b|body-?centered\s+cubic", "cubic Im3m"),
    (r"\bIa3d\b|\bIa-?3d\b|bicontinuous\s+cubic", "cubic Ia3d"),
    (r"\bFm3m\b|\bFm-?3m\b|face-?centered\s+cubic", "cubic Fm3m"),
    (r"\bSBA-?15\b", "SBA-15 (2D hexagonal p6mm)"),
    (r"\bSBA-?16\b", "SBA-16 (cubic Im3m)"),
    (r"\bMCM-?41\b", "MCM-41 (2D hexagonal p6mm)"),
    (r"\bMCM-?48\b", "MCM-48 (cubic Ia3d)"),
    (r"\bKIT-?6\b", "KIT-6 (cubic Ia3d)"),
    (r"\bFDU-?12\b", "FDU-12 (cubic Fm3m)"),
    (r"wormhole-?like|disordered\s+mesoporous", "wormhole-like / disordered"),
    (r"\blamellar\b", "lamellar"),
]

ASSEMBLY = [
    (r"evaporation[- ]induced\s+self[- ]assembly|\bEISA\b", "EISA（蒸发诱导自组装）"),
    (r"hydrothermal(ly)?\s+(treat|synthes|aged)", "水热合成"),
    (r"sol-?gel", "溶胶-凝胶"),
    (r"soft[- ]templat", "软模板法"),
    (r"hard[- ]templat|nanocast", "硬模板/纳米浇铸"),
    (r"co-?condensation", "共缩聚"),
    (r"micro(wave|-wave)\s+assisted", "微波辅助"),
    (r"spray[- ]dry", "喷雾干燥"),
]

MORPHOLOGY = [
    (r"\bspher(e|es|ical)\b", "spheres"),
    (r"\brod-?like\b|\brods\b", "rod-like"),
    (r"\bfib(er|re|ers|res)\b", "fibers"),
    (r"\bplate-?like\b|\bplatelets?\b", "platelets"),
    (r"\bhollow\b", "hollow"),
    (r"core-?shell", "core-shell"),
    (r"\bfilm(s)?\b|\bmonolith(s|ic)?\b", "film/monolith"),
    (r"\bnanoparticles?\b", "nanoparticles"),
    (r"\bworm-?like\b", "worm-like"),
]

RE_BET = re.compile(
    r"(?:BET\s+(?:specific\s+)?surface\s+areas?|specific\s+surface\s+areas?|surface\s+areas?|S\s?BET)"
    r"[^.]{0,80}?(\d{2,4}(?:\.\d+)?)\s*(?:m\s?2|m²)\s*[/· ]?\s*g",
    re.I,
)
RE_BET_LOOSE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:m\s?2|m²)\s*(?:/|·|\s)?\s*g", re.I)
RE_PORE_NM = re.compile(
    r"(?:pore\s+(?:size|diameter|width)s?|pore-?size\s+distribution|mesopores?)"
    r"[^.]{0,80}?(\d{1,2}(?:\.\d+)?)\s*nm",
    re.I,
)
RE_PORE_A = re.compile(
    r"(?:pore\s+(?:size|diameter|width)s?)[^.]{0,80}?(\d{2,3}(?:\.\d+)?)\s*(?:Å|A\b|angstrom)",
    re.I,
)
RE_CALC = re.compile(r"calcin\w*[^.]{0,60}?(\d{3,4})\s*°?\s*C(?:[^.]{0,30}?(\d+(?:\.\d+)?)\s*h)?", re.I)
RE_AGING = re.compile(r"(?:aged|aging|stirred|stirring)[^.]{0,60}?(\d{1,3})\s*°?\s*C(?:[^.]{0,30}?(\d+(?:\.\d+)?)\s*h)?", re.I)
RE_HYDRO = re.compile(r"hydrothermal\w*[^.]{0,60}?(\d{2,3})\s*°?\s*C(?:[^.]{0,30}?(\d+(?:\.\d+)?)\s*h)?", re.I)
RE_SOLVENT = re.compile(
    r"(H2O|water|EtOH|ethanol|MeOH|methanol|THF|n-?butanol|BuOH)\s*[:/]\s*"
    r"(H2O|water|EtOH|ethanol|MeOH|methanol|THF|n-?butanol|BuOH)\s*=?\s*"
    r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)",
    re.I,
)
# Mass fractions are the preferred unit for the concentration fields.
RE_CONC_MASS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:wt\.?\s*%|w\s*%|%\s*(?:by\s+)?(?:wt|weight))", re.I)
# Molarity must be case sensitive: lowercase "m" collides with mL, mm, min.
RE_CONC_MOLAR = re.compile(r"(\d+(?:\.\d+)?)\s*(?:M(?![A-Za-z%])|mM\b|mol\s*/?\s*L|mol\s+L-1)")
RE_PH = re.compile(r"\bpH\s*(?:of|=|was|at|to)?\s*(\d{1,2}(?:\.\d+)?)", re.I)


def _sentence_at(text: str, position: int, width: int = 260) -> str:
    """Return the sentence-ish window around a match, for use as a quote."""
    start = max(0, position - width // 2)
    end = min(len(text), position + width // 2)
    window = text[start:end]
    # Trim to sentence boundaries when we can find them.
    left = max(window.rfind(". ", 0, width // 2), window.rfind("\n", 0, width // 2))
    if left > 0:
        window = window[left + 1 :]
    right = window.find(". ", len(window) // 2)
    if right > 0:
        window = window[: right + 1]
    return " ".join(window.split()).strip()


class _Hit:
    __slots__ = ("value", "page", "quote")

    def __init__(self, value: str, page: int, quote: str) -> None:
        self.value = value
        self.page = page
        self.quote = quote


def _find_first(pages: list[str], patterns: list[tuple[str, str | None]]) -> _Hit | None:
    for page_no, text in enumerate(pages, 1):
        for pattern, label in patterns:
            if label is None:
                continue
            match = re.search(pattern, text, re.I)
            if match:
                return _Hit(label, page_no, _sentence_at(text, match.start()))
    return None


def _find_all_labels(pages: list[str], patterns: list[tuple[str, str | None]], limit: int = 3) -> _Hit | None:
    """Collect several distinct labels (e.g. multiple morphologies) into one hit.

    ``exclude`` guards against describing the template instead of the product:
    "spherical micelles" must not become the product morphology.
    """
    exclude = re.compile(r"micelle|surfactant|copolymer|template|precursor", re.I)
    labels: list[str] = []
    first: _Hit | None = None
    # Results sections come later: scan from the back so the product wins.
    ordered = sorted(range(1, len(pages) + 1), reverse=True)
    for page_no in ordered:
        text = pages[page_no - 1]
        for pattern, label in patterns:
            if label is None or label in labels:
                continue
            for match in re.finditer(pattern, text, re.I):
                quote = _sentence_at(text, match.start())
                if exclude.search(quote):
                    continue
                labels.append(label)
                if first is None:
                    first = _Hit(label, page_no, quote)
                break
            if len(labels) >= limit:
                break
        if len(labels) >= limit:
            break
    if not first:
        return None
    first.value = "; ".join(labels)
    return first


def _find_regex(pages: list[str], regex: re.Pattern, group: int = 1) -> _Hit | None:
    for page_no, text in enumerate(pages, 1):
        match = regex.search(text)
        if match:
            return _Hit(match.group(group), page_no, _sentence_at(text, match.start()))
    return None


# Sentences in the results/experimental part are the authoritative source; the
# introduction often quotes literature values ("pore diameters below 4 nm"),
# which naive first-match extraction would wrongly pick up.
_PREFER = re.compile(
    r"distribution|cent(?:red|ered)|BJH|BET|mean|average|calcined\s+sample|"
    r"was\s+\d|of\s+the\s+(?:calcined|product|sample)|our|obtained|resulting|typical",
    re.I,
)
_DEMOTE = re.compile(
    r"literature|previously|reported|such\s+as|limits?|below|whereas|"
    r"introduction|M41S|in\s+contrast|compared\s+with",
    re.I,
)


def _find_regex_best(pages: list[str], regex: re.Pattern, group: int = 1) -> _Hit | None:
    """Pick the most trustworthy match instead of the first one."""
    best: tuple[float, _Hit] | None = None
    for page_no, text in enumerate(pages, 1):
        for match in regex.finditer(text):
            quote = _sentence_at(text, match.start())
            score = 0.0
            if _PREFER.search(quote):
                score += 2.0
            if _DEMOTE.search(quote):
                score -= 3.0
            # Later pages hold results; earlier pages hold the introduction.
            score += min(page_no, 6) * 0.3
            if best is None or score > best[0]:
                best = (score, _Hit(match.group(group), page_no, quote))
    return best[1] if best else None


def _best_temperature(pages: list[str], regex: re.Pattern) -> _Hit | None:
    """Return a temperature step as "temp|hours", preferring a stated duration."""
    best: tuple[float, _Hit] | None = None
    for page_no, text in enumerate(pages, 1):
        for match in regex.finditer(text):
            quote = _sentence_at(text, match.start())
            hours = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            score = 1.5 if hours else 0.0
            if _DEMOTE.search(quote):
                score -= 3.0
            score += min(page_no, 6) * 0.2
            if best is None or score > best[0]:
                best = (score, _Hit(f"{match.group(1)}|{hours or ''}", page_no, quote))
    return best[1] if best else None


# Literal reagent names used to detect "another reagent sits between the number
# and my reagent", which is the main cause of concentration cross-assignment.
RE_REAGENT_TOKEN = re.compile(
    r"\bP\s?123\b|\bF\s?127\b|\bF\s?108\b|\bP\s?65\b|\bCTAB\b|\bCTAC\b|\bSDS\b"
    r"|\bTEOS\b|\bTMOS\b|\bMTES\b|\bAPTES\b"
    r"|\bHCl\b|\bHNO3\b|\bH2SO4\b|\bHF\b|\bNaOH\b|\bNH4OH\b|\bTMAOH\b"
    r"|\bNaCl\b|\bKCl\b|\bNa2SO4\b|\bNaF\b|\bKNO3\b|\bNH4F\b|\bNa2CO3\b",
    re.I,
)


def _clean_token(token: str) -> str:
    return re.sub(r"\s+", "", token).lower()


def _concentration_hit(pages: list[str], token: str) -> _Hit | None:
    """Find a concentration stated next to a named reagent.

    Prefers mass fractions over molarity to match the field contract (w%), and
    rejects a number when a *different* reagent name lies between the number
    and this reagent.
    """
    if not token:
        return None
    wanted = _clean_token(token)
    fallback: _Hit | None = None

    def usable(text: str, name_start: int, name_end: int, conc: re.Match, offset: int) -> bool:
        c_start, c_end = offset + conc.start(), offset + conc.end()
        # A concentration immediately followed by another reagent belongs to it:
        # in "120 g of 2.0 M HCl", the 2.0 M is the acid's, not the surfactant's.
        trailing = text[c_end : c_end + 28]
        for other in RE_REAGENT_TOKEN.finditer(trailing):
            if _clean_token(other.group(0)) != wanted:
                return False
            break
        lo, hi = min(name_end, c_end), max(name_start, c_start)
        if lo >= hi:
            return True
        between = text[lo:hi]
        if len(between) > 150:
            return False
        for other in RE_REAGENT_TOKEN.finditer(between):
            if _clean_token(other.group(0)) != wanted:
                return False
        return True

    for page_no, text in enumerate(pages, 1):
        for match in re.finditer(re.escape(token), text, re.I):
            start = max(0, match.start() - 150)
            window = text[start : match.start() + 220]
            quote = _sentence_at(text, match.start())
            mass = RE_CONC_MASS.search(window)
            if mass and usable(text, match.start(), match.end(), mass, start):
                return _Hit(f"{mass.group(1)} w%", page_no, quote)
            molar = RE_CONC_MOLAR.search(window)
            if (
                molar
                and fallback is None
                and usable(text, match.start(), match.end(), molar, start)
            ):
                fallback = _Hit(" ".join(molar.group(0).split()), page_no, quote)
    return fallback


def extract_records_heuristic(pages: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (records, issues) using deterministic patterns only."""

    issues: list[str] = []
    fields: dict[str, Any] = {key: None for key in FIELD_KEYS}
    evidence: dict[str, list[dict[str, Any]]] = {}
    confidence: dict[str, float] = {}

    def put(key: str, hit: _Hit | None, score: float, value: str | None = None) -> None:
        if not hit:
            return
        fields[key] = value if value is not None else hit.value
        evidence[key] = [{"page": hit.page, "quote": hit.quote}]
        confidence[key] = score

    surfactant = _find_first(pages, SURFACTANTS)
    put("surfactant", surfactant, 0.9)
    if surfactant:
        # Match on the distinctive part of the name (e.g. "P123", not "Pluronic").
        token = surfactant.value.split()[-1]
        conc = _concentration_hit(pages, token)
        if conc:
            put("surfactant_concentration", conc, 0.6)

    silica = _find_first(pages, SILICA_SOURCES)
    put("silica_source", silica, 0.9)
    if silica:
        silica_conc = _concentration_hit(pages, silica.value)
        if silica_conc:
            put("silica_source", silica_conc, 0.7, f"{silica.value} {silica_conc.value}")

    acid = _find_first(pages, ACIDS)
    if acid:
        conc = _concentration_hit(pages, acid.value)
        if conc:
            put("acid_base_concentration", conc, 0.75, f"{acid.value} {conc.value}")
        else:
            ph = _find_regex(pages, RE_PH)
            put(
                "acid_base_concentration",
                acid,
                0.6,
                f"{acid.value}, pH {ph.value}" if ph else acid.value,
            )
    else:
        ph = _find_regex(pages, RE_PH)
        if ph:
            put("acid_base_concentration", ph, 0.5, f"pH {ph.value}")

    salt = _find_first(pages, SALTS)
    if salt:
        salt_conc = _concentration_hit(pages, salt.value)
        if salt_conc:
            put("salt", salt_conc, 0.75, f"{salt.value} {salt_conc.value}")
        else:
            put("salt", salt, 0.65)

    put("assembly_method", _find_first(pages, ASSEMBLY), 0.75)
    put("mesostructure", _find_first(pages, MESOSTRUCTURES), 0.8)
    put("morphology", _find_all_labels(pages, MORPHOLOGY), 0.6)

    solvent = _find_regex(pages, RE_SOLVENT, group=0)
    if solvent:
        put("solution_system", solvent, 0.7, " ".join(solvent.value.split()))

    bet = _find_regex_best(pages, RE_BET)
    bet_score = 0.85
    if bet is None:
        bet = _find_regex_best(pages, RE_BET_LOOSE)
        bet_score = 0.5
    put("surface_area", bet, bet_score)

    pore = _find_regex_best(pages, RE_PORE_NM)
    if pore:
        put("pore_size", pore, 0.85)
    else:
        pore_a = _find_regex_best(pages, RE_PORE_A)
        if pore_a:
            # Deterministic unit conversion: angstrom -> nm, matching postprocess.py.
            put("pore_size", pore_a, 0.7, f"{round(float(pore_a.value) / 10, 2)}")

    steps: list[str] = []
    temp_hit: _Hit | None = None
    for regex, template in (
        (RE_AGING, "{t} °C aging{h}"),
        (RE_HYDRO, "{t} °C hydrothermal{h}"),
        (RE_CALC, "{t} °C calcination{h}"),
    ):
        hit = _best_temperature(pages, regex)
        if hit:
            temp, hours = hit.value.split("|", 1)
            steps.append(template.format(t=temp, h=f" {hours} h" if hours else ""))
            temp_hit = temp_hit or hit
    if steps:
        put("synthesis_temperature", temp_hit, 0.75, "; ".join(steps))

    filled = sum(1 for value in fields.values() if value)
    if filled == 0:
        issues.append("规则引擎未命中任何字段：该 PDF 可能不是介孔材料合成类论文，或文本层缺失")
        return [], issues
    if filled < 4:
        issues.append(f"规则引擎仅命中 {filled}/12 个字段，建议配置 API Key 后用 Agent 模式重抽")

    record = {
        "record_index": 1,
        "fields": fields,
        "evidence": evidence,
        "confidence": confidence,
        "label": "rule-based record",
    }
    return [record], issues
