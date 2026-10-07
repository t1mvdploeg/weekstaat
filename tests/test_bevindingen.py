"""Tests voor `bevindingen`: de lijst bevindingen die de tool uit het model afleidt, de opmerkingen en acties die
daaruit volgen, en `bevindingen.csv`. Alles op kleine, verzonnen dossiers uit `test_overzicht`."""

import re
from dataclasses import replace

import pandas as pd
import pytest
from hulp import (
    BANK_B,
    DAAN,
    FACTUREN,
    FEMKE,
    IBAN_G,
    IBAN_GEWOON,
    KAART_B,
    dagregels,
    kaart_dossier,
    mini_dossier,
    orders_basis,
    overzicht_van,
)

from weekstaat import bevindingen, dossier, inlezen
from weekstaat.aansluiten import CV, CW, NOG, OV
from weekstaat.bevindingen import GROEPEN, Bevinding

OPDRACHTGEVER, BUREAU = "Oeverland", "Studio Noord"
KAART_ALLEEN_DAAN = [("2025-03-31", "factuur", "F-401", "", 300.08)]
BANK_A = [("2025-04-14", IBAN_G, 580.80), ("2025-04-14", IBAN_GEWOON, 1355.20)]  # order 1: 1936,00 incl


def afleiden(d: dossier.Dossier, *namen: str) -> list[Bevinding]:
    return bevindingen.leid_af([overzicht_van(d, n) for n in namen or (FEMKE,)], d)


def sleutels(bs: list[Bevinding]) -> list[str]:
    return [b.sleutel for b in bs]


def vind(bs: list[Bevinding], sleutel: str) -> Bevinding:
    gevonden = [b for b in bs if b.sleutel == sleutel]
    assert len(gevonden) == 1, f"{sleutel} niet gevonden in {sleutels(bs)}"
    return gevonden[0]


def met_saldo(d: dossier.Dossier, naam: str, regels: list[list]) -> dossier.Dossier:
    kolommen = ["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"]
    dossier.schrijf(d.map, saldo={naam: pd.DataFrame(regels, columns=kolommen)})
    return d


def met_onkosten(d: dossier.Dossier, rijen: list[tuple[str, float]], factuur: str = "F-101") -> dossier.Dossier:
    """Zet onkostenregels (uren 0) van Femke op de factuur: (datum, bedrag)."""
    basis = d.uren[d.uren.factuurnummer == factuur].iloc[0]
    extra = [
        basis.to_dict() | dict(datum=pd.Timestamp(dag), uren=0.0, soort="Vergoeding", tarief=0.0, bedrag=b,
                               netto=b, factuurbedrag=b, rij=1000 + i)
        for i, (dag, b) in enumerate(rijen)
    ]  # fmt: skip
    return replace(d, uren=pd.concat([d.uren, pd.DataFrame(extra)], ignore_index=True))


def schoon_dossier(tmp_path) -> dossier.Dossier:
    """Twee facturen van Femke, twee orders, beide betaald en afgeletterd; op de kaart staat alleen een factuur van
    Daan."""
    return mini_dossier(
        tmp_path, FACTUREN[:2], orders_basis(), [*BANK_A, ("2025-04-25", IBAN_G, 464.64),
                                                   ("2025-04-25", IBAN_GEWOON, 1084.16)], KAART_ALLEEN_DAAN,
    )  # fmt: skip


# ---------- de soorten ----------


def test_uren_zonder_order_per_blok(tmp_path):
    bs = afleiden(kaart_dossier(tmp_path))
    b = vind(bs, f"uren-zonder-order|{FEMKE}|2025 wk 13-16")
    assert (b.soort, b.medewerker, b.bedrag, b.btw) == ("Uren zonder order", FEMKE, 1600.00, "ex")
    assert (b.wie, b.groep, b.bewijs) == (OPDRACHTGEVER, "Naar de opdrachtgever", "Blad Verschillen")
    assert "2025 wk 13" in b.wat and "F-103" in b.wat and "1.600,00" in b.wat
    assert b.actie == f"Vraag {OPDRACHTGEVER} om een order voor 2025 wk 13-16."


def test_uren_zonder_order_ontstaat_niet_als_er_een_order_is(tmp_path):
    orders = [*orders_basis(), ("I01250003", "2025-03-31", FEMKE, dagregels("2025-03-24", 5, 40.0))]
    bank = [*BANK_A, ("2025-04-25", IBAN_G, 464.64), ("2025-04-25", IBAN_GEWOON, 1084.16)]
    d = mini_dossier(tmp_path, FACTUREN, orders, bank, KAART_ALLEEN_DAAN)
    assert not [b for b in afleiden(d) if b.soort == "Uren zonder order"]


def test_dagen_ontbreken_op_de_order(tmp_path):
    order = [("I01250001", "2025-03-17", FEMKE, dagregels("2025-03-10", 4, 40.0))]  # 14-03 staat er niet op
    bank = [("2025-04-14", IBAN_G, 464.64), ("2025-04-14", IBAN_GEWOON, 1084.16)]
    d = mini_dossier(tmp_path, FACTUREN[:1], order, bank, KAART_ALLEEN_DAAN)
    b = vind(afleiden(d), f"dagen-ontbreken-op-de-order|{FEMKE}|2025 wk 09-12")
    assert (b.bedrag, b.btw, b.wie, b.groep) == (320.00, "ex", OPDRACHTGEVER, "Naar de opdrachtgever")
    assert "14-03" in b.wat and "320,00" in b.wat
    assert b.actie.startswith(f"Vraag {OPDRACHTGEVER} om een order voor 14-03")


def test_uren_op_een_toezegging_en_week_dubbel(tmp_path):
    d = kaart_dossier(tmp_path)
    kolommen = ["medewerker", "nr", "datum", "week", "maand", "omschrijving", "eenheid", "aantal", "prijs", "bedrag"]
    t = pd.DataFrame(
        [
            [FEMKE, "T-007", pd.Timestamp("2025-04-01"), "2025 wk 13", "", "Week 13", "Uren", 40.0, 40.0, 1600.0],
            [FEMKE, "T-007", pd.Timestamp("2025-04-01"), "2025 wk 11", "", "Week 11", "Uren", 40.0, 40.0, 1600.0],
        ],
        columns=kolommen,
    )
    bs = afleiden(replace(d, toezegging=t))
    b = vind(bs, f"uren-op-een-toezegging|{FEMKE}|T-007")
    assert (b.bedrag, b.btw, b.wie, b.groep) == (1600.00, "ex", OPDRACHTGEVER, "Naar de opdrachtgever")
    assert "T-007" in b.wat and "2025 wk 13" in b.wat and b.bewijs == "Blad Zonder order, blok D"
    assert "het nummer T-007" in b.actie and OPDRACHTGEVER in b.actie
    # de uren op de toezegging zijn niet ook nog "uren zonder order"
    assert not [x for x in bs if x.soort == "Uren zonder order"]
    dubbel = vind(bs, f"week-dubbel-op-toezegging-en-order|{FEMKE}|T-007")
    assert (dubbel.bedrag, dubbel.btw, dubbel.wie) == (1600.00, "ex", OPDRACHTGEVER)
    assert "2025 wk 11" in dubbel.wat and "I01250001" in dubbel.wat
    assert dubbel.actie.startswith("Ga na of ")


def test_geen_toezegging_geeft_geen_bevinding_over_een_toezegging(tmp_path):
    bs = afleiden(kaart_dossier(tmp_path))
    assert not [b for b in bs if "toezegging" in b.soort.lower()]


def late_bank(tmp_path, bank_tot: str) -> dossier.Dossier:
    """Order 1 (factuurdatum 17-03-2025) is niet betaald en staat open op de kaart; de bankexport loopt t/m `bank_tot`,
    gezet door de betaling van order 2."""
    kaart = [
        ("2025-03-17", "factuur", "F-101", "", 1936.00),
        ("2025-03-24", "factuur", "F-102", "", 1548.80),
        (bank_tot, "ontvangst", "", IBAN_G, -464.64),
        (bank_tot, "ontvangst", "", IBAN_GEWOON, -1084.16),
    ]
    bank = [(bank_tot, IBAN_G, 464.64), (bank_tot, IBAN_GEWOON, 1084.16)]
    return mini_dossier(tmp_path, FACTUREN[:2], orders_basis(), bank, kaart, ander=False)


def test_bevinding_order_niet_betaald_alleen_na_de_termijn(tmp_path):
    """De termijn is 30 dagen na de orderdatum: voor 16-04-2025 is order 1 nog niet vervallen bij einde bankexport."""
    te_laat = afleiden(late_bank(tmp_path / "laat", "2025-04-25"))
    b = vind(te_laat, f"order-niet-betaald|{FEMKE}|I01250001")
    assert (b.soort, b.bedrag, b.btw, b.wie, b.groep) == ("Order niet betaald", 1936.00, "incl", OPDRACHTGEVER,
                                                         "Naar de opdrachtgever")  # fmt: skip
    assert b.bewijs == "Blad Orders"
    assert "17-03-2025" in b.wat and "25-04-2025" in b.wat and "1.936,00" in b.wat
    assert b.actie.startswith(f"Vraag {OPDRACHTGEVER} om betaling van order I01250001")
    nog_niet = afleiden(late_bank(tmp_path / "vroeg", "2025-04-10"))
    assert not [x for x in nog_niet if x.soort == "Order niet betaald"]


def test_order_deels_betaald(tmp_path):
    d = kaart_dossier(tmp_path, bank_a=[("2025-04-14", IBAN_G, 580.80)])  # de gewone rekening ontving niets
    b = vind(afleiden(d), f"order-deels-betaald|{FEMKE}|I01250001")
    assert (b.bedrag, b.btw, b.wie, b.groep) == (1355.20, "incl", OPDRACHTGEVER, "Naar de opdrachtgever")
    assert "580,80" in b.wat and "1.355,20" in b.wat
    assert b.actie == f"Vraag {OPDRACHTGEVER} waarom 1.355,20 niet is betaald op order I01250001."


def creditorders(tmp_path, bank: list) -> dossier.Dossier:
    orders = [
        ("I01250001", "2025-03-17", FEMKE, dagregels("2025-03-10", 5, 40.0)),  # 1936,00 incl
        ("I01250009", "2025-03-24", DAAN, [("2025-03-10", -5.0, 40.0)]),  # creditorder: -242,00 incl
    ]
    kaart = [("2025-03-17", "factuur", "F-101", "", 1936.00), ("2025-03-31", "factuur", "F-401", "", 300.08)]
    return mini_dossier(tmp_path, FACTUREN[:1], orders, bank, kaart)


def test_creditorder_verrekend(tmp_path):
    d = creditorders(tmp_path, [("2025-04-14", IBAN_G, 508.20), ("2025-04-14", IBAN_GEWOON, 1185.80)])
    b = vind(afleiden(d, DAAN), f"creditorder-verrekend|{DAAN}|I01250009")
    assert (b.soort, b.bedrag, b.btw, b.wie, b.groep) == ("Creditorder verrekend", 242.00, "incl", BUREAU,
                                                         "Zelf doen in de boekhouding")  # fmt: skip
    assert b.bewijs == "Blad Orders" and "242,00" in b.wat
    assert b.actie == f"Ga na of {BUREAU} de creditorder I01250009 heeft geboekt in de boekhouding."
    assert not [x for x in afleiden(d, DAAN) if x.soort == "Creditorder niet verrekend"]


def test_creditorder_zonder_verrekening_in_de_bank(tmp_path):
    d = creditorders(tmp_path, BANK_A)  # de volle betaling voor order 1: de creditorder is nergens verrekend
    bs = afleiden(d, DAAN)
    b = vind(bs, f"creditorder-niet-verrekend|{DAAN}|I01250009")
    assert (b.bedrag, b.btw, b.wie, b.groep) == (242.00, "incl", OPDRACHTGEVER, "Naar de opdrachtgever")
    assert b.actie.startswith(f"Vraag {OPDRACHTGEVER} of")
    assert not [x for x in bs if x.soort == "Creditorder verrekend"]


def test_wel_op_order_niet_gefactureerd(tmp_path):
    order = [("I01250001", "2025-03-17", FEMKE, dagregels("2025-03-10", 6, 40.0))]  # een dag meer dan gefactureerd
    bank = [("2025-04-14", IBAN_G, 696.96), ("2025-04-14", IBAN_GEWOON, 1626.24)]
    d = mini_dossier(tmp_path, FACTUREN[:1], order, bank, KAART_ALLEEN_DAAN)
    b = vind(afleiden(d), f"wel-op-order-niet-gefactureerd|{FEMKE}|2025 wk 09-12")
    assert (b.bedrag, b.btw, b.wie, b.groep) == (320.00, "ex", BUREAU, "Navragen")
    assert "15-03" in b.wat and "320,00" in b.wat and b.bewijs == "Blad Verschillen"
    assert b.actie.startswith("Ga na of er op ")


def test_uren_of_tarief_anders_en_meer_betaald(tmp_path):
    """De order heeft tarief 42, de factuur 40; de opdrachtgever betaalde de order en de kaart toont het teveel."""
    kaart = [*KAART_B, ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -96.80)]
    d = kaart_dossier(
        tmp_path, tarief_a=42.0, bank_a=[("2025-04-14", IBAN_G, 609.84), ("2025-04-14", IBAN_GEWOON, 1422.96)],
        kaart=kaart,
    )  # fmt: skip
    bs = afleiden(d)
    u = vind(bs, f"uren-of-tarief-anders|{FEMKE}|alle")
    assert (u.bedrag, u.btw, u.wie, u.groep, u.bewijs) == (
        80.00,
        "ex",
        "Nog uitzoeken",
        "Nog uitzoeken",
        "Blad Verschillen",
    )
    assert "80,00" in u.wat and "in 2025 wk 09-12 staan" in u.wat and u.actie.startswith("Ga na welk tarief")
    m = vind(bs, f"meer-betaald-dan-gefactureerd|{FEMKE}|F-101")
    assert (m.bedrag, m.btw, m.wie, m.groep) == (96.80, "incl", "Nog uitzoeken", "Navragen")
    assert m.bewijs == "Blad Debiteurenkaart, blok C" and "96,80" in m.wat
    assert OPDRACHTGEVER in m.wat and BUREAU in m.wat


def test_uren_of_tarief_anders_ontstaat_niet_als_alles_gelijk_is(tmp_path):
    assert not [b for b in afleiden(kaart_dossier(tmp_path)) if b.soort == "Uren of tarief anders"]


def test_dagvergoeding_en_losse_post(tmp_path):
    dagen = [(f"2025-03-1{i}", 12.50) for i in range(5)]  # vijf keer hetzelfde bedrag: een vast bedrag per dag
    d = met_onkosten(kaart_dossier(tmp_path), [*dagen, ("2025-03-17", 33.00)])  # en een losse post
    bs = afleiden(d)
    v = vind(bs, f"dagvergoeding-niet-op-order|{FEMKE}|alle")
    assert (v.bedrag, v.btw, v.wie, v.groep, v.bewijs) == (
        62.50,
        "ex",
        "Nog uitzoeken",
        "Nog uitzoeken",
        "Blad Verschillen",
    )
    assert "5 dagen" in v.wat and f"Ga na of {OPDRACHTGEVER} dit vaste bedrag" in v.actie
    los = vind(bs, f"losse-onkostenpost-niet-op-order|{FEMKE}|alle")
    assert (los.bedrag, los.btw, los.wie) == (33.00, "ex", "Nog uitzoeken")
    assert los.actie.startswith("Ga na wat")


def test_geen_onkosten_geen_bevinding_over_onkosten(tmp_path):
    bs = afleiden(kaart_dossier(tmp_path))
    assert not [b for b in bs if "onkosten" in b.soort.lower() or "vergoeding" in b.soort.lower()]


def test_onkosten_anders_dan_op_de_order(tmp_path):
    d = kaart_dossier(tmp_path)
    kop, regels = d.kop.copy(), d.regels.copy()
    extra = regels[regels.order == "I01250001"].iloc[[0]].assign(
        eenheid="Kilometers", aantal=10.0, tarief=0.19, bedrag=1.90, omschrijving="Kilometers"
    )  # fmt: skip
    kop.loc[kop.order == "I01250001", ["excl", "incl"]] = [1601.90, round(1601.90 * 1.21, 2)]
    d = met_onkosten(replace(d, kop=kop, regels=pd.concat([regels, extra], ignore_index=True)), [("2025-03-10", 2.50)])
    b = vind(afleiden(d), f"onkosten-anders-dan-op-de-order|{FEMKE}|alle")
    assert (b.bedrag, b.btw, b.groep) == (0.60, "ex", "Nog uitzoeken")
    assert "2,50" in b.wat and "1,90" in b.wat


def test_dubbel_gefactureerd_en_dubbel_op_orders(tmp_path):
    d = kaart_dossier(tmp_path)
    d = met_saldo(d, FEMKE, [
        ["2025 wk 12-13", "Km dubbel (bureau)", 12.5, 0.0, CV, "Twee keer gefactureerd."],
        ["2025 wk 12-13", "Km dubbel (opdrachtgever)", 0.0, 12.5, OV, "Op twee orders."],
    ])  # fmt: skip
    bs = afleiden(d)
    f = vind(bs, f"dubbel-gefactureerd|{FEMKE}|alle")
    assert (f.bedrag, f.btw, f.wie, f.groep) == (12.50, "ex", BUREAU, "Zelf doen in de boekhouding")
    assert f.actie.startswith(f"Ga na of {BUREAU}")
    o = vind(bs, f"dubbel-op-orders|{FEMKE}|alle")
    assert (o.bedrag, o.btw, o.wie, o.groep) == (12.50, "ex", OPDRACHTGEVER, "Naar de opdrachtgever")
    assert o.actie.startswith(f"Meld {OPDRACHTGEVER}")


def afwijkend(week: str, bedrag: float) -> pd.DataFrame:
    return pd.DataFrame([[FEMKE, week, bedrag, "Eigen toelichting."]], columns=inlezen.URENSTAAT_AFWIJKEND)


def test_meer_en_minder_gefactureerd_dan_de_urenstaat(tmp_path):
    d = kaart_dossier(tmp_path)  # week 12 is voor 1280,00 gefactureerd
    meer = afleiden(replace(d, urenstaat_afwijkend=afwijkend("2025 wk 12", 1000.0)))
    b = vind(meer, f"meer-gefactureerd-dan-de-urenstaat|{FEMKE}|2025 wk 12")
    assert (b.bedrag, b.btw, b.wie, b.groep, b.bewijs) == (280.00, "ex", BUREAU, "Zelf doen in de boekhouding",
                                                         "Blad Per week")  # fmt: skip
    assert "1.280,00" in b.wat and "1.000,00" in b.wat and "Eigen toelichting." in b.wat
    assert b.actie.startswith("Ga na of ")
    minder = afleiden(replace(d, urenstaat_afwijkend=afwijkend("2025 wk 12", 1500.0)))
    m = vind(minder, f"minder-gefactureerd-dan-de-urenstaat|{FEMKE}|2025 wk 12")
    assert m.bedrag == 220.00 and m.wie == BUREAU
    assert not [x for x in afleiden(d) if "gefactureerd dan de urenstaat" in x.soort]


def test_kan_worden_afgeletterd(tmp_path):
    bs = afleiden(kaart_dossier(tmp_path))
    b = vind(bs, f"kan-worden-afgeletterd|{FEMKE}|I01250002")
    # F-103 staat ook open op de kaart, maar daarvoor is niets betaald: dat is geen factuur om af te letteren
    assert [x.sleutel for x in bs if x.soort == "Kan worden afgeletterd"] == [b.sleutel]
    assert (b.bedrag, b.btw, b.wie, b.groep) == (1548.80, "incl", BUREAU, "Zelf doen in de boekhouding")
    assert b.bewijs == "Blad Debiteurenkaart, blok C" and "F-102" in b.wat
    assert b.actie.startswith("Ga na of F-102 in de boekhouding van " + BUREAU)


def test_debiteurenkaart_sluit_niet_aan(tmp_path):
    kaart = [("2025-03-24", "factuur", "F-102", "", 1000.00), *KAART_B[1:]]  # 548,80 te laag
    b = vind(afleiden(kaart_dossier(tmp_path, kaart=kaart)), f"debiteurenkaart-sluit-niet-aan|{FEMKE}|debiteurenkaart")
    assert (b.bedrag, b.btw, b.wie, b.groep, b.bewijs) == (548.80, "incl", "Nog uitzoeken", "Nog uitzoeken",
                                                         "Blad Debiteurenkaart, blok B")  # fmt: skip
    assert "548,80" in b.wat
    assert not [x for x in afleiden(kaart_dossier(tmp_path / "goed")) if x.soort == "Debiteurenkaart sluit niet aan"]


def test_vervangen_order(tmp_path):
    orders = [*orders_basis(), ("I01250003", "2025-03-18", FEMKE, dagregels("2025-03-10", 5, 40.0))]
    d = kaart_dossier(tmp_path, orders=orders)
    vervallen = pd.DataFrame(
        [["I01250003", "I01250001", "Vervangen."]], columns=["order", "vervangen_door", "toelichting"]
    )
    b = vind(afleiden(replace(d, vervallen=vervallen)), f"vervangen-order|{FEMKE}|I01250003")
    assert (b.bedrag, b.btw, b.wie, b.groep, b.bewijs) == (1600.00, "ex", OPDRACHTGEVER, "Naar de opdrachtgever",
                                                         "Blad Orders")  # fmt: skip
    assert (
        "I01250001" in b.wat
        and b.actie == f"Vraag {OPDRACHTGEVER} te bevestigen dat order I01250003 is vervangen door I01250001."
    )


def test_bron_ontbreekt(tmp_path):
    d = kaart_dossier(tmp_path)
    zonder_bank = afleiden(replace(d, bank=None))
    b = vind(zonder_bank, "bron-ontbreekt|Algemeen|bank")
    assert (b.soort, b.medewerker, b.bedrag, b.btw, b.groep, b.wie) == ("Bron ontbreekt", "Algemeen", None, "",
                                                                      "Aanleveren", "Nog uitzoeken")  # fmt: skip
    assert "debiteurenkaart" in b.wat  # de kaart wordt tegen de bank gelegd: zonder bank blijft hij ongebruikt
    assert b.actie.startswith("Lever ")
    zonder_kaart = afleiden(replace(d, kaart=None))
    k = vind(zonder_kaart, "bron-ontbreekt|Algemeen|debiteurenkaart")
    assert k.groep == "Aanleveren"
    assert not [x for x in zonder_kaart if x.sleutel == "bron-ontbreekt|Algemeen|bank"]
    assert not [x for x in afleiden(d) if x.soort == "Bron ontbreekt"]


# ---------- wie en groep volgen de toewijzing ----------


def test_wie_volgt_de_toewijzing_uit_het_model(tmp_path):
    d = met_saldo(kaart_dossier(tmp_path), FEMKE, [["2025 wk 13", "Uren", None, None, NOG, "Eigen oordeel."]])
    assert vind(afleiden(d), f"uren-zonder-order|{FEMKE}|2025 wk 13-16").wie == "Nog uitzoeken"
    d = met_saldo(kaart_dossier(tmp_path / "b"), FEMKE, [["2025 wk 13", "Uren", None, None, CW, ""]])
    b = vind(afleiden(d), f"uren-zonder-order|{FEMKE}|2025 wk 13-16")
    assert b.wie == BUREAU and b.groep == "Zelf doen in de boekhouding"


# ---------- sleutels ----------


def rijk_dossier(tmp_path) -> dossier.Dossier:
    """Een dossier met veel soorten bevindingen tegelijk, voor de controles over alle teksten en sleutels."""
    bank_a = [("2025-04-14", IBAN_G, 609.84), ("2025-04-14", IBAN_GEWOON, 1422.96)]
    kaart = [*KAART_B, ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -96.80)]
    d = kaart_dossier(tmp_path, tarief_a=42.0, bank_a=bank_a, kaart=kaart)
    d = met_onkosten(d, [(f"2025-03-1{i}", 12.50) for i in range(5)])
    d = met_saldo(
        d,
        FEMKE,
        [["2025 wk 12-13", "Km dubbel A", 12.5, 0.0, CV, ""], ["2025 wk 12-13", "Km dubbel B", 0.0, 12.5, OV, ""]],
    )
    return replace(d, urenstaat_afwijkend=afwijkend("2025 wk 12", 1000.0))


def test_sleutels_zijn_vast_en_uniek(tmp_path):
    d = rijk_dossier(tmp_path)
    eerste, tweede = afleiden(d, FEMKE, DAAN), afleiden(d, FEMKE, DAAN)
    assert sleutels(eerste) == sleutels(tweede) and len(eerste) > 8
    assert len(set(sleutels(eerste))) == len(eerste)
    for b in eerste:
        soort, medewerker, deel = b.sleutel.split("|")
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", soort) and medewerker == b.medewerker and deel
        assert (
            "," not in b.sleutel
        )  # een sleutel mag in kolom `bevinding` naast andere staan, gescheiden door een komma
    assert {b.groep for b in eerste} <= set(GROEPEN)
    assert {b.btw for b in eerste} <= {"ex", "incl", ""}


def test_sleutels_hangen_niet_af_van_de_toewijzing(tmp_path):
    """Een ander oordeel van de gebruiker verandert wie aan zet is, niet de sleutel: een opmerking blijft erbij."""
    basis = afleiden(kaart_dossier(tmp_path / "a"))
    anders = afleiden(
        met_saldo(kaart_dossier(tmp_path / "b"), FEMKE, [["2025 wk 13", "Uren", None, None, CW, "Oordeel."]])
    )
    assert sleutels(basis) == sleutels(anders)
    assert [b.wie for b in basis] != [b.wie for b in anders]


def test_dezelfde_medewerker_twee_keer_is_een_fout(tmp_path):
    d = kaart_dossier(tmp_path)
    o = overzicht_van(d, FEMKE)
    with pytest.raises(ValueError, match=f"{FEMKE}.*twee keer"):
        bevindingen.leid_af([o, o], d)


# ---------- teksten ----------


def test_teksten_zeggen_wat_de_tool_ziet_en_gebruiken_de_namen_uit_de_instellingen(tmp_path):
    d = rijk_dossier(tmp_path)
    for b in afleiden(d, FEMKE, DAAN):
        for tekst in (b.onderwerp, b.wat, b.actie):
            assert tekst[:1].isupper() or tekst[:1].isdigit(), f"{b.sleutel}: {tekst}"
            assert not re.search(r"\bmoet|\bmoeten", tekst), f"{b.sleutel}: {tekst}"
            assert "de opdrachtgever" not in tekst.lower() and "het bureau" not in tekst.lower(), tekst
        assert b.actie.rstrip().endswith((".", "?")), b.actie


def test_zonder_namen_in_de_instellingen_begint_elke_zin_met_een_hoofdletter(tmp_path):
    kaart = [*KAART_B, ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -96.80)]
    bank = BANK_A + [("2025-04-25", IBAN_G, 464.64), ("2025-04-25", IBAN_GEWOON, 1084.16)]
    d = mini_dossier(tmp_path, FACTUREN, orders_basis(), bank, kaart, namen="")
    bs = afleiden(d)
    assert bs
    for b in bs:
        for tekst in (b.onderwerp, b.wat, b.actie):
            assert tekst[:1].isupper() or tekst[:1].isdigit(), f"{b.sleutel}: {tekst}"
    assert {"de opdrachtgever", "het bureau"} & {b.wie for b in bs}
    assert any(b.wat.startswith("Het bureau ") for b in bs)


# ---------- opmerkingen ----------


def opmerking_rij(medewerker: str, nr: str, onderwerp: str, bevinding: str = "", belangrijk: str = "nee") -> list:
    return [
        medewerker,
        nr,
        belangrijk,
        onderwerp,
        None,
        "",
        f"Wat bij {onderwerp}",
        "Bewijs",
        "Actie",
        BUREAU,
        bevinding,
    ]


def test_handmatige_opmerking_vervangt_de_automatische(tmp_path):
    d = kaart_dossier(tmp_path)
    bs = afleiden(d)
    een, twee = f"uren-zonder-order|{FEMKE}|2025 wk 13-16", f"kan-worden-afgeletterd|{FEMKE}|I01250002"
    handmatig = pd.DataFrame(
        [opmerking_rij(FEMKE, "1", "Eigen tekst", f"{een}, {twee}", "ja"), opmerking_rij("", "2", "Voor iedereen")],
        columns=inlezen.OPMERKINGEN,
    ).astype({"bedrag": float})
    o = bevindingen.opmerkingen(bs, replace(d, opmerkingen=handmatig))
    assert list(o.columns) == [*inlezen.OPMERKINGEN, "bron"]
    assert o.bevinding.tolist()[0] == f"{een}, {twee}"  # de sleutels staan alleen in de cel van de handmatige rij
    assert o[o.bron == "handmatig"].onderwerp.tolist() == ["Eigen tekst", "Voor iedereen"]
    automatisch = o[o.bron == "automatisch"]
    assert set(automatisch.bevinding) == set(sleutels(bs)) - {een, twee}
    assert len(o) == 2 + len(bs) - 2


def test_opmerkingen_zonder_handmatig_bestand_zijn_alle_automatisch(tmp_path):
    d = kaart_dossier(tmp_path)
    bs = afleiden(d)
    o = bevindingen.opmerkingen(bs, d)
    assert (o.bron == "automatisch").all() and o.bevinding.tolist() == sleutels(bs)
    assert len(o) == len(bs) and not o.duplicated(["medewerker", "nr"]).any()


def test_automatische_opmerking_neemt_de_bevinding_over(tmp_path):
    d = late_bank(tmp_path, "2025-04-25")
    bs = afleiden(d)
    o = bevindingen.opmerkingen(bs, d).set_index("bevinding")
    b = vind(bs, f"order-niet-betaald|{FEMKE}|I01250001")
    r = o.loc[b.sleutel]
    assert (r.medewerker, r.onderwerp, r.bedrag, r.btw, r.wat, r.bewijs, r.actie, r.wie) == (
        b.medewerker, b.onderwerp, b.bedrag, b.btw, b.wat, b.bewijs, b.actie, b.wie
    )  # fmt: skip


def test_belangrijk_is_ja_boven_de_duizend_en_voor_een_order_die_niet_is_betaald(tmp_path):
    groot = bevindingen.opmerkingen(afleiden(kaart_dossier(tmp_path)), kaart_dossier(tmp_path / "x")).set_index(
        "bevinding"
    )
    assert groot.belangrijk[f"uren-zonder-order|{FEMKE}|2025 wk 13-16"] == "ja"  # 1.600,00
    assert groot.belangrijk[f"kan-worden-afgeletterd|{FEMKE}|I01250002"] == "ja"  # 1.548,80
    d = late_bank(tmp_path / "laat", "2025-04-25")
    o = bevindingen.opmerkingen(afleiden(d), d).set_index("bevinding")
    assert o.belangrijk[f"order-niet-betaald|{FEMKE}|I01250001"] == "ja"  # 1.936,00 en niet betaald
    # klein bedrag en geen order niet betaald: nee
    d2 = met_onkosten(kaart_dossier(tmp_path / "klein"), [("2025-03-10", 5.0)])
    o2 = bevindingen.opmerkingen(afleiden(d2), d2).set_index("bevinding")
    assert o2.belangrijk[f"losse-onkostenpost-niet-op-order|{FEMKE}|alle"] == "nee"


def test_opmerkingen_staan_per_medewerker_met_de_handmatige_eerst(tmp_path):
    d = kaart_dossier(tmp_path)
    bs = afleiden(d)
    handmatig = pd.DataFrame(
        [opmerking_rij("Iemand Anders", "1", "Van een ander"), opmerking_rij(FEMKE, "2", "Van Femke")],
        columns=inlezen.OPMERKINGEN,
    ).astype({"bedrag": float})
    o = bevindingen.opmerkingen(bs, replace(d, opmerkingen=handmatig))
    assert o.medewerker.tolist()[:2] == ["Iemand Anders", FEMKE]
    femke = o[o.medewerker == FEMKE]
    assert femke.bron.tolist() == ["handmatig", *["automatisch"] * (len(femke) - 1)]
    volgorde = ["Iemand Anders", FEMKE, "Algemeen"]  # in de volgorde van eerste voorkomen
    assert o.medewerker.tolist() == sorted(o.medewerker.tolist(), key=volgorde.index)


# ---------- acties ----------


def test_alles_sluit_aan(tmp_path):
    d = schoon_dossier(tmp_path)
    bs = afleiden(d)
    assert bs == []
    assert bevindingen.acties(bs, d) == [("Niets te doen", [("Er zijn geen acties: alles sluit aan.", "Algemeen", "")])]
    o = bevindingen.opmerkingen(bs, d)
    assert o.empty and list(o.columns) == [*inlezen.OPMERKINGEN, "bron"]


def test_acties_uit_de_bevindingen_per_groep_in_vaste_volgorde(tmp_path):
    d = kaart_dossier(tmp_path)
    bs = afleiden(d)
    a = bevindingen.acties(bs, d)
    assert [g for g, _ in a] == [g for g in GROEPEN if g in {b.groep for b in bs}]
    alle = [(g, titel, voor, tekst) for g, regels in a for titel, voor, tekst in regels]
    assert len(alle) == len(bs)
    b = vind(bs, f"uren-zonder-order|{FEMKE}|2025 wk 13-16")
    assert ("Naar de opdrachtgever", b.onderwerp, "Femke", f"{b.wat} {b.actie}") in alle
    # met een woordenboek met korte namen
    kort = bevindingen.acties(bs, d, korte_namen={FEMKE: "Femke J"})
    assert {voor for _, regels in kort for _, voor, _ in regels} == {"Femke J", "Algemeen"}


def test_algemene_bevinding_is_voor_algemeen(tmp_path):
    d = kaart_dossier(tmp_path)
    bs = afleiden(replace(d, kaart=None))
    a = dict(bevindingen.acties(bs, replace(d, kaart=None)))
    assert [(titel, voor) for titel, voor, _ in a["Aanleveren"]] == [("Er is geen debiteurenkaart", "Algemeen")]


def test_todo_gaat_voor_de_automatische_acties(tmp_path):
    d = kaart_dossier(tmp_path)
    bs = afleiden(d)
    todo = [
        ("Naar de opdrachtgever", [("Femke ophalen", "Vraag het na bij Femke."), ("Iets algemeens", "Zonder naam.")]),
        ("Zelf doen", [("Order vragen", "Voor Femke en voor Daan."), ("Anders", "Femkeline is iemand anders.")]),
    ]
    verwacht = [
        ("Naar de opdrachtgever", [("Femke ophalen", "Femke", "Vraag het na bij Femke."),
                                   ("Iets algemeens", "Algemeen", "Zonder naam.")]),
        ("Zelf doen", [("Order vragen", "Femke, Daan", "Voor Femke en voor Daan."),
                       ("Anders", "Algemeen", "Femkeline is iemand anders.")]),
    ]  # fmt: skip
    assert bevindingen.acties(bs, replace(d, todo=todo), korte_namen={FEMKE: "Femke", DAAN: "Daan"}) == verwacht
    # zonder woordenboek zijn het de voornamen, en zonder bevindingen staat de to-do er nog steeds: hij gaat voor
    assert bevindingen.acties([], replace(d, todo=todo)) == verwacht


def test_todo_lijst_van_het_dossier_met_inleiding_wordt_gelezen(tmp_path):
    d = schoon_dossier(tmp_path)
    (d.map / "to-do.md").write_text(
        "# Acties\n\nEen inleiding.\n\n## Groep\n\n- **Doe iets** voor Femke.\n", encoding="utf-8"
    )
    d = dossier.lees(d.map)
    assert bevindingen.acties([], d, korte_namen={FEMKE: "Femke"}) == [
        ("Groep", [("Doe iets", "Femke", "Voor Femke.")])
    ]


# ---------- bevindingen.csv ----------


def test_bevindingen_csv_heeft_de_vaste_vorm(tmp_path):
    bs = [
        Bevinding("soort|Femke|x", "Soort", FEMKE, "-Begint met een min", 1234.5, "ex", "=Wat; met puntkomma", "Blad X",
                  "+Actie", BUREAU, "Zelf doen in de boekhouding"),
        Bevinding("bron|Algemeen|bank", "Bron", "Algemeen", "Zonder bedrag", None, "", "Wat", "Blad Y", "Actie",
                  "Nog uitzoeken", "Aanleveren"),
    ]  # fmt: skip
    pad = tmp_path / "bevindingen.csv"
    bevindingen.schrijf_csv(bs, pad)
    regels = pad.read_text(encoding="utf-8").splitlines()
    assert regels[0] == "sleutel;soort;medewerker;onderwerp;bedrag;btw;wat;bewijs;actie;wie;groep"
    assert regels[1] == (
        f"soort|Femke|x;Soort;{FEMKE};'-Begint met een min;1234,5;ex;\"'=Wat; met puntkomma\";Blad X;'+Actie;{BUREAU};"
        "Zelf doen in de boekhouding"
    )
    assert regels[2] == "bron|Algemeen|bank;Bron;Algemeen;Zonder bedrag;;;Wat;Blad Y;Actie;Nog uitzoeken;Aanleveren"
    # een lege lijst geeft alleen de kopregel
    bevindingen.schrijf_csv([], tmp_path / "leeg.csv")
    assert (tmp_path / "leeg.csv").read_text(encoding="utf-8").splitlines() == [regels[0]]


def test_bevindingen_csv_van_een_dossier_heeft_een_regel_per_bevinding(tmp_path):
    bs = afleiden(kaart_dossier(tmp_path))
    pad = tmp_path / "uit" / "bevindingen.csv"
    pad.parent.mkdir()
    bevindingen.schrijf_csv(bs, pad)
    terug = pd.read_csv(pad, sep=";", decimal=",", keep_default_na=False)
    assert terug.sleutel.tolist() == sleutels(bs)
    assert terug.bedrag.tolist() == [b.bedrag for b in bs]
    assert terug.groep.tolist() == [b.groep for b in bs]


# ---------- perioden, algemene bevindingen, sleutels bewaakt ----------


def test_uren_of_tarief_anders_noemt_blokken_perioden_en_geen_weken(tmp_path):
    dagen = ["2025-03-10", "2025-04-07", "2025-05-05", "2025-06-02"]  # vier blokken van vier weken
    facturen = [(f"F-10{i}", dag, dagregels(dag, 1, 40.0)) for i, dag in enumerate(dagen)]
    orders = [(f"I0125000{i}", dag, FEMKE, dagregels(dag, 1, 42.0)) for i, dag in enumerate(dagen)]
    d = mini_dossier(tmp_path, facturen, orders, [], [], ander=False)
    b = vind(afleiden(d), f"uren-of-tarief-anders|{FEMKE}|alle")
    assert "in 4 perioden (2025 wk 09-12 t/m 2025 wk 21-24) staan" in b.wat
    assert "weken" not in b.wat


def test_soorten_op_het_mini_dossier_met_kaart(tmp_path):
    """De verzameling soorten als geheel: een soort die er niet hoort, valt hier op."""
    soorten = {b.soort for b in afleiden(kaart_dossier(tmp_path))}
    assert soorten == {"Uren zonder order", "Kan worden afgeletterd", "Open facturen buiten de uren"}


def met_onbekende_order(tmp_path, bank=()) -> dossier.Dossier:
    orders = [*orders_basis(), ("I01250004", "2025-03-31", "Gerrit Visser", dagregels("2025-03-10", 3, 40.0))]
    return kaart_dossier(tmp_path, orders=orders, bank_b=[*BANK_B, *bank])


def test_order_voor_een_medewerker_zonder_uren(tmp_path):
    b = vind(afleiden(met_onbekende_order(tmp_path / "a")), "order-voor-een-medewerker-zonder-uren|Algemeen|I01250004")
    assert (b.soort, b.medewerker, b.bedrag, b.btw, b.wie, b.groep) == (
        "Order voor een medewerker zonder uren",
        "Algemeen",
        1161.60,
        "incl",
        "Nog uitzoeken",
        "Aanleveren",
    )
    assert (b.onderwerp, b.bewijs) == ("Order I01250004 voor een medewerker zonder uren", "Blad Orderregels")
    assert "Gerrit Visser" in b.wat and "10-03-2025 t/m 12-03-2025" in b.wat and "1.161,60" in b.wat
    assert "In de uren staat geen regel voor deze naam." in b.wat
    assert "Er is geen betaling voor deze order gevonden." in b.wat
    assert b.actie == f"Ga na of deze medewerker bij {BUREAU} hoort en of de uren nog moeten worden aangeleverd."
    betaald = [("2025-04-20", IBAN_G, 348.48), ("2025-04-20", IBAN_GEWOON, 813.12)]
    b = vind(afleiden(met_onbekende_order(tmp_path / "b", betaald)), b.sleutel)
    assert "Er is een betaling voor deze order gevonden." in b.wat


def test_order_met_bekende_naam_en_zonder_bank_geeft_geen_of_een_kortere_bevinding(tmp_path):
    # voornaam en laatste woord tellen: een tweede naam of initiaal maakt niets uit
    orders = [*orders_basis(), ("I01250004", "2025-03-31", "Femke M. Jansma", dagregels("2025-03-10", 3, 40.0))]
    assert not [b for b in afleiden(kaart_dossier(tmp_path / "a", orders=orders)) if b.soort.startswith("Order voor")]
    # zonder bank staat er niets over een betaling, en een vervallen order telt niet
    d = met_onbekende_order(tmp_path / "b")
    zonder_bank = vind(afleiden(replace(d, bank=None)), "order-voor-een-medewerker-zonder-uren|Algemeen|I01250004")
    assert "betaling" not in zonder_bank.wat
    vervallen = pd.DataFrame([["I01250004", "", ""]], columns=["order", "vervangen_door", "toelichting"])
    assert not [b for b in afleiden(replace(d, vervallen=vervallen)) if b.soort.startswith("Order voor")]


def test_order_met_meer_medewerkers_noemt_alleen_de_onbekende_naam(tmp_path):
    d = kaart_dossier(tmp_path)
    regels = d.regels.copy()
    regels.loc[regels.index[regels.order == "I01250002"][-2:], "medewerker"] = "Gerrit Visser"
    b = vind(afleiden(replace(d, regels=regels)), "order-voor-een-medewerker-zonder-uren|Algemeen|I01250002")
    assert "Gerrit Visser" in b.wat and FEMKE not in b.wat and "Femke" not in b.wat


def test_open_facturen_buiten_de_uren(tmp_path):
    d = kaart_dossier(tmp_path)
    b = vind(afleiden(d), "open-facturen-buiten-de-uren|Algemeen|debiteurenkaart")
    assert (b.medewerker, b.bedrag, b.btw, b.wie, b.groep) == (
        "Algemeen",
        121.00,
        "incl",
        "Nog uitzoeken",
        "Nog uitzoeken",
    )
    assert (
        b.bewijs == "Blad Debiteurenkaart, blok A"
        and b.actie == "Ga na bij welke medewerker of opdracht deze facturen horen."
    )
    assert b.wat.startswith("Op de debiteurenkaart staat 1 open factuur die bij geen enkele urenregel voorkomt: F-900")
    assert b.bedrag == overzicht_van(d, FEMKE).kaart.heel["buiten_uren"]  # hetzelfde bedrag als in het model


def test_open_facturen_buiten_de_uren_noemt_er_vijf_en_telt_de_rest(tmp_path):
    extra = [("2025-03-31", "factuur", f"F-90{i}", "", 100.0 + i) for i in range(7)]
    d = kaart_dossier(tmp_path, kaart=[*KAART_B[:2], *extra])
    b = vind(afleiden(d), "open-facturen-buiten-de-uren|Algemeen|debiteurenkaart")
    assert (
        "7 open facturen die bij geen enkele urenregel voorkomen: F-900, F-901, F-902, F-903, F-904 en 2 andere"
        in b.wat
    )
    assert (
        b.bedrag == pytest.approx(sum(100.0 + i for i in range(7))) == overzicht_van(d, FEMKE).kaart.heel["buiten_uren"]
    )
    assert not [x for x in afleiden(replace(d, kaart=None)) if x.soort == "Open facturen buiten de uren"]


def test_dubbele_sleutel_is_een_fout_in_de_tool(tmp_path, monkeypatch):
    d = kaart_dossier(tmp_path)
    b = afleiden(d)[0]
    monkeypatch.setattr(bevindingen, "PER_MEDEWERKER", (lambda o: [b, b],))
    with pytest.raises(ValueError, match=re.escape(f"de sleutel {b.sleutel} komt twee keer voor")):
        afleiden(d)


def test_opmerking_met_onbekende_sleutel_geeft_een_waarschuwing_en_blijft_staan(tmp_path, caplog):
    d = kaart_dossier(tmp_path)
    bs = afleiden(d)
    goed = sleutels(bs)[0]
    handmatig = pd.DataFrame(
        [opmerking_rij(FEMKE, "7", "Typfout", f"{goed}, uren-zonder-ordr|{FEMKE}|2025 wk 13-16")],
        columns=inlezen.OPMERKINGEN,
    ).astype({"bedrag": float})
    with caplog.at_level("WARNING", logger="weekstaat"):
        o = bevindingen.opmerkingen(bs, replace(d, opmerkingen=handmatig))
    meldingen = [r.getMessage() for r in caplog.records]
    assert len(meldingen) == 1 and "opmerking 7" in meldingen[0] and "uren-zonder-ordr|" in meldingen[0]
    assert goed not in meldingen[0] and o.onderwerp.tolist()[0] == "Typfout"
    caplog.clear()
    with caplog.at_level("WARNING", logger="weekstaat"):
        bevindingen.opmerkingen(bs, d)
    assert not caplog.records


def test_todo_herkent_een_medewerker_ook_bij_de_achternaam(tmp_path):
    d = kaart_dossier(tmp_path)
    naam = "Jan Willem de Groot"
    todo = [
        (
            "Groep",
            [
                ("Uren van De Groot", "Zonder meer."),
                ("Algemeen punt", "Jan heeft het nog niet gedaan."),
                ("Volledige naam", "Bel Jan Willem de Groot morgen."),
                ("Achternaam met voorvoegsel", "Vraag het aan Willem de Groot."),
                ("Niet herkend", "In januari bij de groothandel."),
                ("Korte achternaam", "De naam Jan staat hier niet."),
            ],
        )
    ]
    a = bevindingen.acties([], replace(d, todo=todo), korte_namen={naam: "Jan", DAAN: "Daan"})
    wie = {titel: voor for _, regels in a for titel, voor, _ in regels}
    assert wie["Uren van De Groot"] == "Jan"  # de achternaam: het laatste woord (vier letters of meer)
    assert wie["Algemeen punt"] == "Jan"  # de korte naam
    assert wie["Volledige naam"] == "Jan"
    assert wie["Achternaam met voorvoegsel"] == "Jan"  # alles na de eerste voornaam
    assert wie["Niet herkend"] == "Algemeen"  # "januari" en "groothandel" zijn andere woorden
    assert wie["Korte achternaam"] == "Jan"


def test_een_kort_laatste_woord_telt_niet_als_achternaam(tmp_path):
    d = kaart_dossier(tmp_path)
    todo = [("Groep", [("Titel", "De uren van Lee en de rest van Vos.")])]
    a = bevindingen.acties([], replace(d, todo=todo), korte_namen={"Anna Lee": "Anna", "Piet Vos": "Piet"})
    assert a == [("Groep", [("Titel", "Algemeen", "De uren van Lee en de rest van Vos.")])]


def test_todo_toont_titel_zonder_punt_en_toelichting_met_hoofdletter(tmp_path):
    d = kaart_dossier(tmp_path)
    todo = [
        (
            "Groep",
            [
                ("Telefoonvergoeding:", "is dit afgesproken?"),
                ("Afgesloten.", "ja"),
                ("Vraag blijft staan?", ""),
                ("Dubbel...", "klaar."),
            ],
        )
    ]
    a = bevindingen.acties([], replace(d, todo=todo))
    assert a == [
        (
            "Groep",
            [
                ("Telefoonvergoeding", "Algemeen", "Is dit afgesproken?"),
                ("Afgesloten", "Algemeen", "Ja"),
                ("Vraag blijft staan?", "Algemeen", ""),
                ("Dubbel", "Algemeen", "Klaar."),
            ],
        )
    ]
