"""Tests voor `sluit_aan`: de voorbeeldset geeft de verwachte uitkomst en kleine gevallen laten het gedrag zien."""

from dataclasses import replace

import pandas as pd
import pytest
from conftest import MEDEWERKERS, saldo, verwacht_bank, verwacht_per_dag

from weekstaat import aansluiten, opmaak
from weekstaat.aansluiten import GEEN, sluit_aan


@pytest.fixture(scope="module", params=MEDEWERKERS)
def uitkomst(request, inst, uren, kop, regels, vervallen, bankregels):
    naam = request.param
    return naam, sluit_aan(uren, kop, regels, naam, inst, bankregels, vervallen, saldo(naam))


def test_per_dag_komt_overeen_met_de_voorbeelduitkomst(uitkomst):
    naam, a = uitkomst
    pd.testing.assert_frame_equal(
        a.per_dag, verwacht_per_dag(naam), check_dtype=False, check_exact=False, rtol=0, atol=0.005
    )


def test_bankregels_komen_overeen_met_de_voorbeelduitkomst(uitkomst):
    naam, a = uitkomst
    bank = a.bankregels.assign(orders=a.bankregels.orders.map(", ".join))
    pd.testing.assert_frame_equal(bank, verwacht_bank(naam), check_dtype=False, check_exact=False, rtol=0, atol=0.005)


def test_saldo_telt_op_tot_het_verschil(uitkomst):
    _, a = uitkomst
    verschil = (
        a.per_dag.fact_bedrag_uren + a.per_dag.fact_onkosten - a.per_dag.order_bedrag_uren - a.per_dag.order_onkosten
    ).sum()
    assert (a.saldo.fact_bedrag - a.saldo.order_bedrag).sum() == pytest.approx(verschil, abs=0.01)


def test_samenvatting_geeft_per_status_de_verschillen(uitkomst):
    _, a = uitkomst
    s = a.samenvatting()
    assert list(s.columns) == ["dagen", "factuurkant", "orderkant", "verschil"]
    assert s.dagen.sum() == len(a.per_dag)
    assert s.verschil.sum() == pytest.approx((s.factuurkant - s.orderkant).sum(), abs=0.01)


# ---------- kleine gevallen ----------

DAG = pd.Timestamp("2025-03-03")


def _uren(*rijen):
    """Rijen (datum, uren, bedrag) van één medewerker; een regel zonder uren is een onkostenregel."""
    return pd.DataFrame(
        [
            dict(rij=i + 2, medewerker="Sanne Bakker", urenstaat="TS-1", datum=d, uren=u, soort="Normale Uren",
                 tarief=50.0, status="Approved", factuurnummer="F1", factuurdatum=pd.Timestamp("2025-03-31"), bedrag=b,
                 creditnummer="", creditbedrag=0.0, factuurbedrag=b, netto=b)
            for i, (d, u, b) in enumerate(rijen)
        ]
    )  # fmt: skip


def _orders(*orders):
    """Orders (nummer, [(datum, eenheid, aantal, bedrag)]); de kop krijgt het totaal van de regels."""
    kop, regels = [], []
    for nr, rijen in orders:
        bestand = f"Order {nr}.pdf"
        totaal = sum(r[3] for r in rijen)
        kop.append(dict(bestand=bestand, order=nr, entiteit="OI", factuurdatum=pd.Timestamp("2025-03-31"),
                        excl=totaal, incl=round(totaal * 1.21, 2), referentie=""))  # fmt: skip
        for d, eenheid, aantal, bedrag in rijen:
            regels.append(dict(bestand=bestand, order=nr, entiteit="OI", medewerker="Sanne Bakker", project="P",
                               datum=d, omschrijving="Uitvoerder", eenheid=eenheid, aantal=aantal,
                               tarief=bedrag / aantal, bedrag=bedrag))  # fmt: skip
    return pd.DataFrame(kop), pd.DataFrame(regels)


def test_bij_twee_orders_op_een_dag_gaat_een_regel_naar_de_order_met_hetzelfde_bedrag(inst):
    uren = _uren((DAG, 8.0, 400.0), (DAG, 0.0, 20.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]), ("B1", [(DAG, "Kilometers", 100.0, 20.0)]))
    a = sluit_aan(uren, kop, regels, "Sanne Bakker", inst)
    dag = a.per_dag.set_index("order")
    assert dag.fact_bedrag_uren.to_dict() == {"A1": 400.0, "B1": 0.0}
    assert dag.fact_onkosten.to_dict() == {"A1": 0.0, "B1": 20.0}
    assert set(dag.status) == {"Klopt"}


def test_een_vervallen_order_telt_niet_mee(inst):
    uren = _uren((DAG, 8.0, 400.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]), ("A2", [(DAG, "Uren", 8.0, 400.0)]))
    vervallen = pd.DataFrame([dict(order="A1", vervangen_door="A2", toelichting="Vervangen.")])
    zonder = sluit_aan(uren, kop, regels, "Sanne Bakker", inst, vervallen=vervallen)
    assert list(zonder.per_dag.order) == ["A2"]
    assert zonder.per_dag.order_bedrag_uren.tolist() == [400.0]
    assert not zonder.order_regels.set_index("order").telt_mee["A1"].any()
    met = sluit_aan(uren, kop, regels, "Sanne Bakker", inst)
    assert len(met.per_dag) == 2  # zonder de lijst telt de order dubbel


def test_een_dag_zonder_order_krijgt_de_status_van_zijn_periode(inst):
    dagen = [DAG, DAG + pd.Timedelta(days=1), DAG + pd.Timedelta(days=7)]
    uren = _uren(*[(d, 8.0, 400.0) for d in dagen])
    kop, regels = _orders(("A1", [(dagen[0], "Uren", 8.0, 400.0)]))
    dag = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag
    assert dag.order.tolist() == ["A1", GEEN, GEEN]
    assert dag.status.tolist()[1] == "Geen order - dag ontbreekt in periode"
    assert dag.status.tolist()[2] == "Geen order - dag ontbreekt in periode"  # week 10 hoort bij het blok van week 9-12


def test_zonder_orders_ontbreekt_alles(inst):
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]))
    uren = _uren((DAG, 8.0, 400.0)).assign(medewerker="Jeroen Hendriks")
    a = sluit_aan(uren, kop, regels, "Jeroen Hendriks", inst)
    assert a.per_dag.status.tolist() == ["Geen order - hele periode ontbreekt"]


def test_een_correctieregel_met_een_tarief_ver_onder_het_uurtarief_telt_geen_uren_mee(inst):
    uren = _uren((DAG, 8.0, 401.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0), (DAG, "Uren", 1.0, 1.0)]))
    regels.loc[1, "tarief"] = 1.0
    dag = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag
    assert dag.order_uren.tolist() == [8.0]
    assert dag.order_bedrag_uren.tolist() == [401.0]


def test_orders_die_niet_optellen_geven_een_duidelijke_fout(inst):
    uren = _uren((DAG, 8.0, 400.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]))
    with pytest.raises(ValueError, match="tellen niet op tot het ordertotaal.*A1"):
        sluit_aan(uren, kop.assign(excl=500.0), regels, "Sanne Bakker", inst)


def test_een_urenregel_zonder_datum_geeft_een_duidelijke_fout(inst):
    uren = _uren((DAG, 8.0, 400.0), (pd.NaT, 8.0, 400.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]))
    with pytest.raises(ValueError, match="urenregels sluiten niet aan"):
        sluit_aan(uren, kop, regels, "Sanne Bakker", inst)


def test_sluit_aan_laat_de_invoer_ongemoeid(inst, uren, kop, regels):
    voor = [uren.copy(), kop.copy(), regels.copy()]
    sluit_aan(uren, kop, regels, MEDEWERKERS[0], inst)
    for oud, nieuw in zip(voor, (uren, kop, regels), strict=True):
        pd.testing.assert_frame_equal(oud, nieuw)


# ---------- de afspraken uit de instellingen ----------


def test_de_aansluiting_bewaart_de_instellingen(uitkomst, inst):
    _, a = uitkomst
    assert a.inst is inst
    assert a.rekeningen == inst.rekeningen


def test_blok_per_week_geeft_periode_per_week(uren, kop, regels, inst):
    dag = sluit_aan(uren, kop, regels, "Sanne Bakker", replace(inst, blok="week")).per_dag
    assert (dag.periode == dag.week).all()
    assert dag.periode.iloc[0] == "2025 wk 28"
    # week 43 heeft geen enkele order: bij een blok van een week ontbreekt dan de hele periode, niet één dag
    week35 = dag[dag.week == "2025 wk 43"]
    assert set(week35.status) == {"Geen order - hele periode ontbreekt"}
    vier = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag
    assert set(vier[vier.week == "2025 wk 43"].status) == {"Geen order - dag ontbreekt in periode"}


def test_blok_per_maand(uren, kop, regels, inst):
    dag = sluit_aan(uren, kop, regels, "Sanne Bakker", replace(inst, blok="maand")).per_dag
    assert (dag.periode == dag.datum.dt.strftime("%Y-%m")).all()
    assert dag.periode.iloc[0] == "2025-07"
    # september heeft in de voorbeeldset geen order, oktober wel (week 41 en 42)
    assert set(dag[dag.periode == "2025-09"].status) == {"Geen order - hele periode ontbreekt"}
    assert set(dag[dag.week == "2025 wk 43"].status) == {"Geen order - dag ontbreekt in periode"}


def test_week_53_valt_in_een_blok_van_vier_weken_dat_op_53_begint(inst):
    dag = pd.Timestamp("2015-12-31")  # donderdag in ISO-week 53
    uren = _uren((dag, 8.0, 400.0))
    kop, regels = _orders(("A1", [(dag, "Uren", 8.0, 400.0)]))
    d = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag
    assert (d.week.iloc[0], d.periode.iloc[0]) == ("2015 wk 53", "2015 wk 53-56")


def test_onkosten_van_dezelfde_isoweek_horen_bij_de_order_ook_over_de_jaargrens(inst):
    """Donderdag 1 januari 2015 en maandag 29 december 2014 zijn dezelfde ISO-week (2015 wk 01)."""
    ma, do = pd.Timestamp("2014-12-29"), pd.Timestamp("2015-01-01")
    uren = _uren((do, 0.0, 12.5))
    kop, regels = _orders(("A1", [(ma, "Kilometers", 50.0, 12.5)]))
    d = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag
    assert d.order.tolist() == ["A1", "A1"]
    assert set(d.week) == {"2015 wk 01"}


def test_nl_bedrag_blijft_beschikbaar_onder_de_oude_naam():
    assert aansluiten.nl_bedrag is opmaak.nl_bedrag
    assert aansluiten.nl_bedrag(1234.5) == "1.234,50"


# ---------- namen op een order ----------


def _twee_jannen(naam_op_order: str):
    """Twee medewerkers met dezelfde voornaam en hetzelfde laatste woord, elk één dag; één order op de naam `naam`."""
    uren = pd.concat(
        [
            _uren((DAG, 8.0, 400.0)).assign(medewerker="Jan de Vries"),
            _uren((DAG, 4.0, 200.0)).assign(medewerker="Jan van Vries"),
        ],
        ignore_index=True,
    )
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]))
    return uren, kop, regels.assign(medewerker=naam_op_order)


@pytest.mark.parametrize("naam", ["Jan de Vries", "Jan van Vries"])
def test_een_naam_op_een_order_die_letterlijk_in_de_uren_staat_gaat_voor(inst, naam):
    """ "Jan de Vries" en "Jan van Vries" hebben dezelfde `naamsleutel`; de order is van wie er letterlijk op staat."""
    uren, kop, regels = _twee_jannen(naam)
    assert aansluiten.naamsleutel("Jan de Vries") == aansluiten.naamsleutel("Jan van Vries")
    andere = "Jan van Vries" if naam == "Jan de Vries" else "Jan de Vries"
    eigen = sluit_aan(uren, kop, regels, naam, inst)
    assert eigen.per_dag.order.tolist() == ["A1"] and eigen.per_dag.order_bedrag_uren.tolist() == [400.0]
    ander = sluit_aan(uren, kop, regels, andere, inst)
    assert ander.per_dag.order.tolist() == [GEEN]  # de order van de een komt niet stil bij de ander


def test_een_naam_op_een_order_die_bij_twee_medewerkers_past_is_een_fout(inst):
    uren, kop, regels = _twee_jannen("Jan Vries")
    with pytest.raises(ValueError) as fout:
        sluit_aan(uren, kop, regels, "Jan de Vries", inst)
    tekst = str(fout.value)
    assert "Jan Vries" in tekst and "Jan de Vries" in tekst and "Jan van Vries" in tekst and "A1" in tekst
    assert "voluit gelijk" in tekst


def test_een_afgeleide_naam_zonder_twijfel_blijft_koppelen(inst):
    """Het gewone geval: "Anna de Vries" op de order, "Anna Maria De Vries" in de uren."""
    uren = _uren((DAG, 8.0, 400.0)).assign(medewerker="Anna Maria De Vries")
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]))
    a = sluit_aan(uren, kop, regels.assign(medewerker="Anna de Vries"), "Anna Maria De Vries", inst)
    assert a.per_dag.order.tolist() == ["A1"]


# ---------- verzamelorder, kilometers zonder bedrag en "Niet Facturabel" ----------


def test_een_verzamelorder_met_alleen_onkosten_telt_als_een_regel_op_de_datum_van_de_factuur(inst):
    """Kilometers over drie dagen die het bureau als één bedrag factureerde: één regel in plaats van drie."""
    dagen = [DAG, DAG + pd.Timedelta(days=1), DAG + pd.Timedelta(days=2)]
    uren = _uren((dagen[2] + pd.Timedelta(days=5), 0.0, 60.0))  # de onkosten staan op de datum van de factuur
    kop, regels = _orders(("V1", [(d, "Kilometers", 40.0, 20.0) for d in dagen]))
    dag = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag
    assert len(dag) == 1 and dag.order.tolist() == ["V1"]
    r = dag.iloc[0]
    assert r.datum == dagen[2] + pd.Timedelta(days=5) and (r.fact_onkosten, r.order_onkosten) == (60.0, 60.0)
    assert r.verzamel == "verzamelorder onkosten, 3 regels van 03-03-2025 t/m 05-03-2025"
    assert r.status == "Klopt" and "verzamelorder onkosten, 3 regels" in r.toelichting
    # staat er geen urenregel met precies dat bedrag, dan valt de order gewoon per dag uit elkaar
    anders = sluit_aan(_uren((DAG, 0.0, 59.0)), kop, regels, "Sanne Bakker", inst).per_dag
    assert (anders.order == "V1").sum() == 3 and (anders.verzamel == "").all()


def test_kilometers_zonder_bedrag_op_de_order_geven_een_eigen_status(inst):
    uren = _uren((DAG, 8.0, 400.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0), (DAG, "Kilometers", 50.0, 0.0)]))
    r = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag.iloc[0]
    assert r.status == aansluiten.KM == "Bedragen kloppen, km zonder bedrag op order"
    assert "50 km vermeld zonder bedrag" in r.toelichting and "ook geen onkosten gefactureerd" in r.toelichting


def test_kilometers_zonder_bedrag_die_later_met_bedrag_op_een_andere_order_staan(inst):
    uren = _uren((DAG, 8.0, 400.0), (DAG, 0.0, 11.5))
    kop, regels = _orders(
        ("A1", [(DAG, "Uren", 8.0, 400.0), (DAG, "Kilometers", 50.0, 0.0)]), ("B1", [(DAG, "Kilometers", 50.0, 11.5)])
    )
    dag = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag.set_index("order")
    assert "50 km vermeld zonder bedrag, later met bedrag op B1" in dag.toelichting["A1"]
    assert dag.status["A1"] == "Klopt" and dag.status["B1"] == "Klopt"  # het bedrag staat elders: geen eigen status


def test_een_orderregel_niet_facturabel_telt_gewoon_mee_en_staat_in_de_toelichting(inst):
    uren = _uren((DAG, 8.0, 400.0))
    kop, regels = _orders(("A1", [(DAG, "Uren", 8.0, 400.0)]))
    regels["omschrijving"] = "Uitvoerder Niet Facturabel"
    r = sluit_aan(uren, kop, regels, "Sanne Bakker", inst).per_dag.iloc[0]
    assert r.order_bedrag_uren == 400.0 and r.status == "Klopt"
    assert "op de order als 'Niet Facturabel' benoemd, wel met bedrag" in r.toelichting


def test_een_zin_met_een_hoofdletter_staat_op_een_plek(inst):
    from weekstaat import bevindingen, overzicht

    assert opmaak.zin("het bureau factureerde") == "Het bureau factureerde" and opmaak.zin("") == ""
    assert overzicht.zin is opmaak.zin is bevindingen.zin
