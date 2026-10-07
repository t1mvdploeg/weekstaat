"""Tests voor het rekenmodel `overzicht.maak`: de voorbeeldset geeft sluitende cijfers en kleine gevallen laten het
gedrag zien (toezegging, oordeel van de gebruiker, eigen keuze, urenstaat die afwijkt, bank en kaart)."""

import shutil
from dataclasses import replace

import pandas as pd
import pytest
from conftest import MEDEWERKERS, VOORBEELD
from hulp import (
    DAAN,
    FACTUREN,
    FEMKE,
    IBAN_G,
    IBAN_GEWOON,
    KAART_B,
    aansluiting,
    dagregels,
    kaart_dossier,
    mini_dossier,
    orders_basis,
    overzicht_van,
    zonder_betalingen,
)

from weekstaat import aansluiten, dossier, overzicht
from weekstaat.aansluiten import CW, GEEN, NOG, OW
from weekstaat.overzicht import O_BLOK, O_DAG, O_DAGV, O_DUBBEL, O_LOS, O_TOEZEGGING, OORZAKEN, maak

SANNE = "Sanne Bakker"
THIJMEN = "Thijmen Lucas Van Dijk"


@pytest.fixture(scope="module")
def voorbeeld() -> dossier.Dossier:
    return dossier.lees(VOORBEELD)


@pytest.fixture(scope="module")
def overzichten(voorbeeld):
    """Het overzicht van elke medewerker in de voorbeeldset."""
    return [overzicht_van(voorbeeld, naam) for naam in MEDEWERKERS]


def zonder_saldo(d: dossier.Dossier, tmp_path) -> dossier.Dossier:
    """Hetzelfde dossier zonder `saldo <naam>.csv`: de map is een kopie zonder dat bestand."""
    map_ = tmp_path / "zonder-saldo"
    shutil.copytree(d.map, map_)
    for pad in map_.glob("saldo *.csv"):
        pad.unlink()
    return replace(d, map=map_)


# ---------- de voorbeeldset ----------


def test_verschillen_tellen_op_tot_factuur_min_order(overzichten):
    for o in overzichten:
        v = o.verschillen
        assert round((v.fact - v.order).sum(), 2) == round(o.stand["gefactureerd"] - o.stand["op_orders"], 2)


def test_per_oorzaak_en_per_toewijzing_hebben_hetzelfde_totaal(overzichten):
    """Blok 2 en blok 3 van de Stand tellen dezelfde regels. Blok 3 toont het bedrag bruto (zonder teken), daarom heeft
    het ook een kolom `verschil` met teken: die telt op tot blok 2."""
    for o in overzichten:
        oorzaak, toewijzing = o.per_oorzaak(), o.per_toewijzing()
        assert oorzaak.regels.sum() == toewijzing.regels.sum() == len(o.verschillen)
        assert round(toewijzing.verschil.sum(), 2) == round(oorzaak.bedrag.sum(), 2)
        assert round(oorzaak.bedrag.sum(), 2) == round(o.stand["gefactureerd"] - o.stand["op_orders"], 2)
        assert toewijzing.bedrag.sum() >= abs(toewijzing.verschil.sum()) - 0.005
        assert list(toewijzing.toewijzing) == list(overzicht.TOEWIJZINGEN)


def test_per_oorzaak_heeft_alleen_voorkomende_oorzaken_in_vaste_volgorde(overzichten):
    for o in overzichten:
        namen = list(o.per_oorzaak().oorzaak)
        assert namen == [x for x in OORZAKEN if x in set(o.verschillen.oorzaak)]


def test_betaald_nooit_meer_dan_op_orders(overzichten):
    """Per rekening wordt het aandeel van een order op de cent afgerond; samen kan een order daardoor één cent meer
    ontvangen dan zijn bedrag. Verder is er nooit meer betaald dan op de orders staat."""
    for o in overzichten:
        assert o.stand["betaald"] <= o.stand["op_orders"] + 0.01 * len(o.orders)
        assert (o.orders.betaald_ex <= o.orders.waarvan_medewerker + 0.01).all()
        assert (o.orders.ontvangen <= o.orders.incl + 0.01).all()
        assert o.stand["betaald"] == pytest.approx(o.orders.betaald_ex.sum(), abs=0.005)


def test_stand_sluit_aan_op_de_bronnen(overzichten, voorbeeld):
    for o in overzichten:
        u = voorbeeld.uren[voorbeeld.uren.medewerker == o.medewerker]
        assert o.stand["gefactureerd"] == pytest.approx(u.netto.sum(), abs=0.005)
        assert o.stand["urenstaat"] == pytest.approx(o.weken.urenstaat_bedrag.sum(), abs=0.005)
        a = o.aansluiting
        assert o.stand["op_orders"] == pytest.approx(
            a.per_dag.order_bedrag_uren.sum() + a.per_dag.order_onkosten.sum(), abs=0.005
        )


def test_per_dag_heeft_alleen_dagen_met_een_bedrag(overzichten):
    for o in overzichten:
        bedragen = o.per_dag[["fact_bedrag_uren", "fact_onkosten", "order_bedrag_uren", "order_onkosten"]]
        assert (bedragen.abs() > 0.005).any(axis=1).all()


def test_weken_zijn_aaneengesloten_en_hebben_een_status(overzichten):
    for o in overzichten:
        eerste = o.weken.van.min() - pd.Timedelta(days=o.weken.van.min().weekday())
        assert len(o.weken) == (o.per_dag.datum.max() - eerste).days // 7 + 1
        assert set(o.weken.status) <= set(overzicht.WEEKSTATUSSEN)
        assert o.weken.fact.sum() == pytest.approx(o.stand["gefactureerd"], abs=0.005)
        assert o.weken.order.sum() == pytest.approx(o.stand["op_orders"], abs=0.005)


def test_weken_zonder_werk_staan_er_ook_in(overzichten):
    sanne = overzichten[0]
    w = sanne.weken.set_index("week")
    assert w.status["2025 wk 36"] == overzicht.W_NIET_GEWERKT  # alleen verlof
    assert w.status["2025 wk 28"] == overzicht.W_GEEN
    assert w.status["2025 wk 32"] == overzicht.W_DEELS


def test_perioden_tellen_op_tot_de_stand(overzichten):
    for o in overzichten:
        p = o.perioden
        assert p.fact.sum() == pytest.approx(o.stand["gefactureerd"], abs=0.005)
        assert p.order.sum() == pytest.approx(o.stand["op_orders"], abs=0.005)
        assert list(p.periode) == list(dict.fromkeys(o.weken.periode))


def test_periode_zonder_order_heeft_status_geen_order(overzichten):
    p = overzichten[0].perioden.set_index("periode")
    assert p.status["2025 wk 33-36"] == "Geen order"
    assert p.status["2025 wk 29-32"] == "Verschil"


def test_orders_hebben_een_kolom_per_rekening(overzichten):
    o = overzichten[0]
    namen = [f"ontvangen {r.naam}" for r in o.inst.rekeningen]
    assert all(n in o.orders.columns for n in namen)
    assert o.orders[namen].sum(axis=1).round(2).tolist() == o.orders.ontvangen.round(2).tolist()
    assert set(o.orders.order) == {"I01250361", "I02250362", "I01250491"}


def test_orders_oordeel_in_woorden(overzichten, voorbeeld):
    oordeel = overzichten[0].orders.set_index("order").oordeel
    assert oordeel["I01250361"] == "Betaald"
    # niet in de bankexport, maar de factuur is op de kaart afgeletterd
    assert oordeel["I02250362"].startswith("Betaald volgens de boekhouding")
    zonder_kaart = overzicht_van(replace(voorbeeld, kaart=None), SANNE).orders.set_index("order").oordeel
    assert zonder_kaart["I02250362"] == "Niet ontvangen"


def test_facturen_zonder_order_sluiten_aan_tussen_de_blokken(overzichten):
    for o in overzichten:
        z, dag, f = o.zonder_order, o.zonder_order_dag, o.facturen
        assert z.zonder_order.sum() == pytest.approx(dag.totaal.sum(), abs=0.005)
        assert z.zonder_order.sum() == pytest.approx(f.zonder_order.sum(), abs=0.005)
        assert (z.zonder_order == (z.bedrag_uren + z.onkosten).round(2)).all()
        assert (z.ontvangen == 0).all()


def test_zonder_order_geeft_soort_onkosten_en_ander_stuk(overzichten, voorbeeld):
    dag = overzichten[0].zonder_order_dag
    assert set(dag.soort_onkosten) == {"", "dagvergoeding"}  # 10,07 komt vaak terug
    assert overzichten[0].zonder_order.ander_stuk.eq("").all()  # de voorbeeldset heeft geen zonder-order.csv


def test_facturen_hebben_per_factuur_de_omzet(overzichten, voorbeeld):
    o = overzichten[0]
    u = voorbeeld.uren[voorbeeld.uren.medewerker == SANNE]
    assert len(o.facturen) == u.factuurnummer.nunique()
    assert o.facturen.netto.sum() == pytest.approx(u.netto.sum(), abs=0.005)


def test_bank_los_heeft_de_boekingen_die_niet_passen(overzichten):
    los = overzichten[0].bank_los
    assert los is not None and len(los)
    assert (los.orders == "").sum() > 0
    assert not los.betreft.str.startswith("nee").any()


# ---------- zonder bank, met een toezegging, met een oordeel ----------


def test_zonder_bank_is_betaald_nul_en_bank_los_none(voorbeeld):
    d = replace(voorbeeld, bank=None)
    o = overzicht_van(d, SANNE)
    assert o.stand["betaald"] == 0
    assert o.bank_los is None
    assert (o.orders.ontvangen == 0).all() and o.orders.oordeel.eq("Geen bank in het dossier").all()
    assert o.perioden.betaling.eq("").all()


def toezegging(*rijen) -> pd.DataFrame:
    """Rijen (nr, week, maand, omschrijving, eenheid, aantal, prijs, bedrag) voor Sanne, met datum 10-11-2025."""
    kolommen = ["nr", "week", "maand", "omschrijving", "eenheid", "aantal", "prijs", "bedrag"]
    df = pd.DataFrame(rijen, columns=kolommen)
    df.insert(0, "medewerker", SANNE)
    df.insert(2, "datum", pd.Timestamp("2025-11-10"))
    return df


def test_week_op_een_toezegging_krijgt_eigen_oorzaak(voorbeeld):
    t = toezegging(
        ("T-001", "2025 wk 33", "", "Sanne week 33", "Uren", 32.0, 37.31, 1193.92),  # geen order voor deze week
        ("T-001", "2025 wk 29", "", "Sanne week 29", "Uren", 32.0, 37.31, 1193.92),  # staat ook op een order
        ("T-001", "2025 wk 34", "", "Sanne week 34", "Uren", None, 37.31, None),  # zonder bedrag
    )
    o = overzicht_van(replace(voorbeeld, toezegging=t), SANNE)
    v = o.verschillen
    assert v[(v.week == "2025 wk 33") & (v.oorzaak == O_TOEZEGGING)].fact.sum() == pytest.approx(1193.92)
    # week 34 staat zonder bedrag op de toezegging: dat telt niet als staan op een toezegging
    assert v[(v.week == "2025 wk 34")].oorzaak.tolist() == [O_BLOK]
    assert v[v.oorzaak == O_TOEZEGGING].toewijzing.eq(OW).all()
    assert "T-001 van 10-11-2025" in v[v.oorzaak == O_TOEZEGGING].toelichting.iloc[0]
    w = o.weken.set_index("week")
    assert "Op toezegging T-001, niet op een order" in w.opmerking["2025 wk 33"]
    assert "Ook op toezegging T-001: dubbel" in w.opmerking["2025 wk 29"]
    blok_d = o.toezegging.set_index("week_of_maand")
    assert blok_d.soort["2025 wk 33"] == overzicht.T_ALLEEN
    assert blok_d.soort["2025 wk 29"] == overzicht.T_DUBBEL
    assert blok_d.soort["2025 wk 34"] == overzicht.T_LEEG
    assert blok_d.op_order["2025 wk 29"] == "I01250361"
    z = o.zonder_order
    assert z.op_toezegging.sum() == pytest.approx(1193.92)  # alleen week 33 zit bij de uren zonder order
    assert (z.niet_op_toezegging == (z.zonder_order - z.op_toezegging).round(2)).all()
    assert o.stand["gefactureerd"] == overzicht_van(voorbeeld, SANNE).stand["gefactureerd"]


def test_zonder_toezegging_is_het_blok_leeg(overzichten):
    assert all(o.toezegging is None for o in overzichten)
    assert (overzichten[0].zonder_order.op_toezegging == 0).all()


def test_toezegging_van_een_andere_medewerker_telt_niet_mee(voorbeeld):
    t = toezegging(("T-001", "2025 wk 33", "", "Week 33", "Uren", 32.0, 37.31, 1193.92)).assign(medewerker=THIJMEN)
    o = overzicht_van(replace(voorbeeld, toezegging=t), SANNE)
    assert o.toezegging is None and O_TOEZEGGING not in set(o.verschillen.oorzaak)


def test_reiskosten_op_een_toezegging_staan_naast_de_dagvergoeding(voorbeeld):
    t = toezegging(("T-002", "", "2025-08", "Reiskosten augustus - 4 dagen", "Kilometers", 80.0, 0.19, 15.2))
    o = overzicht_van(replace(voorbeeld, toezegging=t), SANNE)
    r = o.toezegging.iloc[0]
    assert r.soort == overzicht.T_REIS and r.week_of_maand == "2025-08"
    assert r.gefactureerd.startswith("4 x dagvergoeding, samen 40,28")
    assert "20 km per dag = 3,80 per dag." in r.betekenis
    assert "Op orders staan over deze maand geen kilometers." in r.betekenis
    # een dagvergoeding in een maand met een toezegging verwijst naar blok D
    v = o.verschillen
    assert "blok D" in v[(v.week == "2025 wk 33") & (v.oorzaak == O_DAGV)].toelichting.iloc[0]


def test_saldo_bestand_gaat_voor_het_voorstel(voorbeeld, tmp_path):
    zonder = overzicht_van(zonder_saldo(voorbeeld, tmp_path), SANNE).verschillen.set_index(["week", "oorzaak"])
    met = overzicht_van(voorbeeld, SANNE).verschillen.set_index(["week", "oorzaak"])
    # week 32 heeft in het voorbeeld een oordeel "nog uitzoeken", het voorstel is "opdrachtgever te weinig opgenomen"
    sleutel = ("2025 wk 32", O_DAG)
    assert zonder.voorstel[sleutel] == OW and zonder.toewijzing[sleutel] == OW
    assert met.voorstel[sleutel] == NOG and met.toewijzing[sleutel] == NOG
    saldo = voorbeeld.saldo(SANNE).set_index("week")
    assert met.toelichting[sleutel] == saldo.toelichting["2025 wk 32"]
    # een week zonder oordeel houdt zijn voorstel
    assert met.voorstel[("2025 wk 28", O_BLOK)] == OW
    # het oordeel verandert niets aan de bedragen
    assert met.verschil.sum() == pytest.approx(zonder.verschil.sum())


def test_saldo_met_bedragen_is_een_extra_post(voorbeeld, tmp_path):
    """Twee extra posten die tegen elkaar wegvallen (dezelfde kilometers aan beide kanten): ze staan als eigen regels
    onder de oorzaak kilometers dubbel, in het blok waar de week in valt."""
    d = zonder_saldo(voorbeeld, tmp_path)
    kolommen = ["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"]
    extra = pd.DataFrame(
        [
            [
                "2025 wk 22-23",
                "Km dubbel (bureau)",
                12.5,
                0.0,
                "Bureau te veel gefactureerd",
                "Twee keer gefactureerd.",
            ],
            [
                "2025 wk 22-23",
                "Km dubbel (opdrachtgever)",
                0.0,
                12.5,
                "Opdrachtgever te veel opgenomen",
                "Op twee orders.",
            ],
        ],
        columns=kolommen,
    )
    basis = overzicht_van(d, SANNE)
    dossier.schrijf(d.map, saldo={SANNE: extra})
    o = overzicht_van(d, SANNE)
    v = o.verschillen[o.verschillen.oorzaak == O_DUBBEL]
    assert v.week.tolist() == ["2025 wk 22-23"] * 2 and v.periode.tolist() == ["2025 wk 21-24"] * 2
    assert v.fact.tolist() == [12.5, 0] and v.order.tolist() == [0, 12.5]
    assert v.toewijzing.tolist() == ["Bureau te veel gefactureerd", "Opdrachtgever te veel opgenomen"]
    assert (v.dagen == "").all() and (v.facturen == "").all()
    assert o.stand == basis.stand  # per saldo nul
    assert o.per_oorzaak().set_index("oorzaak").regels[O_DUBBEL] == 2
    assert o.perioden.verschil.sum() == pytest.approx(basis.perioden.verschil.sum())  # niet in het blok meegeteld


SALDO_KOLOMMEN = ["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"]


def _saldo_van(voorbeeld, tmp_path, *rijen) -> dossier.Dossier:
    d = zonder_saldo(voorbeeld, tmp_path)
    dossier.schrijf(d.map, saldo={SANNE: pd.DataFrame(list(rijen), columns=SALDO_KOLOMMEN)})
    return d


def test_saldo_met_een_verkeerde_soort_noemt_bestand_en_regel(voorbeeld, tmp_path):
    d = _saldo_van(
        voorbeeld, tmp_path, ["2025 wk 32", "Uren", None, None, NOG, ""], ["2025 wk 28", "Uur", None, None, NOG, ""]
    )
    with pytest.raises(ValueError, match=r"saldo Sanne Bakker\.csv: regel 3, kolom soort: 'Uur' is Uren of Onkosten"):
        overzicht_van(d, SANNE)


def test_saldo_met_een_soort_in_kleine_letters_is_goed(voorbeeld, tmp_path):
    klein = _saldo_van(voorbeeld, tmp_path / "a", ["2025 wk 32", "uren", None, None, NOG, "Klein."])
    groot = _saldo_van(voorbeeld, tmp_path / "b", ["2025 wk 32", "Uren", None, None, NOG, "Klein."])
    pd.testing.assert_frame_equal(overzicht_van(klein, SANNE).verschillen, overzicht_van(groot, SANNE).verschillen)
    assert overzicht_van(klein, SANNE).verschillen.set_index(["week", "oorzaak"]).voorstel[("2025 wk 32", O_DAG)] == NOG


def test_saldo_voor_een_week_zonder_verschil_van_die_soort_is_een_fout(voorbeeld, tmp_path):
    """Een typfout in de week of een verschil dat er niet meer is, werd stil genegeerd."""
    d = _saldo_van(voorbeeld, tmp_path, ["2025 wk 32", "Onkosten", None, None, NOG, ""])
    with pytest.raises(
        ValueError, match=r"saldo Sanne Bakker\.csv: regel 2: er is in 2025 wk 32 geen verschil van de soort Onkosten"
    ):
        overzicht_van(d, SANNE)
    d = _saldo_van(voorbeeld, tmp_path / "x", ["2025 wk 5", "Uren", None, None, NOG, ""])
    with pytest.raises(ValueError, match=r"regel 2: er is in 2025 wk 5 geen verschil van de soort Uren"):
        overzicht_van(d, SANNE)


def test_een_oordeel_voor_een_week_zonder_verschil_is_ook_in_de_aansluiting_een_fout(voorbeeld, tmp_path):
    d = _saldo_van(voorbeeld, tmp_path, ["2025 wk 32", "Onkosten", None, None, NOG, ""])
    with pytest.raises(ValueError, match=r"saldo Sanne Bakker\.csv: regel 2: er is in 2025 wk 32 geen verschil"):
        aansluiting(d, SANNE)


def test_saldo_met_een_enkele_extra_post_sluit_niet_aan(voorbeeld, tmp_path):
    d = zonder_saldo(voorbeeld, tmp_path)
    extra = pd.DataFrame(
        [["2025 wk 22-23", "Km", 12.5, 0.0, CW, ""]],
        columns=["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"],
    )
    dossier.schrijf(d.map, saldo={SANNE: extra})
    with pytest.raises(ValueError, match=r"tellen niet op tot het totale verschil.*saldo Sanne Bakker\.csv"):
        overzicht_van(d, SANNE)


def test_saldo_met_onbekende_toewijzing_is_een_fout(voorbeeld, tmp_path):
    d = zonder_saldo(voorbeeld, tmp_path)
    fout = pd.DataFrame(
        [["2025 wk 32", "Uren", None, None, "Misschien", ""]],
        columns=["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"],
    )
    dossier.schrijf(d.map, saldo={SANNE: fout})
    with pytest.raises(ValueError, match=r"2025 wk 32.*Misschien"):
        overzicht_van(d, SANNE)


def test_eigen_keuze_gaat_voor_alles(voorbeeld):
    basis = overzicht_van(voorbeeld, SANNE)
    sleutel = ("2025 wk 32", O_DAG)  # heeft een oordeel in het saldo-bestand
    o = overzicht_van(voorbeeld, SANNE, eigen={sleutel: CW, ("2025 wk 28", O_BLOK): NOG})
    v = o.verschillen.set_index(["week", "oorzaak"])
    assert v.toewijzing[sleutel] == CW and v.eigen[sleutel] == CW
    assert v.voorstel[sleutel] == basis.verschillen.set_index(["week", "oorzaak"]).voorstel[sleutel]
    assert v.toewijzing[("2025 wk 28", O_BLOK)] == NOG
    assert v.eigen[("2025 wk 33", O_BLOK)] == ""  # zonder keuze geldt het voorstel
    assert o.per_toewijzing().set_index("toewijzing").regels[CW] == 1
    assert (
        o.per_toewijzing().set_index("toewijzing").regels[NOG]
        == (basis.per_toewijzing().set_index("toewijzing").regels[NOG])
    )


def test_eigen_keuze_buiten_de_lijst_is_een_fout(voorbeeld):
    with pytest.raises(ValueError, match="Misschien"):
        overzicht_van(voorbeeld, SANNE, eigen={("2025 wk 32", O_DAG): "Misschien"})


# ---------- de goedgekeurde urenstaat ----------


def afwijkende_uren(d: dossier.Dossier) -> tuple[pd.DataFrame, str]:
    """De uren van de voorbeeldset met een goedgekeurde urenstaat waarvan het bedrag 10,00 afwijkt van de factuur."""
    uren = d.uren.copy()
    i = uren.index[(uren.medewerker == THIJMEN) & (uren.status == "Goedgekeurd") & (uren.datum == "2025-05-20")][0]
    uren.loc[i, "factuurbedrag"] += 10
    return uren, "2025 wk 21"


def test_urenstaat_wijkt_af_zonder_regel_is_een_fout(voorbeeld):
    uren, week = afwijkende_uren(voorbeeld)
    with pytest.raises(ValueError, match=rf"{week}.*urenstaat-afwijkend"):
        maak(aansluiting(voorbeeld, THIJMEN, uren), voorbeeld)


def test_urenstaat_afwijkend_met_regel_is_goed(voorbeeld):
    uren, week = afwijkende_uren(voorbeeld)
    bedrag = overzicht_van(voorbeeld, THIJMEN).weken.set_index("week").fact[week] + 10
    regel = pd.DataFrame(
        [[THIJMEN, week, bedrag, "Een dag staat op twee goedgekeurde urenstaten."]],
        columns=["medewerker", "week", "bedrag", "toelichting"],
    )
    # de regel van een andere medewerker telt niet mee
    ander = regel.assign(medewerker=SANNE, week="2025 wk 28")
    d = replace(voorbeeld, urenstaat_afwijkend=pd.concat([regel, ander]))
    o = maak(aansluiting(d, THIJMEN, uren), d)
    w = o.weken.set_index("week")
    assert w.urenstaat_bedrag[week] == pytest.approx(bedrag)
    assert w.opmerking[week] == "Een dag staat op twee goedgekeurde urenstaten."
    assert o.stand["urenstaat"] == pytest.approx(o.stand["gefactureerd"] + 10)


def test_goedgekeurde_urenstaat_telt_dubbele_regels_een_keer():
    """Factuurregels staan soms dubbel in de export: een regel die in alles gelijk is, telt één keer."""
    regel = {
        "urenstaat": "TS-1", "datum": pd.Timestamp("2025-03-03"), "uren": 8.0, "soort": "Normale Uren",
        "factuurnummer": "F1", "bedrag": 100.0, "factuurbedrag": 100.0, "status": "Goedgekeurd", "week": "2025 wk 10",
    }  # fmt: skip
    u = pd.DataFrame([regel, regel, {**regel, "datum": pd.Timestamp("2025-03-04")}, {**regel, "status": "Corrected"}])
    assert overzicht._geldende_urenstaat(u).to_dict() == {"2025 wk 10": 200.0}


# ---------- bank, notities en kaart ----------


def test_bank_notitie_bepaalt_of_een_boeking_de_medewerker_betreft(voorbeeld):
    notities = pd.DataFrame(
        [["nabetaling Kim", "Sanne", "Hoort bij Sanne."], ["week 35-36 aanvulling", "Thijmen", "Van een ander."]],
        columns=["tekst", "betreft", "opmerking"],
    )
    o = overzicht_van(replace(voorbeeld, bank_notities=notities), SANNE)
    los = o.bank_los
    na = los[los.omschrijving.str.contains("nabetaling Kim")]
    assert len(na) == 2 and na.betreft.eq("ja").all() and na.opmerking.eq("Hoort bij Sanne.").all()
    assert not los.omschrijving.str.contains("week 35-36 aanvulling").any()  # betreft een ander: valt weg
    # voor de andere medewerker is het omgekeerd
    ander = overzicht_van(replace(voorbeeld, bank_notities=notities), THIJMEN).bank_los
    assert ander[ander.omschrijving.str.contains("week 35-36 aanvulling")].betreft.eq("ja").all()


def test_order_zonder_bank_is_betaald_volgens_de_boekhouding(voorbeeld):
    """Staat een order niet in de bank, maar zijn alle facturen van zijn dagen afgeletterd (niet meer open op de kaart),
    dan telt hij als betaald."""
    basis = overzicht_van(replace(voorbeeld, kaart=None), SANNE)  # zonder kaart is er niets afgeletterd
    order = "I02250362"
    assert basis.orders.set_index("order").oordeel[order] == "Niet ontvangen"
    eff = basis.aansluiting.uren
    dagen = basis.aansluiting.per_dag.datum[basis.aansluiting.per_dag.order == order]
    facturen = sorted(set(eff.factuurnummer[eff.datum.isin(dagen)].dropna()))
    kaart = pd.DataFrame(
        [[pd.Timestamp("2025-07-01"), "factuur", "F-ANDERS", "", "", 100.0]],
        columns=["datum", "soort", "nummer", "omschrijving", "rekening", "bedrag"],
    )
    afgeletterd = overzicht_van(replace(voorbeeld, kaart=kaart), SANNE).orders.set_index("order")
    assert afgeletterd.oordeel[order].startswith("Betaald volgens de boekhouding")
    assert all(f in afgeletterd.oordeel[order] for f in facturen)
    assert afgeletterd.betaald_ex[order] == pytest.approx(afgeletterd.waarvan_medewerker[order], abs=0.01)
    # staat een van de facturen nog open op de kaart, dan blijft het "niet ontvangen". De kaart moet kloppen met de
    # bank en de orders (anders stopt het model), dus de factuur staat open voor wat hij incl. btw is.
    gewone = overzicht_van(replace(voorbeeld, kaart=kaart), SANNE).kaart.facturen.set_index("factuur")
    open_ = kaart.assign(nummer=facturen[0], bedrag=gewone.incl[facturen[0]])
    nog_open = overzicht_van(replace(voorbeeld, kaart=open_), SANNE).orders.set_index("order")
    assert nog_open.oordeel[order] == "Niet ontvangen"


def test_vervallen_orders_van_de_medewerker(voorbeeld):
    vervallen = pd.DataFrame(
        [["I02250362", "I01250491", "Vervangen."]], columns=["order", "vervangen_door", "toelichting"]
    )
    o = overzicht_van(replace(voorbeeld, vervallen=vervallen), SANNE)
    assert "I02250362" not in set(o.orders.order)
    v = o.vervallen_orders()
    assert v.order.tolist() == ["I02250362"] and v.waarvan_medewerker.iloc[0] == pytest.approx(596.96)
    assert overzicht_van(voorbeeld, SANNE).vervallen_orders().empty


# ---------- kleine gevallen ----------


def test_dagvergoeding_is_een_bedrag_dat_vaak_terugkomt_de_rest_een_losse_post(voorbeeld):
    """Een onkostenbedrag dat minstens vijf keer voorkomt is een vast bedrag per dag; een eenmalig bedrag is een losse
    post. Een onkostenbedrag dat op een order staat, zit niet in deze twee."""
    v = overzicht_van(voorbeeld, SANNE).verschillen
    assert set(v[v.oorzaak == O_DAGV].fact.round(2)) == {40.28}  # vier keer 10,07 per week
    assert v[v.oorzaak == O_LOS].fact.round(2).tolist() == [495.65]
    assert (v[v.oorzaak == O_DAGV].toelichting.str.contains("4 x 10,07")).all()


def test_geen_enkele_dag_met_een_bedrag_is_een_fout(voorbeeld):
    a = aansluiting(voorbeeld, SANNE)
    leeg = replace(a, per_dag=a.per_dag.iloc[0:0])
    with pytest.raises(ValueError, match="geen enkele dag"):
        maak(leeg, voorbeeld)


def test_per_dag_die_niet_sluit_op_de_bronnen_is_een_fout(voorbeeld):
    a = aansluiting(voorbeeld, SANNE)
    scheef = a.per_dag.copy()
    scheef.loc[scheef.index[0], "fact_bedrag_uren"] += 1
    with pytest.raises(ValueError, match="per dag sluit niet aan"):
        maak(replace(a, per_dag=scheef), voorbeeld)


def test_geen_order_is_geen_getal(overzichten):
    assert GEEN == "—"  # de dag zonder order heet zo in per_dag; het model laat hem weg uit de lijst met orders
    for o in overzichten:
        assert GEEN not in set(o.orders.order)
        assert not o.verschillen.orders.str.contains(GEEN).any()


def test_uitleg_noemt_de_namen_uit_de_instellingen(voorbeeld):
    inst = replace(voorbeeld.inst, bureau="Studio Noord", opdrachtgever="Oeverland")
    tekst = overzicht.uitleg(inst)
    assert set(tekst) == set(OORZAKEN)
    assert "Studio Noord" in tekst[O_BLOK] and "Oeverland" in tekst[O_TOEZEGGING]
    assert not any(woord in " ".join(tekst.values()).lower() for woord in ("inleenorder", "timesheet"))


VERBODEN = ("hij ", "vergoedt gedeclareerde", "betaalt hij", "pas na een factuur", "betaalt er pas")


def test_uitleg_en_toelichting_beweren_niets_over_de_opdrachtgever(voorbeeld):
    """De tool ziet alleen de bronnen: de teksten zijn waarnemingen of een vraag om na te gaan, geen bewering over
    wat de opdrachtgever vergoedt of wanneer hij betaalt."""
    inst = replace(voorbeeld.inst, bureau="Studio Noord", opdrachtgever="Oeverland")
    teksten = list(overzicht.uitleg(inst).values())
    teksten += [overzicht._toelichting(o, inst, "T-001 van 10-11-2025") for o in OORZAKEN if o != O_DUBBEL]
    for tekst in teksten:
        assert not any(woord in tekst.lower() for woord in VERBODEN), tekst
    assert "Oeverland" in overzicht.uitleg(inst)[O_DAGV]
    assert "Oeverland" in overzicht._toelichting(O_DAGV, inst, "")
    assert "T-001 van 10-11-2025" in overzicht._toelichting(O_TOEZEGGING, inst, "T-001 van 10-11-2025")
    assert "Oeverland" in overzicht.uitleg(inst)[O_TOEZEGGING]


def test_teksten_van_een_overzicht_met_toezegging_hebben_geen_beweringen(voorbeeld):
    t = toezegging(
        ("T-001", "2025 wk 33", "", "Sanne week 33", "Uren", 32.0, 37.31, 1193.92),
        ("T-001", "2025 wk 29", "", "Sanne week 29", "Uren", 32.0, 37.31, 1193.92),
        ("T-001", "2025 wk 34", "", "Sanne week 34", "Uren", None, 37.31, None),
        ("T-002", "", "2025-08", "Reiskosten augustus - 4 dagen", "Kilometers", 80.0, 0.19, 15.2),
    )
    o = overzicht_van(replace(voorbeeld, toezegging=t), SANNE)
    teksten = list(o.verschillen.toelichting) + list(o.toezegging.betekenis) + list(o.weken.opmerking)
    for tekst in teksten:
        assert not any(woord in tekst.lower() for woord in VERBODEN), tekst


def test_reiskosten_zin_noemt_de_toezegging_als_feit(voorbeeld):
    t = toezegging(("T-002", "", "2025-08", "Reiskosten augustus - 4 dagen", "Kilometers", 80.0, 0.19, 15.2))
    v = overzicht_van(replace(voorbeeld, toezegging=t), SANNE).verschillen
    tekst = v[(v.week == "2025 wk 33") & (v.oorzaak == O_DAGV)].toelichting.iloc[0]
    assert "Op toezegging T-002 staan over deze maand ook reiskosten (blad Zonder order, blok D)." in tekst


def extra_posten(d: dossier.Dossier, week: str, blok: str) -> overzicht.Overzicht:
    """Twee extra posten die tegen elkaar wegvallen in `week`, met de instelling `blok`."""
    kolommen = ["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"]
    posten = pd.DataFrame(
        [[week, "Km A", 5.0, 0.0, CW, ""], [week, "Km B", 0.0, 5.0, "Opdrachtgever te veel opgenomen", ""]],
        columns=kolommen,
    )
    dossier.schrijf(d.map, saldo={SANNE: posten})
    return overzicht_van(replace(d, inst=replace(d.inst, blok=blok)), SANNE)


@pytest.mark.parametrize(
    ("blok", "periode"), [("4 weken", "2020 wk 53-56"), ("week", "2020 wk 53"), ("maand", "2020-12")]
)
def test_periode_van_een_extra_post_volgt_de_instelling_ook_in_week_53(voorbeeld, tmp_path, blok, periode):
    """2020 heeft 53 weken: week 53 blijft bij blokken van vier weken '53-56', zoals de rest van de tool."""
    d = zonder_saldo(voorbeeld, tmp_path)
    o = extra_posten(d, "2020 wk 53", blok)
    assert o.verschillen[o.verschillen.oorzaak == O_DUBBEL].periode.tolist() == [periode] * 2


def test_periode_van_een_extra_post_voor_een_week_die_niet_bestaat_is_een_fout(voorbeeld, tmp_path):
    d = zonder_saldo(voorbeeld, tmp_path)  # 2025 heeft 52 weken
    with pytest.raises(ValueError, match=r"saldo Sanne Bakker\.csv.*2025 wk 53"):
        extra_posten(d, "2025 wk 53", "4 weken")
    with pytest.raises(ValueError, match=r"saldo Sanne Bakker\.csv.*week veertien"):
        extra_posten(d, "week veertien", "4 weken")


# ---------- de debiteurenkaart ----------


def kaart_van(d: dossier.Dossier, naam: str = FEMKE) -> overzicht.Kaartblok:
    return overzicht_van(d, naam).kaart


def test_kaart_sluit_aan_op_bank_en_orders(tmp_path):
    d = kaart_dossier(tmp_path)
    o = overzicht_van(d, FEMKE)
    k = o.kaart
    assert isinstance(k, overzicht.Kaartblok)
    # blok A: de hele kaart en per medewerker
    assert k.heel["open"] == pytest.approx(3905.88) and k.heel["ontvangsten"] == pytest.approx(-1548.80)
    assert k.heel["saldo"] == pytest.approx(2357.08) and k.heel["buiten_uren"] == pytest.approx(121.00)
    assert k.per_medewerker.to_dict() == pytest.approx({FEMKE: 3484.80, DAAN: 300.08})
    # blok B: wat er voor deze medewerker echt openstaat
    assert (k.open, k.af_ontvangen, k.bij_credit, k.af_verrekend, k.bij_niet_gefactureerd) == pytest.approx(
        (3484.80, 1548.80, 0.0, 0.0, 0.0)
    )
    assert k.echt_open == pytest.approx(1936.00) and k.controle == pytest.approx(1936.00)
    assert k.eraf == [] and k.per_saldo == pytest.approx(1936.00)
    assert sum(b for _, b, _ in k.regels) == pytest.approx(k.echt_open)
    assert [label for label, _, _ in k.regels][0] == "Open facturen van Femke op de kaart"
    # blok C: elke factuur van Femke naast wat de opdrachtgever voor haar betaalde
    f = k.facturen.set_index("factuur")
    assert list(k.facturen.columns) == [
        "factuur", "factuurdatum", "weken", "incl", "orders", "ontvangen", "echt_open", "kaart", "oordeel"
    ]  # fmt: skip
    assert f.incl.to_dict() == pytest.approx({"F-101": 1936.00, "F-102": 1548.80, "F-103": 1936.00})
    assert f.ontvangen.to_dict() == pytest.approx({"F-101": 1936.00, "F-102": 1548.80, "F-103": 0.0})
    assert f.echt_open.to_dict() == pytest.approx({"F-101": 0.0, "F-102": 0.0, "F-103": 1936.00})
    assert f.kaart.to_dict() == pytest.approx({"F-101": 0.0, "F-102": 1548.80, "F-103": 1936.00})
    assert f.orders.to_dict() == {"F-101": "I01250001", "F-102": "I01250002", "F-103": ""}
    assert f.weken["F-101"] == "2025 wk 11"
    assert f.oordeel["F-101"] == "Afgeletterd"
    assert f.oordeel["F-102"] == "Betaald door Oeverland; staat nog open op de kaart"
    assert f.oordeel["F-103"] == "Open: geen order, niets ontvangen"
    assert k.sluit is True
    # blok D: de ontvangsten op de kaart naast de bank
    assert list(k.ontvangsten.columns) == [
        "datum",
        "rekening",
        "omschrijving",
        "bedrag",
        "orders",
        "waarvan",
        "opmerking",
    ]
    assert k.ontvangsten.rekening.tolist() == ["G-rekening", "Gewone rekening"]
    assert k.ontvangsten.bedrag.tolist() == pytest.approx([464.64, 1084.16])
    assert k.ontvangsten.orders.tolist() == ["I01250002"] * 2
    assert k.ontvangsten.waarvan.tolist() == pytest.approx([464.64, 1084.16])
    assert k.ontvangsten.waarvan.sum() == pytest.approx(k.af_ontvangen)


def test_restant_op_de_kaart_hoort_bij_de_grotere_boeking_van_die_dag(tmp_path):
    """Kaart: ontvangst van 40,00; bank: één boeking van 363,00 op dezelfde dag en rekening. De 40,00 telt als los op
    de kaart voor die order en verschijnt in blok D met de orders van die boeking."""
    factuur = [("F-201", "2025-03-17", dagregels("2025-03-10", 5, 20.0))]  # 968,00 incl
    order = [("I01250003", "2025-03-17", FEMKE, dagregels("2025-03-10", 5, 25.0))]  # 1210,00 incl: 363,00 + 847,00
    bank = [("2025-04-14", IBAN_G, 363.00), ("2025-04-14", IBAN_GEWOON, 847.00)]
    kaart = [("2025-04-14", "ontvangst", "", IBAN_G, -40.00)]
    k = kaart_van(mini_dossier(tmp_path, factuur, order, bank, kaart, ander=False))
    r = k.ontvangsten.iloc[0]
    assert (r.bedrag, r.rekening, r.orders) == (40.00, "G-rekening", "I01250003")
    assert r.opmerking == "Lijkt een restant van de bankboeking van 363,00 op deze dag"
    # de factuur is afgeletterd en de opdrachtgever betaalde 242,00 meer: dat geld staat los op de kaart
    f = k.facturen.iloc[0]
    assert (f.kaart, f.ontvangen, f.echt_open) == (0.0, 1210.00, -242.00)
    assert f.oordeel == "Afgeletterd; Oeverland betaalde 242,00 meer dan Studio Noord factureerde; zie blad Verschillen"
    assert len(k.eraf) == 1 and k.eraf[0][1] == 242.00
    assert k.eraf[0][0].startswith("Daartegenover: door Oeverland meer betaald dan gefactureerd")
    assert k.echt_open == pytest.approx(0.0) and k.per_saldo == pytest.approx(-242.00)
    # staat de 40,00 op een dag zonder bankboeking, dan is er geen bewijs dat het geld van deze order is
    kaart = [("2025-04-15", "ontvangst", "", IBAN_G, -40.00)]
    ander = kaart_van(mini_dossier(tmp_path, factuur, order, bank, kaart, ander=False))
    assert ander.eraf == [] and ander.per_saldo == pytest.approx(0.0)
    assert (
        ander.ontvangsten.orders.tolist() == [""]
        and ander.ontvangsten.opmerking.iloc[0] == "Staat niet in de bankexport"
    )
    # twee grotere boekingen op die dag en rekening: geen van beide is de enige
    bank2 = [*bank, ("2025-04-14", IBAN_G, 400.00)]
    kaart_dag = [("2025-04-14", "ontvangst", "", IBAN_G, -40.00)]
    assert kaart_van(mini_dossier(tmp_path, factuur, order, bank2, kaart_dag, ander=False)).eraf == []


def test_meer_betaald_dan_gefactureerd_gaat_van_per_saldo_af(tmp_path):
    """De order staat hoger dan de factuur en is betaald; de factuur is afgeletterd en het teveel staat als restant
    van de betaling op de kaart."""
    kaart = [*KAART_B, ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -96.80)]
    d = kaart_dossier(
        tmp_path, tarief_a=42.0, bank_a=[("2025-04-14", IBAN_G, 609.84), ("2025-04-14", IBAN_GEWOON, 1422.96)],
        kaart=kaart,
    )  # fmt: skip
    k = kaart_van(d)
    f = k.facturen.set_index("factuur")
    assert f.echt_open["F-101"] == pytest.approx(-96.80) and f.kaart["F-101"] == 0.0
    assert k.echt_open == pytest.approx(1936.00) and k.controle == pytest.approx(1936.00)
    assert len(k.eraf) == 1 and k.eraf[0][1] == pytest.approx(96.80)
    assert k.eraf[0][0].startswith("Daartegenover: door Oeverland meer betaald dan gefactureerd")
    assert k.per_saldo == pytest.approx(1936.00 - 96.80)
    assert k.ontvangsten.waarvan.sum() == pytest.approx(k.af_ontvangen)  # het restant telt in blok B niet mee


def test_afgeletterd_terwijl_er_minder_is_betaald(tmp_path):
    d = kaart_dossier(
        tmp_path, tarief_a=38.0, bank_a=[("2025-04-14", IBAN_G, 551.76), ("2025-04-14", IBAN_GEWOON, 1287.44)]
    )
    k = kaart_van(d)
    f = k.facturen.set_index("factuur")
    assert f.kaart["F-101"] == 0.0 and f.echt_open["F-101"] == pytest.approx(96.80)
    assert f.oordeel["F-101"] == (
        "Afgeletterd, terwijl Oeverland 96,80 minder betaalde: dat deel staat niet op I01250001; zie blad Verschillen"
    )
    assert k.eraf == [] and k.echt_open == pytest.approx(k.controle)


def test_afgeletterd_terwijl_de_bank_niets_toont(tmp_path):
    """Geen bankregel voor order 1, maar de factuur van die dagen is niet meer open op de kaart: de order telt als
    betaald volgens de boekhouding, en dat geld telt mee als ontvangen."""
    d = kaart_dossier(tmp_path, bank_a=[])
    f = kaart_van(d).facturen.set_index("factuur")
    assert f.oordeel["F-101"] == "Afgeletterd"
    assert f.ontvangen["F-101"] == pytest.approx(1936.00)
    # de order telt als betaald (ontvangen en betaald in de stand), maar staat niet als ontvangst op de kaart: het
    # blok "af: ontvangen op orders" heeft alleen de ontvangsten van order 2
    o = overzicht_van(d, FEMKE)
    order = o.orders.set_index("order")
    assert order.oordeel["I01250001"].startswith("Betaald volgens de boekhouding")
    assert order.ontvangen["I01250001"] == pytest.approx(1936.00)
    assert o.kaart.af_ontvangen == pytest.approx(1548.80) and o.kaart.sluit is True


def test_creditorder_van_een_ander_is_verrekend(tmp_path):
    """Een betaling voor Femke waarop de opdrachtgever een creditorder van Daan inhield: de order van Femke telt als
    betaald, maar het geld is niet ontvangen."""
    factuur = FACTUREN[:1]
    orders = [
        ("I01250001", "2025-03-17", FEMKE, dagregels("2025-03-10", 5, 40.0)),  # 1936,00 incl
        ("I01250009", "2025-03-24", DAAN, [("2025-03-10", -5.0, 40.0)]),  # creditorder van -242,00 incl
    ]
    bank = [("2025-04-14", IBAN_G, 508.20), ("2025-04-14", IBAN_GEWOON, 1185.80)]
    kaart = [
        ("2025-03-17", "factuur", "F-101", "", 1936.00),
        ("2025-04-14", "ontvangst", "", IBAN_G, -508.20), ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -1185.80),
    ]  # fmt: skip
    k = kaart_van(mini_dossier(tmp_path, factuur, orders, bank, kaart))
    assert k.af_verrekend == pytest.approx(242.00) and k.af_ontvangen == pytest.approx(1694.00)
    assert k.echt_open == pytest.approx(0.0) and k.controle == pytest.approx(0.0)
    label, bedrag, uitleg = next(r for r in k.regels if r[0].startswith("Af: op de betaling staat een creditorder"))
    assert bedrag == pytest.approx(-242.00) and "I01250009" in uitleg and DAAN in uitleg
    assert "242,00 lager" in uitleg and "Ga na of de creditorder" in uitleg
    assert k.facturen.oordeel.iloc[0] == "Betaald door Oeverland; staat nog open op de kaart"
    assert sum(b for _, b, _ in k.regels) == pytest.approx(k.echt_open)
    # zonder namen in de instellingen staan er de standaardnamen, en een zin begint met een hoofdletter
    standaard = kaart_van(mini_dossier(tmp_path / "standaard", factuur, orders, bank, kaart, namen=""))
    assert next(u for lb, _, u in standaard.regels if lb.startswith("Bij: daarvan ontvangen")).startswith(
        "De opdrachtgever betaalde deze dagen"
    )


def test_kaart_zonder_ontvangsten_heeft_een_leeg_blok_d(tmp_path):
    """Alleen een open factuur op de kaart: alles wat de opdrachtgever betaalde is al afgeletterd."""
    k = kaart_van(kaart_dossier(tmp_path, kaart=[("2025-03-31", "factuur", "F-103", "", 1936.00)]))
    assert k.ontvangsten.empty and list(k.ontvangsten.columns)[-1] == "opmerking"
    assert k.heel["ontvangsten"] == 0.0 and k.echt_open == pytest.approx(1936.00) == pytest.approx(k.controle)
    assert k.per_medewerker.to_dict() == {FEMKE: 1936.00}


def test_kaart_die_niet_sluit_stopt_niet_maar_zegt_het(tmp_path):
    """De gebruiker kan een verschil in deze afgeleide controle niet in zijn invoer oplossen: het model geeft het
    kaartblok met `sluit` onwaar, de schrijver toont het verschil."""
    kaart = [("2025-03-24", "factuur", "F-102", "", 1000.00), *KAART_B[1:]]  # 548,80 te laag
    k = kaart_van(kaart_dossier(tmp_path, kaart=kaart))
    assert k.sluit is False
    assert round(k.echt_open - k.controle, 2) == -548.80
    assert sum(b for _, b, _ in k.regels) == pytest.approx(k.echt_open)


def test_zonder_kaart_is_kaart_none(tmp_path):
    d = kaart_dossier(tmp_path)
    assert overzicht_van(d, FEMKE).kaart is not None
    assert overzicht_van(replace(d, kaart=None), FEMKE).kaart is None


def test_kaart_zonder_bank_is_kaart_none(tmp_path):
    """Zonder bank is er niets om de kaart tegen te leggen: ook met een kaart is er geen blok."""
    d = kaart_dossier(tmp_path)
    assert d.kaart is not None
    assert overzicht_van(replace(d, bank=None), FEMKE).kaart is None


def scenario_restant(tmp_path) -> overzicht.Kaartblok:
    """Een restant op de kaart (40,00 van een boeking van 363,00) en een ontvangst die niet in de bank staat."""
    factuur = [("F-201", "2025-03-17", dagregels("2025-03-10", 5, 20.0))]
    order = [("I01250003", "2025-03-17", FEMKE, dagregels("2025-03-10", 5, 25.0))]
    bank = [("2025-04-14", IBAN_G, 363.00), ("2025-04-14", IBAN_GEWOON, 847.00)]
    kaart = [("2025-04-14", "ontvangst", "", IBAN_G, -40.00), ("2025-05-20", "ontvangst", "", IBAN_G, -12.00)]
    return kaart_van(mini_dossier(tmp_path, factuur, order, bank, kaart, ander=False))


def scenario_creditorder_van_een_ander(tmp_path) -> overzicht.Kaartblok:
    orders = [
        ("I01250001", "2025-03-17", FEMKE, dagregels("2025-03-10", 5, 40.0)),
        ("I01250009", "2025-03-24", DAAN, [("2025-03-10", -5.0, 40.0)]),
    ]
    bank = [("2025-04-14", IBAN_G, 508.20), ("2025-04-14", IBAN_GEWOON, 1185.80)]
    kaart = [
        ("2025-03-17", "factuur", "F-101", "", 1936.00),
        ("2025-04-14", "ontvangst", "", IBAN_G, -508.20), ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -1185.80),
    ]  # fmt: skip
    return kaart_van(mini_dossier(tmp_path, FACTUREN[:1], orders, bank, kaart))


def scenario_creditorder_na_afletteren(tmp_path) -> overzicht.Kaartblok:
    """De factuur van week 11 is afgeletterd; een creditorder voor een dag in die week is daarna op de betaling van
    order 2 verrekend (1548,80 min 242,00 = 1306,80). Die betaling staat nog niet afgeletterd op de kaart."""
    orders = [
        *orders_basis(),
        ("I01250009", "2025-03-24", FEMKE, [("2025-03-10", -5.0, 40.0)]),  # creditorder van -242,00 incl
    ]
    bank = [
        ("2025-04-14", IBAN_G, 580.80), ("2025-04-14", IBAN_GEWOON, 1355.20),  # order 1
        ("2025-04-25", IBAN_G, 392.04), ("2025-04-25", IBAN_GEWOON, 914.76),  # order 2 min de creditorder
    ]  # fmt: skip
    kaart = [
        ("2025-03-24", "factuur", "F-102", "", 1548.80), ("2025-03-31", "factuur", "F-103", "", 1936.00),
        ("2025-04-25", "ontvangst", "", IBAN_G, -392.04), ("2025-04-25", "ontvangst", "", IBAN_GEWOON, -914.76),
    ]  # fmt: skip
    return kaart_van(mini_dossier(tmp_path, FACTUREN, orders, bank, kaart, ander=False))


def scenario_deels_betaald(tmp_path) -> overzicht.Kaartblok:
    """Order 2 staat lager dan de factuur van week 12 (tarief 38 tegen 40): de factuur is deels betaald."""
    bank_b = [("2025-04-25", IBAN_G, 441.41), ("2025-04-25", IBAN_GEWOON, 1029.95)]  # 1471,36 incl
    kaart = [
        ("2025-03-24", "factuur", "F-102", "", 1548.80), ("2025-03-31", "factuur", "F-103", "", 1936.00),
        ("2025-04-25", "ontvangst", "", IBAN_G, -441.41), ("2025-04-25", "ontvangst", "", IBAN_GEWOON, -1029.95),
    ]  # fmt: skip
    d = kaart_dossier(tmp_path, orders=orders_basis(tarief_b=38.0), kaart=kaart, bank_b=bank_b)
    return kaart_van(d)


def test_creditorder_na_het_afletteren_komt_terug_als_open(tmp_path):
    k = scenario_creditorder_na_afletteren(tmp_path)
    assert k.bij_credit == pytest.approx(242.00)  # de creditorder incl. btw
    # F-103 (1936,00, geen order) staat gewoon open; de creditorder komt erbij
    assert k.sluit is True and k.echt_open == pytest.approx(1936.00 + 242.00) == pytest.approx(k.controle)
    assert k.af_ontvangen == pytest.approx(1306.80 + 242.00)
    label, bedrag, uitleg = next(r for r in k.regels if r[0].startswith("Bij: creditorder verrekend"))
    assert bedrag == pytest.approx(242.00) and "I01250009" in uitleg
    f = k.facturen.set_index("factuur")
    assert f.kaart["F-101"] == 0.0 and f.echt_open["F-101"] == pytest.approx(242.00)
    assert (
        f.oordeel["F-101"]
        == "Afgeletterd; creditorder I01250009 is daarna verrekend, daardoor telt 242,00 weer als open"
    )
    assert sum(b for _, b, _ in k.regels) == pytest.approx(k.echt_open)


def test_factuur_die_deels_is_betaald(tmp_path):
    k = scenario_deels_betaald(tmp_path)
    f = k.facturen.set_index("factuur")
    assert f.ontvangen["F-102"] == pytest.approx(1471.36) and f.echt_open["F-102"] == pytest.approx(77.44)
    assert f.oordeel["F-102"] == "Deels betaald: 1.471,36 ontvangen, 77,44 staat nog open"
    assert k.sluit is True and k.echt_open == pytest.approx(2013.44)


# De zinsdelen die de tool niet mag stellen: een oorzaak, een gebod of een conclusie over de boekhouding. De tool zegt
# wat hij in de bronnen ziet, of vraagt de lezer iets na te gaan.
STELLIG = (
    "moet ", "dus weer open", "hield dit in", "niet compleet", "voor de start", "door btw", "terecht",
    "is afgeletterd", "de rest is", "accepteert",
)  # fmt: skip


def test_teksten_van_de_kaart_noemen_de_namen_uit_de_instellingen_en_beweren_niets(tmp_path):
    """Loopt alle teksten van een overzicht met kaartblok na: de regels van blok B met uitleg, `eraf`, het oordeel per
    factuur en de opmerking per ontvangst. De scenario's raken de takken met veel tekst: een restant, een ontvangst die
    niet in de bank staat, een creditorder van een ander, een creditorder na het afletteren, een deels betaalde factuur,
    meer en minder betaald dan gefactureerd."""
    kaart = [*KAART_B, ("2025-04-14", "ontvangst", "", IBAN_GEWOON, -96.80)]
    meer = kaart_dossier(
        tmp_path / "meer", tarief_a=42.0, bank_a=[("2025-04-14", IBAN_G, 609.84), ("2025-04-14", IBAN_GEWOON, 1422.96)],
        kaart=kaart,
    )  # fmt: skip
    bank_minder = [("2025-04-14", IBAN_G, 551.76), ("2025-04-14", IBAN_GEWOON, 1287.44)]
    minder = kaart_dossier(tmp_path / "minder", tarief_a=38.0, bank_a=bank_minder)
    blokken = [
        kaart_van(meer), kaart_van(minder), kaart_van(kaart_dossier(tmp_path / "basis")),
        scenario_restant(tmp_path / "restant"), scenario_creditorder_van_een_ander(tmp_path / "ander"),
        scenario_creditorder_na_afletteren(tmp_path / "na"), scenario_deels_betaald(tmp_path / "deels"),
    ]  # fmt: skip
    teksten = []
    for k in blokken:
        teksten += list(k.facturen.oordeel) + list(k.ontvangsten.opmerking)
        teksten += [t for regel in k.eraf + k.regels for t in (regel[0], regel[2])]
    # de scenario's raken de takken waar het om gaat
    for fragment in (
        "Lijkt een restant van de bankboeking", "Staat niet in de bankexport",
        "Af: op de betaling staat een creditorder", "Bij: creditorder verrekend", "Deels betaald", "meer dan",
        "minder betaalde", "Open: geen order",
    ):  # fmt: skip
        assert any(fragment in t for t in teksten), fragment
    assert any("Oeverland" in t for t in teksten) and any("Studio Noord" in t for t in teksten)
    for tekst in teksten:
        laag = tekst.lower()
        assert not any(woord in laag for woord in (*VERBODEN, *STELLIG, "inleenorder", "timesheet")), tekst


# ---------- wat het werkboek nodig heeft: kaartaantallen, bank, dagoordeel en uitleg van blok 3 ----------


def test_kaart_heeft_de_aantallen_en_nummers_van_blok_a(tmp_path):
    k = kaart_van(kaart_dossier(tmp_path))
    assert k.aantallen == {"open": 4, "ontvangsten": 2, "buiten_uren": 1}
    assert k.per_medewerker_aantal.to_dict() == {FEMKE: 2, DAAN: 1}
    assert k.buiten_nummers == ["F-900"]
    assert k.volgorde == [
        FEMKE,
        DAAN,
        "",
    ]  # de volgorde waarin ze op de kaart staan; "" zijn de facturen buiten de uren
    assert k.al_afgeletterd == 0.0
    # wat van de open facturen afgaat: de ontvangsten van de medewerker plus wat terugkwam, min wat al was afgeletterd
    assert k.af_ontvangen == pytest.approx(k.ontvangsten.waarvan.sum() + k.bij_credit - k.al_afgeletterd)


def test_kaart_met_een_ontvangst_op_een_afgeletterde_factuur_telt_die_apart(tmp_path):
    k = scenario_creditorder_na_afletteren(tmp_path)
    assert k.al_afgeletterd >= 0.0
    assert k.af_ontvangen == pytest.approx(k.ontvangsten.waarvan.sum() + k.bij_credit - k.al_afgeletterd)


def test_bank_heeft_elke_boeking_met_betreft_en_opmerking(voorbeeld):
    notities = pd.DataFrame(
        [["nabetaling Kim", "Sanne", "Hoort bij Sanne."]], columns=["tekst", "betreft", "opmerking"]
    )
    o = overzicht_van(replace(voorbeeld, bank_notities=notities), SANNE)
    assert len(o.bank) == len(o.aansluiting.bankregels)
    assert {"betreft", "opmerking", "orders", "medewerkers", "aandeel"} <= set(o.bank.columns)
    nabetaling = o.bank[o.bank.omschrijving.str.contains("nabetaling Kim")]
    assert len(nabetaling) and nabetaling.opmerking.eq("Hoort bij Sanne.").all()
    assert set(o.bank_los.omschrijving) <= set(o.bank.omschrijving)
    zonder = overzicht_van(voorbeeld, SANNE)
    assert zonder.bank is not None


def test_zonder_bank_is_bank_none(voorbeeld):
    d = replace(voorbeeld, bank=None)
    assert overzicht_van(d, SANNE).bank is None


def test_dagen_hebben_een_oordeel_in_woorden(overzichten):
    o = overzichten[0]
    assert len(o.per_dag.oordeel) == len(o.per_dag) and o.per_dag.oordeel.ne("").all()
    geen = o.per_dag[o.per_dag.status.isin((aansluiten.GEEN_P, aansluiten.GEEN_D))]
    assert len(geen) and geen.oordeel.str.startswith("Geen order").all()
    klopt = o.per_dag[o.per_dag.status == aansluiten.KLOPT]
    assert len(klopt) and klopt.oordeel.eq("Sluit aan").all()
    assert not any("inleenorder" in t.lower() for t in o.per_dag.oordeel)


def test_toewijzing_uitleg_noemt_de_namen_uit_de_instellingen(voorbeeld):
    inst = replace(voorbeeld.inst, bureau="Studio Noord", opdrachtgever="Oeverland")
    tekst = overzicht.toewijzing_uitleg(inst)
    assert list(tekst) == list(overzicht.TOEWIJZINGEN)
    assert "Oeverland" in tekst[OW] and "Studio Noord" in tekst[CW]
    alles = " ".join(tekst.values()).lower()
    assert not any(woord in alles for woord in ("inleenorder", "timesheet", *VERBODEN, *STELLIG)), alles
    assert all("voorstel" in t.lower() or t.startswith("Eerst") for t in tekst.values())  # het is een voorstel


def test_een_medewerker_zonder_orders_heeft_een_lege_ordertabel_met_kolommen(voorbeeld):
    """Zonder één order voor de medewerker staan alle dagen op 'geen order'; de tabel van de orders is leeg maar heeft
    de kolommen, zodat de schrijvers er gewoon uit kunnen lezen."""
    thijmen_orders = ["I01250324", "I02250348"]
    d = replace(
        voorbeeld,
        kop=voorbeeld.kop[voorbeeld.kop.order.isin(thijmen_orders)],
        regels=voorbeeld.regels[voorbeeld.regels.order.isin(thijmen_orders)],
        vervallen=None,
    )
    o = overzicht_van(d, SANNE)
    assert o.orders.empty and {"incl", "ontvangen", "oordeel", "betaald_ex", "ontvangen G-rekening"} <= set(
        o.orders.columns
    )
    assert o.stand["betaald"] == 0.0 and o.stand["op_orders"] == 0.0


def test_teksten_die_met_een_naam_beginnen_beginnen_met_een_hoofdletter(voorbeeld):
    inst = replace(voorbeeld.inst, bureau="het bureau", opdrachtgever="de opdrachtgever")
    teksten = [*overzicht.uitleg(inst).values(), *overzicht.toewijzing_uitleg(inst).values()]
    assert all(t[0].isupper() for t in teksten), [t for t in teksten if not t[0].isupper()]


# ---------- de soort betaling, de controles en de bedragen die het werkboek in zinnen zet ----------


def test_elke_order_heeft_een_soort_betaling_die_bij_het_oordeel_past(overzichten, voorbeeld):
    for o in overzichten:
        assert set(o.orders.soort) <= {
            overzicht.ORDER_BETAALD, overzicht.ORDER_VERREKEND, overzicht.ORDER_BOEKHOUDING, overzicht.ORDER_DEELS,
            overzicht.ORDER_NIET, overzicht.ORDER_NOG_NIET, overzicht.ORDER_CREDIT_NIET, overzicht.ORDER_GEEN_BANK,
        }  # fmt: skip
        betaald = o.orders[o.orders.soort.isin(overzicht.BETAALD)]
        assert betaald.oordeel.str.startswith(("Betaald", "Verrekend")).all()
    boekhouding = overzichten[0].orders.set_index("order")
    assert boekhouding.soort["I02250362"] == overzicht.ORDER_BOEKHOUDING
    niet = overzicht_van(replace(voorbeeld, kaart=None), SANNE).orders.set_index("order")
    assert niet.soort["I02250362"] == overzicht.ORDER_NIET and niet.oordeel["I02250362"] == "Niet ontvangen"
    zonder_bank = overzicht_van(replace(voorbeeld, bank=None), SANNE).orders
    assert zonder_bank.soort.eq(overzicht.ORDER_GEEN_BANK).all()
    assert set(overzicht.ORDER_KORT) == {
        overzicht.ORDER_DEELS, overzicht.ORDER_NIET, overzicht.ORDER_NOG_NIET, overzicht.ORDER_CREDIT_NIET,
        overzicht.ORDER_GEEN_BANK,
    }  # fmt: skip


def test_soort_nog_niet_vervallen_en_betaald_volgens_de_boekhouding(voorbeeld):
    zonder_kaart = replace(voorbeeld, kaart=None)  # met de kaart van de voorbeeldset zijn de facturen afgeletterd
    basis = overzicht_van(zonder_kaart, SANNE)
    order = "I01250491"  # 37 dagen voor het einde van de bank gefactureerd
    zonder = zonder_betalingen(basis.aansluiting)
    lang = overzicht.maak(replace(zonder, inst=replace(basis.aansluiting.inst, betaaltermijn=45)), zonder_kaart)
    kort = overzicht.maak(replace(zonder, inst=replace(basis.aansluiting.inst, betaaltermijn=30)), zonder_kaart)
    assert lang.orders.set_index("order").soort[order] == overzicht.ORDER_NOG_NIET
    assert kort.orders.set_index("order").soort[order] == overzicht.ORDER_NIET
    kaart = pd.DataFrame(
        [[pd.Timestamp("2025-07-01"), "factuur", "F-ANDERS", "", "", 100.0]],
        columns=["datum", "soort", "nummer", "omschrijving", "rekening", "bedrag"],
    )
    boekhouding = overzicht_van(replace(voorbeeld, kaart=kaart), SANNE).orders.set_index("order")
    assert boekhouding.soort["I02250362"] == overzicht.ORDER_BOEKHOUDING


def test_perioden_noemen_alleen_orders_die_niet_in_orde_zijn(overzichten):
    o = overzichten[0]
    niet = set(o.orders.order[~o.orders.soort.isin(overzicht.BETAALD)])
    for p in o.perioden.itertuples():
        genoemd = {x.split(":")[0] for x in p.betaling.split("; ") if ":" in x}
        assert genoemd <= niet


def test_stappen_per_saldo_en_bank_totalen_komen_uit_het_model(overzichten, voorbeeld):
    o = overzichten[0]
    s = o.stand
    assert o.stappen() == {
        "urenstaat_gefactureerd": round(s["urenstaat"] - s["gefactureerd"], 2),
        "gefactureerd_orders": round(s["gefactureerd"] - s["op_orders"], 2),
        "orders_betaald": round(s["op_orders"] - s["betaald"], 2),
    }
    w = o.per_toewijzing().set_index("toewijzing").bedrag
    assert o.per_saldo() == {
        "opnemen": round(w[OW] - w[aansluiten.OV], 2), "factureren": round(w[CW] - w[aansluiten.CV], 2),
        "uitzoeken": round(w[NOG], 2),
    }  # fmt: skip
    b = o.bank_totalen()
    assert b["incl"] == round(o.orders.incl.sum(), 2) and b["ontvangen"] == round(o.orders.ontvangen.sum(), 2)
    assert b["open"] == round(b["incl"] - b["ontvangen"], 2)
    assert overzicht_van(replace(voorbeeld, bank=None), SANNE).bank_totalen() is None


def test_controles_zeggen_of_het_klopt_en_met_welk_verschil(overzichten, voorbeeld, tmp_path):
    for o in overzichten:
        c = o.controles()
        assert set(c) == {overzicht.C_OORZAKEN, overzicht.C_ZONDER_ORDER, overzicht.C_KAART}
        assert all(x.sluit and x.verschil == 0 for k, x in c.items() if k != overzicht.C_KAART)  # exact nul
        assert c[overzicht.C_KAART].sluit and abs(c[overzicht.C_KAART].verschil) < 0.05  # alleen de kaart heeft ruimte
    for naam in (SANNE, THIJMEN):  # zonder kaart geen kaartcontrole
        c = overzicht_van(replace(voorbeeld, kaart=None), naam).controles()
        assert set(c) == {overzicht.C_OORZAKEN, overzicht.C_ZONDER_ORDER}
    scheef = kaart_dossier(tmp_path, kaart=[("2025-03-24", "factuur", "F-102", "", 1000.00), *KAART_B[1:]])
    c = overzicht_van(scheef, FEMKE).controles()[overzicht.C_KAART]
    assert c.sluit is False and c.verschil == pytest.approx(-548.80)
    goed = overzicht_van(kaart_dossier(tmp_path / "goed"), FEMKE).controles()[overzicht.C_KAART]
    assert goed.sluit is True and abs(goed.verschil) < 0.05


def test_controle_op_de_oorzaken_en_op_de_facturen_ziet_een_verschil(overzichten):
    o = overzichten[0]
    kapot = replace(o, stand={**o.stand, "op_orders": o.stand["op_orders"] + 10.0})
    assert kapot.controles()[overzicht.C_OORZAKEN] == overzicht.Controle(False, 10.0)
    facturen = o.facturen.assign(zonder_order=o.facturen.zonder_order + 1.0)
    c = replace(o, facturen=facturen).controles()[overzicht.C_ZONDER_ORDER]
    assert c.sluit is False and c.verschil == pytest.approx(-len(facturen))


def test_toezegging_bedragen_en_bank_telling(voorbeeld):
    assert overzicht_van(voorbeeld, SANNE).toezegging_bedragen() is None
    t = toezegging(
        ("T-001", "2025 wk 33", "", "Sanne week 33", "Uren", 32.0, 37.31, 1193.92),
        ("T-001", "2025 wk 34", "", "Sanne week 34", "Uren", None, 37.31, None),
    )
    o = overzicht_van(replace(voorbeeld, toezegging=t), SANNE)
    b = o.toezegging_bedragen()
    z = o.zonder_order
    assert b["nummers"] == "T-001" and b["op_toezegging"] == pytest.approx(1193.92)
    assert b["rest"] == pytest.approx(z.zonder_order.sum() - 1193.92)
    assert b["uren_rest"] == pytest.approx(z.bedrag_uren.sum() - 1193.92)
    assert b["onkosten"] == pytest.approx(z.onkosten.sum())
    assert b["rest"] == pytest.approx(b["uren_rest"] + b["onkosten"])
    k = o.bank_telling()
    assert k["totaal"] == len(o.bank) and k["totaal"] == k["bij_order"] + k["zonder_order"]
    assert k["bij_order"] == int(sum(len(x) > 0 for x in o.bank.orders)) and k["getoond"] == len(o.bank_los)
    assert overzicht_van(replace(voorbeeld, bank=None), SANNE).bank_telling() is None


def test_regels_van_blok_b_hebben_vaste_sleutels(tmp_path):
    k = kaart_van(kaart_dossier(tmp_path))
    assert len(k.regel_sleutels) == len(k.regels)
    assert k.regel_sleutels[:2] == [overzicht.R_OPEN, overzicht.R_AF_ONTVANGEN]
    assert k.regel_sleutels[-1] == overzicht.R_BIJ_NIET_GEFACTUREERD
    na = scenario_creditorder_na_afletteren(tmp_path / "na")
    assert overzicht.R_BIJ_CREDIT in na.regel_sleutels
    ander = scenario_creditorder_van_een_ander(tmp_path / "ander")
    assert overzicht.R_AF_VERREKEND in ander.regel_sleutels
    for blok in (k, na, ander):
        assert len(set(blok.regel_sleutels)) == len(blok.regel_sleutels)


# ---------- uren met een bedrag en zonder factuurnummer ----------


def _zonder_nummer(voorbeeld, rijen: pd.DataFrame) -> overzicht.Overzicht:
    """Het overzicht van Sanne waarin deze urenregels geen factuurnummer hebben."""
    uren = voorbeeld.uren.copy()
    uren.loc[rijen.index, "factuurnummer"] = None
    return maak(aansluiting(voorbeeld, SANNE, uren), voorbeeld)


def test_een_urenregel_met_een_bedrag_zonder_factuurnummer_noemt_de_rij(voorbeeld):
    u = voorbeeld.uren
    eigen = u[(u.medewerker == SANNE) & (u.bedrag > 0)]
    with pytest.raises(ValueError) as fout:
        _zonder_nummer(voorbeeld, eigen.iloc[:1])
    tekst = str(fout.value)
    assert tekst.startswith(SANNE) and "1 urenregel met een bedrag maar zonder factuurnummer" in tekst
    assert f"rij {int(eigen.rij.iloc[0])}" in tekst and "zet het bedrag op 0" in tekst


def test_veel_urenregels_zonder_factuurnummer_noemt_er_vijf_en_telt_de_rest(voorbeeld):
    u = voorbeeld.uren
    eigen = u[(u.medewerker == SANNE) & (u.bedrag > 0)]
    with pytest.raises(
        ValueError, match=rf"{len(eigen)} urenregels met een bedrag.*\(rij .*en {len(eigen) - 5} andere\)"
    ):
        _zonder_nummer(voorbeeld, eigen)


def test_een_urenregel_met_bedrag_nul_mag_zonder_factuurnummer(voorbeeld):
    u = voorbeeld.uren
    nul = u[(u.medewerker == SANNE) & (u.bedrag == 0)]
    assert not nul.empty
    o = _zonder_nummer(voorbeeld, nul)
    assert o.facturen.columns.tolist()[0] == "factuur" and len(o.facturen) > 0


def test_facturen_heeft_altijd_zijn_kolommen(voorbeeld):
    leeg = overzicht._facturen(voorbeeld.uren.iloc[0:0].assign(week=""))
    assert leeg.empty and "netto" in leeg.columns and "zonder_order" in leeg.columns


def test_een_andere_schrijfwijze_van_de_status_is_een_eigen_fout(voorbeeld):
    """Met "Approved" in plaats van "Goedgekeurd" wees de melding naar urenstaat-afwijkend, de verkeerde kant."""
    uren = voorbeeld.uren.assign(status="Approved")
    with pytest.raises(ValueError) as fout:
        maak(aansluiting(voorbeeld, SANNE, uren), voorbeeld)
    tekst = str(fout.value)
    assert tekst.startswith(SANNE) and "geen enkele urenregel heeft de status Goedgekeurd" in tekst
    assert "'Approved'" in tekst and "urenstaat-afwijkend" not in tekst


def test_een_medewerker_met_een_deel_goedgekeurde_regels_rekent_gewoon(voorbeeld):
    uren = voorbeeld.uren.copy()
    uren.loc[uren.index[:3], "status"] = "Concept"
    assert maak(aansluiting(voorbeeld, SANNE, uren), voorbeeld).stand["gefactureerd"] > 0
