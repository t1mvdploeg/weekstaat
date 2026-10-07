"""Tests voor `bank.koppel` met kleine, verzonnen gevallen."""

from dataclasses import replace

import pandas as pd
import pytest

from weekstaat import bank
from weekstaat.instellingen import Rekening

G, GEWONE = "NL00RABO0991000001", "NL00RABO0123456789"


def _orders(*orders, btw=0.21):
    """Orders (nummer, factuurdatum, bedrag incl btw, medewerker); entiteit OI."""
    kop = pd.DataFrame(
        [dict(bestand=f"{nr}.pdf", order=nr, entiteit="OI", factuurdatum=pd.Timestamp(d), excl=i / (1 + btw), incl=i,
              referentie="") for nr, d, i, _ in orders]
    )  # fmt: skip
    regels = pd.DataFrame([dict(bestand=f"{nr}.pdf", order=nr, medewerker=m) for nr, _, _, m in orders])
    return kop, regels


def _bank(*regels):
    """Regels (datum, rekening, bedrag, omschrijving); tegenpartij is steeds de opdrachtgever."""
    return pd.DataFrame(
        [dict(datum=pd.Timestamp(d), rekening=r, bedrag=b, tegenpartij="Oeverland Infra", omschrijving=o)
         for d, r, b, o in regels]
    )  # fmt: skip


def koppel(bankregels, orders, inst, anderen=("Jeroen Hendriks",)):
    kop, regels = _orders(*orders, btw=inst.btw)
    return bank.koppel(bankregels, kop, regels, "Sanne Bakker", list(anderen), inst)


def test_een_betaling_hoort_bij_een_order_en_wordt_over_de_rekeningen_verdeeld(inst):
    b = _bank(("2025-04-10", G, 363.0, "week 1-4"), ("2025-04-10", GEWONE, 847.0, "week 1-4"))
    regels, betaald = koppel(b, [("A1", "2025-03-31", 1210.0, "Sanne Bakker")], inst)
    assert regels.orders.tolist() == [["A1"], ["A1"]]
    assert regels.rekening.tolist() == ["G-rekening", "Gewone rekening"]
    assert regels.aandeel.tolist() == [0.3, 0.7]
    assert regels.rest.tolist() == [0.0, 0.0]
    assert regels.betreft.tolist() == ["ja", "ja"]
    assert regels.opmerking.tolist() == ["", ""]
    assert set(betaald["A1"]) == {"G-rekening", "Gewone rekening"}
    assert betaald["A1"]["G-rekening"].deel == 363.0
    assert not betaald["A1"]["G-rekening"].verzameld
    assert betaald["A1"]["G-rekening"].sluit


def test_een_rekening_zonder_g_rekening(inst):
    """Eén rekening met aandeel 1: een betaling van het hele bedrag incl. btw koppelt aan de order."""
    een = replace(inst, rekeningen=(Rekening("Bedrijfsrekening", "NL00RABO0123456789", 1.0),))
    b = _bank(("2025-04-10", GEWONE, 1210.0, "week 1-4"))
    regels, betaald = koppel(b, [("A1", "2025-03-31", 1210.0, "Sanne Bakker")], een)
    assert regels.orders.tolist() == [["A1"]]
    assert regels.rekening.tolist() == ["Bedrijfsrekening"]
    assert regels.aandeel.tolist() == [1.0]
    assert regels.rest.tolist() == [0.0]
    assert betaald["A1"]["Bedrijfsrekening"].deel == 1210.0
    assert betaald["A1"]["Bedrijfsrekening"].sluit


def test_ander_btw_tarief_in_de_bankkoppeling(inst):
    """Het aandeel van een order is een deel van het bedrag incl. btw uit de kop; het btw-tarief rekent niet mee."""
    laag = replace(inst, btw=0.09)
    b = _bank(("2025-04-10", G, 327.0, "week 1-4"), ("2025-04-10", GEWONE, 763.0, "week 1-4"))
    regels, betaald = koppel(b, [("A1", "2025-03-31", 1090.0, "Sanne Bakker")], laag)
    assert regels.orders.tolist() == [["A1"], ["A1"]]
    assert regels.rest.tolist() == [0.0, 0.0]
    assert betaald["A1"]["G-rekening"].deel == 327.0
    # een betaling die bij 21% zou passen (30% van 1090 x 1,21 / 1,09) hoort er dus niet bij
    scheef = _bank(("2025-04-10", G, 363.0, "week 1-4"))
    assert koppel(scheef, [("A1", "2025-03-31", 1090.0, "Sanne Bakker")], laag)[0].orders.tolist() == [[]]


def test_een_betaling_van_voor_de_factuurdatum_hoort_er_niet_bij(inst):
    b = _bank(("2025-03-20", G, 363.0, "week 1-4"))
    regels, betaald = koppel(b, [("A1", "2025-03-31", 1210.0, "Sanne Bakker")], inst)
    assert regels.orders.tolist() == [[]]
    assert regels.opmerking.tolist() == ["geen order in de map bij deze betaling"]
    assert betaald == {}


def test_een_verzamelbetaling_dekt_de_orders_van_ongeveer_vier_weken_eerder(inst):
    orders = [("A1", "2025-03-03", 1210.0, "Sanne Bakker"), ("A2", "2025-03-04", 605.0, "Jeroen Hendriks")]
    b = _bank(("2025-03-31", G, 544.5, "week 1-4"))
    regels, betaald = koppel(b, orders, inst)
    assert regels.orders.tolist() == [["A1", "A2"]]
    assert regels.medewerkers.tolist() == ["Jeroen Hendriks, Sanne Bakker"]
    assert regels.rest.tolist() == [0.0]
    assert betaald["A1"]["G-rekening"].verzameld  # het bedrag van de order zit in een grotere betaling
    assert betaald["A2"]["G-rekening"].deel == 181.5


def test_een_verzamelbetaling_met_rest_krijgt_een_opmerking(inst):
    orders = [("A1", "2025-03-03", 1210.0, "Sanne Bakker"), ("A2", "2025-03-04", 605.0, "Jeroen Hendriks")]
    b = _bank(("2025-03-31", G, 600.0, "week 1-4"))
    regels, _ = koppel(b, orders, inst)
    assert regels.orders.tolist() == [["A1", "A2"]]
    assert regels.rest.tolist() == [55.5]
    assert regels.opmerking.tolist() == ["verzamelbetaling; rest 55.50 hoort bij een order die niet in de map zit"]


def test_een_order_min_een_creditorder_wordt_op_de_cent_gekoppeld(inst):
    orders = [("A1", "2025-03-03", 1210.0, "Sanne Bakker"), ("C1", "2025-03-10", -121.0, "Sanne Bakker")]
    b = _bank(("2025-06-02", G, 326.7, "week 1-4"))  # 30% van 1210 - 121
    regels, _ = koppel(b, orders, inst)
    assert regels.orders.tolist() == [["A1", "C1"]]
    assert regels.rest.tolist() == [0.0]


def test_zonder_order_hangt_betreft_af_van_wat_de_omschrijving_noemt(inst):
    b = _bank(
        ("2025-06-02", G, 10.0, "factuur voor Jeroen"),
        ("2025-06-03", G, 11.0, "factuur voor iemand"),
    )
    regels, _ = koppel(b, [("A1", "2025-03-03", 1210.0, "Sanne Bakker")], inst)
    assert regels.betreft.tolist() == ["nee", "mogelijk"]


def test_een_onbekende_rekening_geeft_een_fout(inst):
    b = _bank(("2025-06-02", "NL00RABO9999999999", 10.0, "x"))
    with pytest.raises(ValueError, match="staat niet in de instellingen"):
        koppel(b, [("A1", "2025-03-03", 1210.0, "Sanne Bakker")], inst)


@pytest.mark.parametrize(
    ("omschrijving", "anderen", "verwacht"),
    [
        ("Betaling januari week 5", ["Jan Smit"], False),  # "Jan" is deel van "januari"
        ("Basisbedrag week 5", ["Bas Koster"], False),  # "Bas" is deel van "Basisbedrag"
        ("Betaling week 5 Bas", ["Bas Koster"], True),  # de voornaam als heel woord
        ("Betaling week 5 J. Hendri", ["Jeroen Hendriks"], True),  # de bank kapt de achternaam af: begin van een woord
        ("Betaling week 5 Hen", ["Jeroen Hendriks"], True),
        ("Betaling Chenille", ["Jeroen Hendriks"], False),  # "hen" midden in een woord telt niet
        ("Betaling week 5 jan.", ["Jan de Vries"], True),  # leestekens horen niet bij het woord
        ("Betaling week 5", [], False),
    ],
)
def test_noemt_ander_vergelijkt_op_hele_woorden(omschrijving, anderen, verwacht):
    assert bank.noemt_ander(omschrijving, anderen) is verwacht


def test_een_boeking_zonder_order_met_de_voornaam_in_een_ander_woord_blijft_mogelijk(inst):
    """Met de voornaam Jan voor een andere medewerker kreeg een boeking met "januari" "nee" en verdween uit blok C."""
    b = _bank(("2025-04-10", GEWONE, 55.5, "Betaling januari week 5"))
    regels, _ = koppel(b, [("A1", "2025-03-31", 1210.0, "Sanne Bakker")], inst, anderen=("Jan Smit",))
    assert regels.betreft.tolist() == ["mogelijk"]
