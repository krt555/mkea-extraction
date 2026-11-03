"""Built-in demo corpus.

Three short synthetic papers written in the style of real mesoporous silica
experimental sections.  They exist so the app can be demonstrated with zero
setup: no PDF upload, no API key.  Records are produced by the same rule engine
and the same evidence-grounding check used for real uploads, so what the demo
shows is the real pipeline, not hard-coded output.
"""

from __future__ import annotations

from typing import Any

DEMO_PAPERS: list[dict[str, Any]] = [
    {
        "title": "Triblock Copolymer Syntheses of Highly Ordered Mesoporous Silica SBA-15",
        "filename": "demo_sba15.pdf",
        "pages": [
            """Triblock Copolymer Syntheses of Highly Ordered Mesoporous Silica

ABSTRACT: Highly ordered mesoporous silica with two-dimensional hexagonal
(p6mm) symmetry was prepared by a sol-gel route using the amphiphilic triblock
copolymer Pluronic P123 as the structure-directing agent under strongly acidic
conditions. The materials exhibit large uniform pores, thick framework walls
and high hydrothermal stability. Nitrogen physisorption, small-angle X-ray
scattering and electron microscopy were used to characterise the products.

1. INTRODUCTION

The discovery of the M41S family stimulated extensive work on surfactant
templated mesoporous oxides. Low molecular weight cationic surfactants such as
CTAB give pore diameters below 4 nm, which limits applications involving bulky
molecules. Amphiphilic block copolymers of the poly(ethylene oxide)-
poly(propylene oxide)-poly(ethylene oxide) type offer larger micelles and
therefore larger mesopores, together with thicker walls.""",
            """2. EXPERIMENTAL SECTION

2.1 Synthesis. In a typical preparation, 4.0 g of Pluronic P123
(EO20PO70EO20, average molecular weight 5800) was dissolved in 30 g of
deionised water and 120 g of 2.0 M HCl solution under vigorous stirring at
35 °C. After complete dissolution, 8.50 g of tetraethyl orthosilicate (TEOS)
was added dropwise to the clear solution. The molar composition of the
synthesis mixture was 0.017 P123 : 1 TEOS : 5.9 HCl : 193 H2O. The mixture was
stirred at 35 °C for 20 h and subsequently aged at 100 °C for 24 h under static
hydrothermal conditions in a sealed polypropylene bottle.

The resulting white precipitate was recovered by filtration, washed with
deionised water and ethanol, and dried in air at room temperature. The
as-synthesised material was calcined in flowing air at 550 °C for 6 h with a
heating rate of 1 °C/min to remove the copolymer template.

2.2 Characterisation. Nitrogen adsorption-desorption isotherms were measured
at 77 K. Specific surface areas were calculated by the BET method over the
relative pressure range 0.05-0.20. Pore size distributions were obtained from
the adsorption branch using the BJH model.""",
            """3. RESULTS AND DISCUSSION

3.1 Structural ordering. The small-angle X-ray scattering pattern of the
calcined sample shows three well resolved reflections that can be indexed to
the (100), (110) and (200) planes of a two-dimensional hexagonal lattice with
p6mm symmetry, confirming a highly ordered SBA-15 type mesostructure. The unit
cell parameter derived from the (100) reflection is 10.5 nm.

3.2 Textural properties. The calcined material gives a type IV isotherm with a
sharp H1 hysteresis loop characteristic of cylindrical mesopores with a narrow
size distribution. The BET surface area of the calcined sample was 820 m2/g and
the total pore volume reached 1.05 cm3/g. The BJH pore size distribution is
narrow and centred at a pore diameter of 7.8 nm, with a wall thickness of
about 2.7 nm estimated from the difference between the unit cell parameter and
the pore diameter.

3.3 Morphology. Scanning electron microscopy reveals that the product consists
of rod-like particles with a typical length of 1-2 μm, which further aggregate
into wheat-like macrostructures. Transmission electron microscopy along the
pore axis confirms the hexagonal arrangement of uniform, axially aligned
channels running the entire length of the rods.""",
        ],
    },
    {
        "title": "Cubic Im3m Mesoporous Silica Templated by Pluronic F127 with Salt Addition",
        "filename": "demo_sba16.pdf",
        "pages": [
            """Salt-Assisted Synthesis of Cage-Type Cubic Mesoporous Silica

ABSTRACT: Cage-type mesoporous silica with body-centered cubic Im3m symmetry
was synthesised by a soft-templating sol-gel approach using the triblock
copolymer Pluronic F127 in acidic media. Addition of an inorganic salt was
found to be essential for obtaining a well ordered cubic phase rather than a
poorly ordered wormhole-like product.

1. INTRODUCTION

Cage-type mesostructures provide three-dimensionally interconnected cavities
that are attractive for the immobilisation of enzymes and for controlled
release. The large hydrophilic volume fraction of Pluronic F127 favours
spherical micelles and therefore cage-type packings.""",
            """2. EXPERIMENTAL

2.1 Preparation. A typical synthesis used 2.0 g of Pluronic F127 dissolved in
a mixed solvent of H2O:EtOH = 1:1 by volume (total 60 mL) together with 7.5 mL
of 0.50 M HCl. After stirring at 40 °C for 2 h, 4.20 g of TEOS was introduced
and the clear sol was stirred for a further 24 h at the same temperature.

To promote the ordering of the cage-type phase, 1.50 g of KCl was dissolved in
the synthesis solution before the addition of the silica source, corresponding
to a concentration of 2.0 wt% with respect to the total mass of the mixture.
Control experiments carried out in the absence of added salt yielded only a
disordered mesoporous product.

The gel was transferred to a Teflon-lined autoclave and treated
hydrothermally at 120 °C for 24 h. The solid was filtered, washed and dried
overnight at 60 °C, then calcined at 550 °C for 5 h in air.""",
            """3. RESULTS

3.1 Mesostructure. The X-ray diffraction pattern of the calcined sample
displays an intense (110) reflection together with weaker (200) and (211)
reflections, which are characteristic of a body-centered cubic Im3m
mesostructure of the SBA-16 type.

3.2 Porosity. Nitrogen physisorption gives a type IV isotherm with an H2
hysteresis loop, as expected for cage-like pores connected through narrow
entrances. The BET surface area was 690 m2/g. The pore size distribution
derived from the adsorption branch is centred at a pore diameter of 9.1 nm,
while the entrance size estimated from the desorption branch is about 3.6 nm.

3.3 Morphology. Field emission scanning electron microscopy shows uniform
spherical particles with diameters of 300-600 nm. The particles are
non-aggregated and show a smooth external surface. High resolution
transmission electron microscopy along the [100] direction reveals the
expected square array of cages.""",
        ],
    },
    {
        "title": "Evaporation-Induced Self-Assembly of Mesoporous Silica Thin Films Using CTAB",
        "filename": "demo_eisa_film.pdf",
        "pages": [
            """Evaporation-Induced Self-Assembly of Continuous Mesoporous Silica Films

ABSTRACT: Continuous mesostructured silica films were prepared by dip coating
from acidic sols through evaporation-induced self-assembly (EISA). Using the
cationic surfactant CTAB as the template, films with a two-dimensional
hexagonal p6mm mesostructure oriented parallel to the substrate were obtained.

1. INTRODUCTION

Mesoporous films are of interest for low dielectric constant layers, membranes
and optical sensors. In the EISA process, preferential evaporation of the
volatile solvent progressively concentrates the surfactant above its critical
micelle concentration, driving in-situ self-assembly at the drying front.""",
            """2. EXPERIMENTAL

2.1 Sol preparation. A stock silica sol was prepared by refluxing TEOS,
absolute ethanol, water and hydrochloric acid at 60 °C for 1 h. The final
coating sol had a composition of EtOH:H2O = 4:1 by volume, with HCl adjusted to
give a pH of 1.8. Cetyltrimethylammonium bromide (CTAB) was then added to give
a surfactant concentration of 4.5 wt% in the coating sol, corresponding to a
CTAB/Si molar ratio of 0.11.

2.2 Film deposition. Silicon wafers and glass slides were dip coated at a
withdrawal rate of 1.5 mm/s in a chamber held at 25 °C and 40% relative
humidity. The as-deposited films were aged at 25 °C for 24 h to complete
condensation, then calcined at 400 °C for 3 h with a slow ramp of
1 °C/min to avoid cracking.""",
            """3. RESULTS AND DISCUSSION

3.1 Mesostructure of the films. Grazing incidence small-angle X-ray
scattering of the calcined films shows a set of reflections consistent with a
two-dimensional hexagonal (p6mm) mesostructure in which the cylindrical
channels lie parallel to the substrate plane.

3.2 Textural analysis. Environmental ellipsometric porosimetry of the calcined
films gives an accessible porosity of 42% and a BET surface area of 950 m2/g
when referred to the film mass. The pore size distribution is narrow with a
mean pore diameter of 2.6 nm, consistent with the dimensions of CTAB micelles.

3.3 Film quality. Optical microscopy and scanning electron microscopy confirm
that the films are continuous, crack-free and homogeneous over areas of several
square centimetres, with a thickness of 220 nm after calcination measured by
ellipsometry. Cross-sectional transmission electron microscopy confirms the
parallel channel orientation throughout the film thickness.""",
        ],
    },
]
