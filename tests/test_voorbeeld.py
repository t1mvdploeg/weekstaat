"""Tests voor de voorbeeldset: de nieuwe uitvoer van de tool komt overeen met wat in `voorbeeld/verwacht/` is
vastgelegd, en de eigen opmerkingen en de actielijst van de set passen bij de bevindingen die de tool afleidt.

Verandert door een wijziging in de tool een bedrag, dan maak je de vastgelegde uitkomst opnieuw met:

    WEEKSTAAT_VERWACHT_OPNIEUW=1 .venv/bin/python -m pytest tests/test_voorbeeld.py

Die run schrijft `stand <naam>.csv`, `verschillen <naam>.csv` en `bevindingen.csv` in `voorbeeld/verwacht/` en
controleert daarna zoals gewoonlijk. Kijk met `git diff voorbeeld/verwacht` of de verandering klopt. De bedragen en
sleutels in `voorbeeld/opmerkingen.csv` en `voorbeeld/to-do.md` zijn vaste tekst; ze horen bij de bevindingen en een
test hieronder meldt het als ze niet meer kloppen."""

import os
from pathlib import Path

import pandas as pd
import pytest
from conftest import MEDEWERKERS, VERWACHT, VOORBEELD
from hulp import overzicht_van

from weekstaat import bevindingen, dossier, inlezen
from weekstaat.opmaak import nl_bedrag

STAND = ["urenstaat", "gefactureerd", "op_orders", "betaald"]
VERSCHILLEN = ["week", "periode", "oorzaak", "fact", "order", "toewijzing"]
TEKST_BEVINDINGEN = ["sleutel", "soort", "medewerker", "btw", "wie", "groep"]  # de kolommen waarop we toetsen


@pytest.fixture(scope="module")
def d() -> dossier.Dossier:
    return dossier.lees(VOORBEELD)


@pytest.fixture(scope="module")
def overzichten(d) -> dict[str, object]:
    return {naam: overzicht_van(d, naam) for naam in MEDEWERKERS}


@pytest.fixture(scope="module")
def afgeleid(d, overzichten) -> list[bevindingen.Bevinding]:
    return bevindingen.leid_af(list(overzichten.values()), d)


@pytest.fixture(scope="module", autouse=True)
def _verwacht_opnieuw(overzichten, afgeleid):
    """Met WEEKSTAAT_VERWACHT_OPNIEUW=1 schrijft de run eerst de vastgelegde uitkomst opnieuw."""
    if not os.environ.get("WEEKSTAAT_VERWACHT_OPNIEUW"):
        return
    for naam, o in overzichten.items():
        pd.DataFrame([{k: o.stand[k] for k in STAND}]).round(2).to_csv(VERWACHT / f"stand {naam}.csv", index=False)
        o.verschillen[VERSCHILLEN].round(2).to_csv(VERWACHT / f"verschillen {naam}.csv", index=False)
    bevindingen.schrijf_csv(afgeleid, VERWACHT / "bevindingen.csv")


# ---------- de vastgelegde uitkomst ----------


@pytest.mark.parametrize("naam", MEDEWERKERS)
def test_de_stand_komt_overeen_met_de_vastgelegde_uitkomst(overzichten, naam):
    verwacht = pd.read_csv(VERWACHT / f"stand {naam}.csv").iloc[0]
    for k in STAND:
        assert overzichten[naam].stand[k] == pytest.approx(verwacht[k], abs=0.005), k


@pytest.mark.parametrize("naam", MEDEWERKERS)
def test_de_verschillen_komen_overeen_met_de_vastgelegde_uitkomst(overzichten, naam):
    verwacht = pd.read_csv(VERWACHT / f"verschillen {naam}.csv", dtype={"week": str, "periode": str}).fillna("")
    pd.testing.assert_frame_equal(
        overzichten[naam].verschillen[VERSCHILLEN].reset_index(drop=True),
        verwacht,
        check_dtype=False,
        check_exact=False,
        rtol=0,
        atol=0.005,
    )


def test_de_bevindingen_komen_overeen_met_de_vastgelegde_uitkomst(afgeleid):
    verwacht = pd.read_csv(VERWACHT / "bevindingen.csv", sep=";", decimal=",", dtype=str).fillna("")
    gevonden = pd.DataFrame(
        [[getattr(b, k) for k in bevindingen.KOLOMMEN] for b in afgeleid], columns=bevindingen.KOLOMMEN
    )
    assert list(gevonden.sleutel) == list(verwacht.sleutel)
    pd.testing.assert_frame_equal(
        gevonden[TEKST_BEVINDINGEN].fillna("").reset_index(drop=True), verwacht[TEKST_BEVINDINGEN], check_dtype=False
    )
    bedrag = pd.to_numeric(verwacht.bedrag.str.replace(",", "."), errors="coerce")
    pd.testing.assert_series_equal(
        gevonden.bedrag.astype(float), bedrag, check_names=False, check_exact=False, rtol=0, atol=0.005
    )


def test_de_vastgelegde_bevindingen_bevatten_ook_wat_een_opmerking_vervangt():
    """`bevindingen.csv` komt uit `leid_af`, zonder dat `opmerkingen.csv` er iets aan verandert: ook de bevinding waar
    een eigen opmerking bij hoort staat erin."""
    verwacht = pd.read_csv(VERWACHT / "bevindingen.csv", sep=";", decimal=",", dtype=str).fillna("")
    opm = inlezen.lees_opmerkingen(VOORBEELD / "opmerkingen.csv")
    assert set(opm.bevinding[opm.bevinding != ""]) <= set(verwacht.sleutel)


# ---------- de debiteurenkaart ----------


@pytest.mark.parametrize("naam", MEDEWERKERS)
def test_de_kaart_van_de_voorbeeldset_sluit(overzichten, naam):
    kaart = overzichten[naam].kaart
    assert kaart is not None
    assert kaart.sluit, f"verschil {kaart.echt_open - kaart.controle:.2f}"


def test_de_kaart_in_sjabloonvorm_is_de_omzetting_van_de_ruwe_kaart(tmp_path):
    from weekstaat import omzetten

    nieuw = tmp_path / "kaart.csv"
    omzetten.schrijf_sjabloon(omzetten.kaart_uit_boekhouding(VOORBEELD / "ruw" / "debiteurenkaart.xlsx"), nieuw)
    assert nieuw.read_bytes() == (VOORBEELD / "debiteurenkaart.csv").read_bytes()


# ---------- opmerkingen.csv en to-do.md ----------


def test_opmerkingen_zijn_leesbaar_met_een_algemene_en_een_vervangende_rij():
    opm = inlezen.lees_opmerkingen(VOORBEELD / "opmerkingen.csv")
    assert len(opm) == 3
    assert (opm.medewerker == "").sum() == 1  # een opmerking voor alle medewerkers
    assert (opm.bevinding != "").sum() == 1
    assert all(opm.bewijs != "")


def test_de_sleutel_in_opmerkingen_hoort_bij_een_echte_bevinding(afgeleid):
    opm = inlezen.lees_opmerkingen(VOORBEELD / "opmerkingen.csv")
    sleutel = opm.bevinding[opm.bevinding != ""].iloc[0]
    assert sleutel in {b.sleutel for b in afgeleid}


def test_de_handmatige_opmerking_vervangt_de_automatische(d, afgeleid):
    opm = inlezen.lees_opmerkingen(VOORBEELD / "opmerkingen.csv")
    sleutel = opm.bevinding[opm.bevinding != ""].iloc[0]
    uit = bevindingen.opmerkingen(afgeleid, d)
    assert sleutel not in set(uit.bevinding[uit.bron == "automatisch"])
    assert sleutel in set(uit.bevinding[uit.bron == "handmatig"])
    assert (uit.bron == "handmatig").sum() == 3
    assert (uit.bron == "automatisch").sum() == len(afgeleid) - 1


def test_het_bedrag_in_de_opmerking_is_dat_van_de_bevinding(afgeleid):
    opm = inlezen.lees_opmerkingen(VOORBEELD / "opmerkingen.csv")
    rij = opm[opm.bevinding != ""].iloc[0]
    b = next(b for b in afgeleid if b.sleutel == rij.bevinding)
    assert (rij.bedrag, rij.btw, rij.medewerker) == (pytest.approx(b.bedrag, abs=0.005), b.btw, b.medewerker)


def test_to_do_is_leesbaar_met_twee_groepen_en_vijf_acties():
    todo = inlezen.lees_todo(VOORBEELD / "to-do.md")
    assert len(todo) == 2
    assert sum(len(acties) for _, acties in todo) == 5
    assert all(titel and toelichting for _, acties in todo for titel, toelichting in acties)


def test_de_bedragen_in_to_do_komen_uit_de_bevindingen(afgeleid):
    tekst = Path(VOORBEELD / "to-do.md").read_text(encoding="utf-8")
    bedrag = {b.sleutel: b.bedrag for b in afgeleid}
    for sleutel in (
        "uren-zonder-order|Sanne Bakker|2025 wk 25-28",
        "uren-zonder-order|Sanne Bakker|2025 wk 33-36",
        "uren-zonder-order|Sanne Bakker|2025 wk 37-40",
        "dagen-ontbreken-op-de-order|Sanne Bakker|2025 wk 29-32",
        "dagen-ontbreken-op-de-order|Sanne Bakker|2025 wk 41-44",
        "dagvergoeding-niet-op-order|Sanne Bakker|alle",
        "losse-onkostenpost-niet-op-order|Sanne Bakker|alle",
        "kan-worden-afgeletterd|Thijmen Lucas Van Dijk|I01250324+I02250348",
    ):
        assert nl_bedrag(bedrag[sleutel]) in tekst, sleutel
    twee_blokken = (
        bedrag["uren-zonder-order|Sanne Bakker|2025 wk 33-36"] + bedrag["uren-zonder-order|Sanne Bakker|2025 wk 37-40"]
    )
    assert nl_bedrag(twee_blokken) in tekst
