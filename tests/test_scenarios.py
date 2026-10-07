"""Scenario-tests: verzonnen dossiers uit `scenario.bouw` gaan door `sluit_aan` en `maak` (het rekenmodel) en door de
opdrachtregel (`scenario.controleer`: werkboeken, totaalbestand, bevindingen). Elk scenario krijgt dezelfde vaste
controles plus een eigen verwachting."""

import re
import shutil
from dataclasses import replace

import pandas as pd
import pytest
from conftest import MEDEWERKERS, VOORBEELD
from scenario import AFWIJKINGEN, GEEN_ACTIES, Afwijking, Scenario, bouw, controleer, reken_na

from weekstaat import aansluiten, bevindingen, dossier, inlezen, omzetten, overzicht
from weekstaat.cli import main
from weekstaat.overzicht import (
    O_BLOK,
    O_DAG,
    O_DAGV,
    O_NIETGEF,
    O_UREN,
    WEEKSTATUSSEN,
    Overzicht,
)

SANNE, JEROEN, MIRJAM = "Sanne Bakker", "Jeroen Hendriks", "Mirjam Dekker"
DRIE = (SANNE, JEROEN, MIRJAM)
VOLLEDIG = {"medewerkers": DRIE, "weken": 24}  # genoeg weken voor alle afwijkingen tegelijk


def overzichten(s: Scenario) -> dict[str, Overzicht]:
    """Het overzicht van elke medewerker van het scenario, zoals de opdrachtregel ze maakt."""
    d = s.lees()
    uit = {}
    for naam in s.medewerkers:
        a = aansluiten.sluit_aan(
            d.uren, d.kop, d.regels, naam, d.inst, bankregels=d.bank, vervallen=d.vervallen, saldo=d.saldo(naam)
        )
        uit[naam] = overzicht.maak(a, d)
    return uit


def controleer_model(s: Scenario) -> dict[str, Overzicht]:
    """De vaste controles voor elk scenario, voor elke medewerker. Geeft de overzichten terug.

    De verschillen tellen op tot gefactureerd min op orders; betaald is nooit meer dan op orders; de stand is wat de
    bouwer weet; zonder afwijking zijn er geen verschillen en is alles betaald; is er een kaartblok, dan sluit het."""
    uit = overzichten(s)
    for naam, o in uit.items():
        stand, v = o.stand, o.verschillen
        assert round((v.fact - v.order).sum(), 2) == round(stand["gefactureerd"] - stand["op_orders"], 2), naam
        assert round(o.per_oorzaak().bedrag.sum(), 2) == round(stand["gefactureerd"] - stand["op_orders"], 2), naam
        assert stand["betaald"] <= stand["op_orders"] + 0.01 * len(o.orders), naam
        if len(o.orders):
            assert (o.orders.betaald_ex <= o.orders.waarvan_medewerker + 0.01).all(), naam
        for sleutel, verwacht in s.verwacht[naam].items():
            assert stand[sleutel] == pytest.approx(verwacht, abs=0.011), (naam, sleutel)
        assert o.perioden.fact.sum() == pytest.approx(stand["gefactureerd"], abs=0.005), naam
        assert o.perioden.order.sum() == pytest.approx(stand["op_orders"], abs=0.005), naam
        assert set(o.weken.status) <= set(WEEKSTATUSSEN), naam
        if o.kaart is not None:
            assert sum(b for _, b, _ in o.kaart.regels) == pytest.approx(o.kaart.echt_open, abs=0.005), naam
            assert o.kaart.sluit, (naam, o.kaart.echt_open, o.kaart.controle)
        if not s.afwijkingen and not s.zonder_orders:
            assert v.empty, naam
            if s.bank:
                assert stand["betaald"] == pytest.approx(stand["op_orders"], abs=0.05), naam
                assert o.orders.soort.isin(overzicht.BETAALD).all(), naam
    return uit


def rijen(o: Overzicht, oorzaak: str, a: Afwijking) -> pd.DataFrame:
    """De verschilregels van deze oorzaak in de weken van de afwijking."""
    return o.verschillen[(o.verschillen.oorzaak == oorzaak) & o.verschillen.week.isin(a.weken)]


def periode(o: Overzicht, a: Afwijking) -> pd.Series:
    return o.perioden.set_index("periode").loc[a.periode]


def kaartregels(o: Overzicht, a: Afwijking) -> pd.DataFrame:
    """De facturen van blok C van de kaart waar de afwijking op zit."""
    return o.kaart.facturen[o.kaart.facturen.factuur.isin(a.facturen)]


# ---------- de eigen verwachting per afwijking, ook in `test_volledig` gebruikt ----------


def verwacht_blok_zonder_order(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_BLOK, a)
    assert set(r.week) == set(a.weken)
    assert r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005)
    assert set(r.toewijzing) == {aansluiten.OW}
    assert (a.medewerker, a.periode) not in s.orders
    assert periode(o, a).status == "Geen order"
    if o.kaart:  # de facturen van het blok zijn niet betaald en staan open
        assert kaartregels(o, a).kaart.sum() == pytest.approx(a.incl(s.btw), abs=0.05)
        assert kaartregels(o, a).oordeel.str.startswith("Open: geen order").all()


def verwacht_dag_zonder_order(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_DAG, a)
    assert list(r.week) == list(a.weken) and r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005)
    assert periode(o, a).status == "Verschil"
    if o.kaart:
        assert kaartregels(o, a).kaart.sum() == pytest.approx(a.incl(s.btw), abs=0.05)


def verwacht_order_niet_betaald(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    order = o.orders.set_index("order").loc[a.order]
    assert order.soort == overzicht.ORDER_NIET and order.ontvangen == 0 and order.betaald_ex == 0
    assert order.excl == pytest.approx(a.bedrag, abs=0.005)
    assert a.order in periode(o, a).betaling
    if s.bank:
        assert o.stand["op_orders"] - o.stand["betaald"] >= a.bedrag - 0.05
    if o.kaart:  # de facturen staan open voor de hele order
        assert kaartregels(o, a).kaart.sum() == pytest.approx(s.incl[a.order], abs=0.05)


def verwacht_niet_gefactureerd(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_NIETGEF, a)
    assert r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005) and a.bedrag < 0
    assert set(r.toewijzing) == {aansluiten.CW}
    assert r.fact.sum() == 0 and r.order.sum() == pytest.approx(-a.bedrag, abs=0.005)
    assert o.orders.set_index("order").soort[a.order] == overzicht.ORDER_BETAALD  # de order is gewoon betaald


def verwacht_ander_tarief(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_UREN, a)
    assert list(r.week) == list(a.weken) and r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005)
    assert set(r.toewijzing) == {aansluiten.NOG} and a.bedrag > 0
    if o.kaart:  # de factuur is deels betaald: het deel boven de order staat open
        f = kaartregels(o, a)
        assert f.kaart.sum() == pytest.approx(a.incl(s.btw), abs=0.05)
        assert f.oordeel.str.startswith("Deels betaald").any()


def verwacht_dagvergoeding(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_DAGV, a)
    assert set(r.week) == set(a.weken) and len(r) == len(a.weken)
    assert r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005)
    assert r.order.sum() == 0 and set(r.toewijzing) == {aansluiten.NOG}
    assert "9,50" in " ".join(r.toelichting)  # het vaste bedrag per dag staat in de toelichting
    if o.kaart:
        assert kaartregels(o, a).kaart.sum() == pytest.approx(a.incl(s.btw), abs=0.05)


def verwacht_dubbel_gefactureerd(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_UREN, a)
    assert list(r.week) == list(a.weken) and r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005)
    # de goedgekeurde urenstaat telt de dag één keer: er is meer gefactureerd dan de urenstaat zegt
    assert o.stand["gefactureerd"] - o.stand["urenstaat"] == pytest.approx(a.bedrag, abs=0.005)
    week = o.weken.set_index("week").loc[a.weken[0]]
    assert week.fact - week.urenstaat_bedrag == pytest.approx(a.bedrag, abs=0.005)
    assert "tweede factuur" in week.opmerking


def verwacht_creditorder(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    r = rijen(o, O_UREN, a)
    assert r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005) and a.bedrag > 0
    credit = o.orders.set_index("order").loc[a.order]
    assert s.incl[a.order] < 0 and credit.ontvangen == pytest.approx(s.incl[a.order], abs=0.011)  # het geld is weg
    assert credit.soort == overzicht.ORDER_VERREKEND
    # de order van de volgende medewerker in dat blok is betaald, met de creditorder erop
    betaler = uit[s.medewerkers[s.medewerkers.index(a.medewerker) + 1]]
    nr = s.orders[(betaler.medewerker, a.periode)]
    order = betaler.orders.set_index("order").loc[nr]
    assert order.soort == overzicht.ORDER_BETAALD and a.order in order.oordeel  # het oordeel noemt de creditorder
    assert order.ontvangen == pytest.approx(s.incl[nr], abs=0.011)  # de hele order telt als betaald


def verwacht_vervangen_order(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    v = o.vervallen_orders()
    assert list(v.order) == [a.order] and list(v.vervangen_door) == [a.extra]
    assert v.waarvan_medewerker.iloc[0] == pytest.approx(a.bedrag, abs=0.005)
    assert a.order not in set(o.orders.order) and a.extra in set(o.orders.order)  # telt nergens mee
    assert o.orders.set_index("order").soort[a.extra] == overzicht.ORDER_BETAALD


def verwacht_dubbele_order(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    assert list(o.orders.order).count(a.order) == 1  # de order telt één keer ...
    regels = o.aansluiting.order_regels
    assert o.aansluiting.kop.order.is_unique and regels.bedrag[regels.order == a.order].sum() == pytest.approx(
        o.orders.set_index("order").excl[a.order], abs=0.005
    )  # ook de regels staan er één keer in
    assert o.orders.set_index("order").soort[a.order] == overzicht.ORDER_BETAALD  # ... en is één keer betaald


def verwacht_overlappende_bankexport(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    ruw = pd.read_csv(s.map / "bank.csv", sep=";")
    assert len(ruw) - ruw.referentie.nunique() == a.aantal > 0  # de export heeft echt dubbele boekingen
    for o in uit.values():
        assert len(o.aansluiting.bankregels) == ruw.referentie.nunique()  # elke boeking telt één keer
        betaald = o.orders[o.orders.soort == overzicht.ORDER_BETAALD]
        assert betaald.ontvangen.tolist() == pytest.approx([s.incl[x] for x in betaald.order], abs=0.011)  # één keer
        assert (o.orders.ontvangen <= o.orders.incl + 0.01).all()  # ook niet twee keer


def verwacht_restant_op_kaart(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    teveel = -a.incl(s.btw)
    assert a.bedrag < 0 and o.kaart.sluit
    assert [x for _, x, _ in o.kaart.eraf] == pytest.approx([teveel], abs=0.02)
    assert o.kaart.per_saldo == pytest.approx(o.kaart.echt_open - teveel, abs=0.02)
    # het restant staat als ontvangst op de kaart, bij de grotere bankboeking van die dag
    r = o.kaart.ontvangsten[o.kaart.ontvangsten.orders == a.order]
    assert r.bedrag.tolist() == pytest.approx([teveel], abs=0.02)
    assert r.opmerking.iloc[0].startswith("Lijkt een restant van de bankboeking")
    assert kaartregels(o, a).echt_open.sum() == pytest.approx(-teveel, abs=0.02)


def verwacht_meer_betaald(s: Scenario, uit: dict[str, Overzicht], a: Afwijking) -> None:
    o = uit[a.medewerker]
    teveel = -a.incl(s.btw)
    assert a.bedrag < 0
    f = kaartregels(o, a)
    assert f.echt_open.sum() == pytest.approx(-teveel, abs=0.02)  # betaald is meer dan gefactureerd
    assert f.oordeel.str.contains(r"Oeverland betaalde [\d.,]+ meer dan Studio Noord factureerde").any()
    assert f.kaart.sum() == 0  # bij het afletteren is het verschil weggeboekt: er staat niets op de kaart
    assert all(abs(x - teveel) > 0.005 for _, x, _ in o.kaart.eraf)  # dus ook niets af van per saldo
    r = rijen(o, O_UREN, a)
    assert r.verschil.sum() == pytest.approx(a.bedrag, abs=0.005)


VERWACHT = {
    "blok_zonder_order": verwacht_blok_zonder_order,
    "dag_zonder_order": verwacht_dag_zonder_order,
    "order_niet_betaald": verwacht_order_niet_betaald,
    "niet_gefactureerd": verwacht_niet_gefactureerd,
    "ander_tarief": verwacht_ander_tarief,
    "dagvergoeding_niet_op_order": verwacht_dagvergoeding,
    "dubbel_gefactureerd": verwacht_dubbel_gefactureerd,
    "creditorder_later_verrekend": verwacht_creditorder,
    "vervangen_order": verwacht_vervangen_order,
    "dubbele_order": verwacht_dubbele_order,
    "overlappende_bankexport": verwacht_overlappende_bankexport,
    "restant_op_kaart": verwacht_restant_op_kaart,
    "meer_betaald": verwacht_meer_betaald,
}


def verwachte_bevindingen(s: Scenario) -> dict[tuple[str, str], float]:
    """Per (medewerker, soort bevinding) het bedrag dat de bevindingen samen moeten hebben, uit de bouwer.

    Een afwijking geeft zijn eigen bevinding en soms een gevolg: wat het bureau niet factureerde en de opdrachtgever
    wel betaalde is ook "meer betaald dan gefactureerd", een creditorder maakt een verschil in uren of tarief en laat
    de betaling van een ander afletteren. De bevinding "uren of tarief anders" is er één per medewerker, met het
    netto bedrag van alle afwijkingen daarvan. Een dubbele order (alleen uit twee pdf's) en een overlappende
    bankexport geven geen bevinding."""
    uit: dict[tuple[str, str], float] = {}
    uren: dict[str, float] = {}

    def tel(wie: str, soort: str, bedrag: float) -> None:
        uit[(wie, soort)] = round(uit.get((wie, soort), 0.0) + abs(bedrag), 2)

    for a in s.afwijkingen.values():
        wie = a.medewerker
        match a.naam:
            case "blok_zonder_order":
                tel(wie, bevindingen.S_ZONDER_ORDER, a.bedrag)
            case "dag_zonder_order":
                tel(wie, bevindingen.S_DAGEN, a.bedrag)
            case "order_niet_betaald":
                tel(wie, bevindingen.S_NIET_BETAALD, s.incl[a.order])
            case "niet_gefactureerd":
                tel(wie, bevindingen.S_NIETGEF, a.bedrag)
                tel(wie, bevindingen.S_MEER_BETAALD, a.incl(s.btw))
            case "dagvergoeding_niet_op_order":
                tel(wie, bevindingen.S_DAGV, a.bedrag)
            case "dubbel_gefactureerd":
                uren[wie] = uren.get(wie, 0.0) + a.bedrag
                tel(wie, bevindingen.S_MEER_FACT, a.bedrag)
            case "ander_tarief":
                uren[wie] = uren.get(wie, 0.0) + a.bedrag
            case "creditorder_later_verrekend":
                uren[wie] = uren.get(wie, 0.0) + a.bedrag
                tel(wie, bevindingen.S_CREDIT, s.incl[a.order])
                betaler = s.medewerkers[s.medewerkers.index(wie) + 1]
                tel(betaler, bevindingen.S_AFLETTEREN, s.incl[s.orders[(betaler, a.periode)]])
            case "restant_op_kaart" | "meer_betaald":
                uren[wie] = uren.get(wie, 0.0) + a.bedrag
                tel(wie, bevindingen.S_MEER_BETAALD, a.incl(s.btw))
            case "vervangen_order":
                tel(wie, bevindingen.S_VERVALLEN, a.bedrag)
    for wie, bedrag in uren.items():
        tel(wie, bevindingen.S_UREN, bedrag)
    return uit


def controleer_bevindingen(s: Scenario, gevonden: pd.DataFrame) -> None:
    """De bevindingen in `bevindingen.csv` zijn precies die van de afwijkingen, met de bedragen van de bouwer."""
    verwacht = verwachte_bevindingen(s)
    algemeen = {"bank": s.bank, "debiteurenkaart": s.kaart}  # wat voor iedereen geldt: een bron die ontbreekt
    verwacht_algemeen = {f"bron-ontbreekt|Algemeen|{bron}" for bron, er in algemeen.items() if not er}
    assert set(gevonden.sleutel[gevonden.medewerker == "Algemeen"]) == verwacht_algemeen
    gevonden = gevonden[gevonden.medewerker != "Algemeen"]
    echt = {(wie, soort): round(g.bedrag.sum(), 2) for (wie, soort), g in gevonden.groupby(["medewerker", "soort"])}
    assert set(echt) == set(verwacht), sorted(set(echt) ^ set(verwacht))
    for sleutel, bedrag in verwacht.items():
        assert echt[sleutel] == pytest.approx(bedrag, abs=0.02), sleutel
    for naam, a in s.afwijkingen.items():  # een afwijking met een eigen bevinding geeft er precies één van die soort
        eigen = HOOFDSOORT.get(naam)
        if eigen:
            assert (gevonden[gevonden.medewerker == a.medewerker].soort == eigen).sum() == len(
                [x for x in s.afwijkingen.values() if HOOFDSOORT.get(x.naam) == eigen and x.medewerker == a.medewerker]
            ), naam


# De soort bevinding die één op één bij een afwijking hoort (de andere zijn een gevolg of een optelling).
HOOFDSOORT = {
    "blok_zonder_order": bevindingen.S_ZONDER_ORDER,
    "dag_zonder_order": bevindingen.S_DAGEN,
    "order_niet_betaald": bevindingen.S_NIET_BETAALD,
    "niet_gefactureerd": bevindingen.S_NIETGEF,
    "dagvergoeding_niet_op_order": bevindingen.S_DAGV,
    "dubbel_gefactureerd": bevindingen.S_MEER_FACT,
    "creditorder_later_verrekend": bevindingen.S_CREDIT,
    "vervangen_order": bevindingen.S_VERVALLEN,
}


def scenario_met(
    tmp_path, *afwijkingen: str, opdrachtregel: bool = True, **opties
) -> tuple[Scenario, dict[str, Overzicht]]:
    """Bouwt het scenario met deze afwijkingen, doet de vaste controles en de eigen verwachting van elke afwijking,
    en laat het door de opdrachtregel lopen: de uitvoer heeft de bevindingen die bij de afwijkingen horen."""
    s = bouw(tmp_path / "dossier", afwijkingen=afwijkingen, **opties)
    uit = controleer_model(s)
    for naam in afwijkingen:
        VERWACHT[naam](s, uit, s.afwijkingen[naam])
    if opdrachtregel:
        controleer_bevindingen(s, controleer(s.map, tmp_path / "uit")["bevindingen"])
    return s, uit


# ---------- de bouwer ----------


@pytest.mark.parametrize("sjabloon", ["csv", "xlsx"])
def test_de_bouwer_geeft_steeds_dezelfde_bestanden(tmp_path, sjabloon):
    """Een csv is bytegelijk. Een werkboek bewaart de aanmaaktijd, dus daar gaat het om de celwaarden."""
    a, b = (bouw(tmp_path / n, weken=24, medewerkers=DRIE, afwijkingen=AFWIJKINGEN, sjabloon=sjabloon) for n in "ab")
    namen = sorted(p.name for p in a.map.iterdir())
    assert namen == sorted(p.name for p in b.map.iterdir())
    for naam in namen:
        if naam.endswith(".xlsx"):
            pd.testing.assert_frame_equal(pd.read_excel(a.map / naam), pd.read_excel(b.map / naam))
        else:
            assert (a.map / naam).read_bytes() == (b.map / naam).read_bytes(), naam
    assert a.afwijkingen == b.afwijkingen and set(a.afwijkingen) == set(AFWIJKINGEN)


def test_de_bouwer_weigert_wat_hij_niet_kan(tmp_path):
    with pytest.raises(ValueError, match="orders_als"):
        bouw(tmp_path / "a", orders_als="pdf")
    with pytest.raises(ValueError, match="onbekende afwijking: bestaat_niet"):
        bouw(tmp_path / "b", afwijkingen=("bestaat_niet",))
    with pytest.raises(ValueError, match="minstens twee medewerkers"):
        bouw(tmp_path / "c", medewerkers=(SANNE,), afwijkingen=("creditorder_later_verrekend",))
    with pytest.raises(ValueError, match="vraag meer weken"):
        bouw(tmp_path / "d", weken=3, afwijkingen=("dagvergoeding_niet_op_order", "ander_tarief"))
    with pytest.raises(ValueError, match="nog niet vervallen"):  # bij maanden valt de laatste betaling te vroeg
        bouw(tmp_path / "e", blok="maand", afwijkingen=("order_niet_betaald",))


def _zonder_bestand(df: pd.DataFrame) -> pd.DataFrame:
    """Orders naast elkaar leggen: de bestandsnaam hangt af van de bron (pdf of sjabloon) en de volgorde ook."""
    return df.drop(columns="bestand").sort_values(list(df.columns.drop("bestand"))).reset_index(drop=True)


def _uitkomsten(d: dossier.Dossier, naam: str) -> Overzicht:
    a = aansluiten.sluit_aan(d.uren, d.kop, d.regels, naam, d.inst, bankregels=d.bank, saldo=d.saldo(naam))
    return overzicht.maak(a, d)


def test_orders_uit_pdf_en_sjabloon(tmp_path):
    """De orders van de voorbeeldset uit de pdf's en ook in een sjabloon: één dossier (de pdf gaat voor). Staan een
    paar orders alleen in het sjabloon, dan is het dossier hetzelfde als met alleen pdf's."""
    map_ = tmp_path / "voorbeeld"
    shutil.copytree(VOORBEELD, map_, ignore=shutil.ignore_patterns("ruw", "verwacht"))
    alleen_pdf = dossier.lees(map_)
    dossier.schrijf(map_, kop=alleen_pdf.kop, regels=alleen_pdf.regels)
    assert (map_ / "orders.csv").is_file() and (map_ / "orders").is_dir()

    allebei = dossier.lees(map_)  # elke order staat in beide bronnen: de pdf gaat voor, het sjabloon telt niet mee
    pd.testing.assert_frame_equal(alleen_pdf.kop, allebei.kop)
    pd.testing.assert_frame_equal(alleen_pdf.regels, allebei.regels)
    assert not allebei.kop.bestand.str.contains("orders.csv").any()

    # twee pdf's weg (met drie orders): die orders komen nu alleen uit het sjabloon
    for pdf in ("Order I01250491.pdf", "Orders I01250324 en I02250348.pdf"):
        (map_ / "orders" / pdf).unlink()
    gemengd = dossier.lees(map_)
    uit_sjabloon = gemengd.kop.bestand.str.contains("orders.csv")
    assert set(gemengd.kop.order[uit_sjabloon]) == {"I01250491", "I01250324", "I02250348"}
    assert set(gemengd.kop.order[~uit_sjabloon]) == set(alleen_pdf.kop.order) - set(gemengd.kop.order[uit_sjabloon])
    pd.testing.assert_frame_equal(_zonder_bestand(alleen_pdf.kop), _zonder_bestand(gemengd.kop), check_dtype=False)
    pd.testing.assert_frame_equal(
        _zonder_bestand(alleen_pdf.regels), _zonder_bestand(gemengd.regels), check_dtype=False
    )
    for naam in MEDEWERKERS:  # en dezelfde uitkomst per medewerker
        a, b = (_uitkomsten(d, naam) for d in (alleen_pdf, gemengd))
        assert a.stand == b.stand
        pd.testing.assert_frame_equal(a.verschillen, b.verschillen)
        pd.testing.assert_frame_equal(a.orders, b.orders)


def _orders_sjabloon(pad, regels: list[tuple[str, float]], totaal: float) -> None:
    """Schrijft `orders.csv` met één order uit regels (omschrijving, bedrag) op één dag en het opgegeven totaal."""
    kolommen = inlezen.ORDERS
    rijen = [
        dict(order="OR-9001", entiteit="OI", factuurdatum=pd.Timestamp("2029-01-08"), totaal_excl=totaal,
             totaal_incl=round(totaal * 1.21, 2), medewerker=SANNE, project="Project Noord",
             datum=pd.Timestamp("2029-01-02"), omschrijving=oms, eenheid="Uren", aantal=8.0, tarief=bedrag / 8,
             bedrag=bedrag)
        for oms, bedrag in regels
    ]  # fmt: skip
    omzetten.schrijf_sjabloon(pd.DataFrame(rijen).reindex(columns=kolommen), pad)


def test_een_order_met_twee_identieke_regels_is_geen_dubbele_order(tmp_path):
    """Twee identieke regels die samen het ordertotaal zijn, zijn een gewone order; vier keer dezelfde regel ook."""
    s = bouw(tmp_path / "dossier")
    inst = s.lees().inst
    pad = s.map / "orders.csv"
    _orders_sjabloon(pad, [("Tekenaar", 329.60)] * 2, 659.20)
    kop, regels = inlezen.lees_orders_sjabloon(pad, inst)
    assert list(kop.order) == ["OR-9001"] and len(regels) == 2
    _orders_sjabloon(pad, [("Tekenaar", 329.60)] * 4, 1318.40)
    assert len(inlezen.lees_orders_sjabloon(pad, inst)[1]) == 4


def test_een_order_die_echt_twee_keer_in_het_bestand_staat(tmp_path):
    """Alle regels twee keer en samen twee keer het totaal: de melding zegt wat er aan de hand is."""
    s = bouw(tmp_path / "dossier")
    inst = s.lees().inst
    pad = s.map / "orders.csv"
    _orders_sjabloon(pad, [("Tekenaar", 329.60), ("Reis", 20.0)] * 2, 349.60)
    with pytest.raises(ValueError, match=r"orders\.csv: order OR-9001 staat twee keer in het bestand"):
        inlezen.lees_orders_sjabloon(pad, inst)
    _orders_sjabloon(pad, [("Tekenaar", 329.60)] * 2, 329.60)  # twee identieke regels, totaal is er één keer: dubbel
    with pytest.raises(ValueError, match=r"order OR-9001 staat twee keer in het bestand"):
        inlezen.lees_orders_sjabloon(pad, inst)


def test_een_order_die_niet_optelt_en_niet_dubbel_is_geeft_de_gewone_melding(tmp_path):
    s = bouw(tmp_path / "dossier")
    inst = s.lees().inst
    pad = s.map / "orders.csv"
    _orders_sjabloon(pad, [("Tekenaar", 329.60)] * 2, 500.0)  # twee identieke regels, het totaal klopt niet
    with pytest.raises(ValueError, match=r"order OR-9001: de regels tellen op tot 659,20, het totaal is 500,00"):
        inlezen.lees_orders_sjabloon(pad, inst)
    _orders_sjabloon(pad, [("Tekenaar", 329.60), ("Reis", 20.0)] * 2, 400.0)
    with pytest.raises(ValueError, match="de regels tellen op tot 699,20, het totaal is 400,00"):
        inlezen.lees_orders_sjabloon(pad, inst)


# ---------- scenario's zonder afwijking ----------


def test_alles_sluit_aan(tmp_path):
    s = bouw(tmp_path / "dossier")
    uit = controleer_model(s)
    for o in uit.values():
        assert o.verschillen.empty and o.per_oorzaak().empty
        assert o.stand["urenstaat"] == o.stand["gefactureerd"] == o.stand["op_orders"] == o.stand["betaald"]
        assert set(o.perioden.status) == {"Sluit aan"} and set(o.perioden.betaling) == {"Betaald"}
        assert set(o.weken.status) == {overzicht.W_AANSLUIT} and len(o.weken) == 8
        assert o.kaart.sluit and o.kaart.echt_open == 0 and o.kaart.per_saldo == 0 and o.kaart.eraf == []
        assert len(o.bank_los) == 0 and len(o.orders) == 2
        assert (o.orders.open.abs() < 0.011).all()
    r = controleer(s.map, tmp_path / "uit")  # en door de opdrachtregel: geen bevindingen, een actielijst zonder acties
    assert r["bevindingen"].empty
    assert any(rij[0] == GEEN_ACTIES for rij in r["totaal"]["Actielijst"].iter_rows(values_only=True))


def samenvatting(wb) -> list[list]:
    return [list(r) for r in wb["Samenvatting"].iter_rows(values_only=True) if isinstance(r[0], str)]


def zoek_samenvatting(wb, begin: str) -> list:
    return [r for r in samenvatting(wb) if r[0].startswith(begin)]


def test_geen_bank(tmp_path):
    s = bouw(tmp_path / "dossier", bank=False)
    assert not (s.map / "bank.csv").exists() and (s.map / "debiteurenkaart.csv").exists()
    for o in controleer_model(s).values():
        assert o.stand["betaald"] == 0 and o.stand["op_orders"] > 0
        assert o.kaart is None and o.bank_los is None  # de kaart wordt tegen de bank gelegd
        assert (o.orders.ontvangen == 0).all() and set(o.orders.soort) == {overzicht.ORDER_GEEN_BANK}
        assert set(o.perioden.betaling) == {""}
    r = controleer(s.map, tmp_path / "uit")  # geen blad Bank en Debiteurenkaart: dat controleert `controleer`
    for wb in r["werkboeken"].values():
        assert "Bank" not in wb.sheetnames and "Debiteurenkaart" not in wb.sheetnames
    assert not [x for x in samenvatting(r["totaal"]) if x[0].startswith("Door Oeverland betaald")]
    assert zoek_samenvatting(r["totaal"], "Door Oeverland op orders gezet")
    bron = r["bevindingen"][r["bevindingen"].soort == bevindingen.S_BRON]
    assert list(bron.sleutel) == ["bron-ontbreekt|Algemeen|bank"] and bron.groep.iloc[0] == "Aanleveren"
    assert "geen bankexport" in bron.wat.iloc[0]
    controleer_bevindingen(s, r["bevindingen"])  # en verder niets: ook geen bevinding bij een medewerker


def test_geen_kaart(tmp_path):
    s = bouw(tmp_path / "dossier", kaart=False)
    assert not (s.map / "debiteurenkaart.csv").exists()
    for o in controleer_model(s).values():
        assert o.kaart is None and o.stand["betaald"] == o.stand["op_orders"] > 0
    r = controleer(s.map, tmp_path / "uit")
    for wb in r["werkboeken"].values():
        assert "Debiteurenkaart" not in wb.sheetnames and "Bank" in wb.sheetnames
    assert not zoek_samenvatting(r["totaal"], "Wat staat er echt open") and zoek_samenvatting(
        r["totaal"], "Door Oeverland betaald"
    )
    bron = r["bevindingen"][r["bevindingen"].soort == bevindingen.S_BRON]
    assert list(bron.sleutel) == ["bron-ontbreekt|Algemeen|debiteurenkaart"] and bron.groep.iloc[0] == "Aanleveren"
    controleer_bevindingen(s, r["bevindingen"])


def test_een_lege_kaart_geeft_een_kaartblok_waarin_niets_openstaat(tmp_path):
    """Een kaart waarop alles is afgeletterd is een bestand met alleen een kopregel: een lege kaart."""
    s = bouw(tmp_path / "dossier")
    assert (s.map / "debiteurenkaart.csv").read_text(encoding="utf-8").count("\n") == 1  # alleen de kopregel
    d = s.lees()
    assert d.kaart is not None and d.kaart.empty and list(d.kaart.columns) == inlezen.KAART
    for o in controleer_model(s).values():
        assert o.kaart.heel == {"open": 0.0, "ontvangsten": 0.0, "saldo": 0.0, "buiten_uren": 0.0}
        assert (
            o.kaart.open == o.kaart.echt_open == o.kaart.per_saldo == 0
            and o.kaart.facturen.oordeel.eq("Afgeletterd").all()
        )
        assert o.kaart.ontvangsten.empty and o.kaart.sluit


def test_een_lege_kaart_gaat_door_de_opdrachtregel(tmp_path):
    """De schrijver van het werkboek heeft voor een lege kaart een blad Debiteurenkaart (zichtbaar)."""
    s = bouw(tmp_path / "dossier")
    r = controleer(s.map, tmp_path / "uit")
    assert len(r["werkboeken"]) == 2 and all("Debiteurenkaart" in wb.sheetnames for wb in r["werkboeken"].values())
    assert sorted(p.name for p in (tmp_path / "uit").iterdir()) == [
        "Overzicht Jeroen Hendriks.xlsx", "Overzicht Sanne Bakker.xlsx", "Overzicht totaal.xlsx", "bevindingen.csv"
    ]  # fmt: skip


def test_een_medewerker(tmp_path):
    s = bouw(tmp_path / "dossier", medewerkers=(SANNE,))
    uit = controleer_model(s)
    r = controleer(s.map, tmp_path / "uit")  # het totaalbestand: één kolom plus Totaal, en alleen zijn bladen
    totaal_blad = [list(x) for x in r["totaal"]["Totaal uitgebreid"].iter_rows(values_only=True)]
    kop = next(x for x in totaal_blad if str(x[0]).startswith("1. Van urenstaat"))
    assert kop[1:3] == ["Sanne", "Totaal"]
    assert r["totaal"].sheetnames[-2:] == ["Stand Sanne", "Zonder order Sanne"]
    assert list(uit) == [SANNE] and uit[SANNE].kaart.sluit
    # zonder andere medewerkers is er niemand om een betaling zonder order aan toe te schrijven
    assert uit[SANNE].aansluiting.bankregels.betreft.isin(["ja"]).all()


def test_een_entiteit(tmp_path):
    s = bouw(tmp_path / "dossier", entiteiten=1)
    for o in controleer_model(s).values():
        assert set(o.orders.entiteit) == {"OI"} and (o.orders.open.abs() < 0.011).all()


def test_twee_entiteiten_geven_elke_medewerker_zijn_eigen_entiteit(tmp_path):
    uit = controleer_model(bouw(tmp_path / "dossier", entiteiten=2))
    assert set(uit[SANNE].orders.entiteit) == {"OI"} and set(uit[JEROEN].orders.entiteit) == {"OIAM"}
    bank = uit[JEROEN].aansluiting.bankregels  # de bank heeft per entiteit een eigen tegenpartij
    assert set(bank.entiteit[bank.orders.map(lambda nrs: bool(set(nrs) & set(uit[JEROEN].orders.order)))]) == {"OIAM"}


def test_een_rekening(tmp_path):
    s = bouw(tmp_path / "dossier", rekeningen=1)
    for o in controleer_model(s).values():
        assert [c for c in o.orders.columns if c.startswith("ontvangen ")] == ["ontvangen Gewone rekening"]
        assert (o.orders.open.abs() < 0.011).all() and (o.orders.ontvangen - o.orders.incl).abs().max() < 0.011


@pytest.mark.parametrize("btw", [0.09, 0.0, 0.21])
def test_btw_negen_procent_nul_en_hoog(tmp_path, btw):
    s = bouw(tmp_path / "dossier", btw=btw)
    for o in controleer_model(s).values():
        assert o.inst.btw == btw and (o.orders.open.abs() < 0.011).all() and o.kaart.sluit
        assert (o.orders.incl.round(2) == (o.orders.excl * (1 + btw)).round(2)).all()


def test_btw_negen_procent(tmp_path):
    s, uit = scenario_met(tmp_path, "order_niet_betaald", "ander_tarief", btw=0.09)
    assert uit[SANNE].orders.soort.tolist() == [overzicht.ORDER_NIET, overzicht.ORDER_BETAALD]
    assert uit[JEROEN].kaart.open == pytest.approx(s.afwijkingen["ander_tarief"].incl(0.09), abs=0.005)


def test_btw_nul(tmp_path):
    for o in controleer_model(bouw(tmp_path / "dossier", btw=0.0)).values():
        assert (o.orders.incl == o.orders.excl).all()


# ---------- de blokgrootte ----------


def test_blok_per_week(tmp_path):
    s, uit = scenario_met(tmp_path, "blok_zonder_order", blok="week")
    o = uit[SANNE]
    assert list(o.perioden.periode) == [f"2029 wk {w:02d}" for w in range(1, 9)]
    assert len(s.orders) == 2 * 8 - 1 and len(o.orders) == 7
    # een ontbrekende order raakt één blok
    assert list(o.perioden.periode[o.perioden.status == "Geen order"]) == ["2029 wk 01"]
    assert s.afwijkingen["blok_zonder_order"].bedrag == pytest.approx(1318.40)
    assert not (uit[JEROEN].perioden.status == "Geen order").any()


def test_blok_per_maand(tmp_path):
    s, uit = scenario_met(tmp_path, "blok_zonder_order", blok="maand")
    o = uit[SANNE]
    assert list(o.perioden.periode) == ["2029-01", "2029-02"]
    assert list(o.perioden.periode[o.perioden.status == "Geen order"]) == ["2029-01"]
    assert s.afwijkingen["blok_zonder_order"].periode == "2029-01"
    assert not (uit[JEROEN].perioden.status == "Geen order").any()


def test_blok_per_vier_weken(tmp_path):
    s, uit = scenario_met(tmp_path, "blok_zonder_order", blok="4 weken")
    assert list(uit[SANNE].perioden.periode) == ["2029 wk 01-04", "2029 wk 05-08"]
    assert list(uit[SANNE].perioden.periode[uit[SANNE].perioden.status == "Geen order"]) == ["2029 wk 01-04"]


@pytest.mark.parametrize("blok", ["week", "4 weken", "maand"])
def test_alles_sluit_aan_in_elke_blokgrootte(tmp_path, blok):
    controleer_model(bouw(tmp_path / "dossier", blok=blok, weken=12))


# ---------- de bank ----------


@pytest.mark.parametrize(
    ("termijn", "dagen", "gekoppeld"), [(60, 57, True), (60, 25, False), (30, 35, True), (30, 57, False)]
)
def test_een_verzamelbetaling_zoekt_orders_rond_de_betaaltermijn(tmp_path, termijn, dagen, gekoppeld):
    """Eén betaling voor de orders van twee opeenvolgende weken, `dagen` na de eerste order. De orders liggen van
    betaaltermijn min 10 t/m betaaltermijn plus 10 dagen terug."""
    s = bouw(
        tmp_path / "dossier", medewerkers=(SANNE,), weken=2, blok="week", rekeningen=1, betaaltermijn=termijn,
        afwijkingen=(),
    )  # fmt: skip
    d = s.lees()
    eerste, tweede = (s.orders[(SANNE, f"2029 wk {w:02d}")] for w in (1, 2))
    kop = d.kop.set_index("order")
    boeking = pd.DataFrame(
        {
            "datum": [kop.factuurdatum[eerste] + pd.Timedelta(days=dagen)],
            "rekening": [d.bank.rekening.iloc[0]],
            "bedrag": [s.incl[eerste] + s.incl[tweede]],
            "tegenpartij": [d.bank.tegenpartij.iloc[0]],
            "omschrijving": ["Verzamelbetaling"],
            "referentie": ["REF00001"],
        }
    )
    omzetten.schrijf_sjabloon(boeking, s.map / "bank.csv")
    o = overzichten(s)[SANNE]
    assert list(o.aansluiting.bankregels.orders) == [[eerste, tweede] if gekoppeld else []]
    assert set(o.orders.soort) <= {overzicht.ORDER_BETAALD} if gekoppeld else overzicht.ORDER_BETAALD not in set(
        o.orders.soort
    )  # fmt: skip


def test_een_korte_betaaltermijn_zoekt_geen_orders_van_na_de_betaling(tmp_path):
    """Bij een betaaltermijn van 5 dagen ligt het venster tussen 0 en 15 dagen terug: een order die pas ná de
    betaaldatum is gemaakt hoort er niet bij, ook al is de betaling voor twee orders."""
    s = bouw(tmp_path / "dossier", medewerkers=(SANNE,), weken=2, blok="week", rekeningen=1, betaaltermijn=5)
    d = s.lees()
    eerste, tweede = (s.orders[(SANNE, f"2029 wk {w:02d}")] for w in (1, 2))
    kop = d.kop.set_index("order")
    assert kop.factuurdatum[tweede] - kop.factuurdatum[eerste] == pd.Timedelta(days=7)
    boeking = pd.DataFrame(
        {
            "datum": [kop.factuurdatum[eerste] + pd.Timedelta(days=3)],  # vóór de tweede order
            "rekening": [d.bank.rekening.iloc[0]],
            "bedrag": [s.incl[eerste] + s.incl[tweede]],
            "tegenpartij": [d.bank.tegenpartij.iloc[0]],
            "omschrijving": ["Verzamelbetaling"],
            "referentie": ["REF00001"],
        }
    )
    omzetten.schrijf_sjabloon(boeking, s.map / "bank.csv")
    assert list(overzichten(s)[SANNE].aansluiting.bankregels.orders) == [[eerste]]


@pytest.mark.parametrize("wie", [0, 1, 2])
def test_een_ontvangst_bij_gelijke_boekingen_wordt_op_volgorde_gekoppeld(tmp_path, wie):
    """Beperking van `overzicht._op_bank`: drie even grote boekingen op één dag en maar één ontvangst op de kaart (de
    andere twee zijn afgeletterd). De ontvangst hoort bij de eerste boeking, ook als de open factuur van een ander is.
    Is de open factuur van de eerste medewerker (`wie` is 0), dan sluit elk kaartblok. Anders sluit alleen het
    kaartblok van de medewerker met de open factuur niet. In beide gevallen telt niemand de ontvangst dubbel."""
    s = bouw(
        tmp_path / "dossier", medewerkers=DRIE, tarieven=(40.0,), gelijk_betaald=True, kaart_ongelet=True,
        rekeningen=1, entiteiten=1, weken=4,
    )  # fmt: skip
    kaart = s.lees().kaart
    open_nr = sorted(kaart[kaart.soort == "factuur"].nummer)[wie]  # de factuur van medewerker `wie` is nog open
    nieuw = pd.concat([kaart[kaart.nummer == open_nr], kaart[kaart.soort == "ontvangst"].head(1)])
    omzetten.schrijf_sjabloon(nieuw, s.map / "debiteurenkaart.csv")
    uit = overzichten(s)
    rood = [naam for naam, o in uit.items() if not o.kaart.sluit]
    assert rood == ([] if wie == 0 else [DRIE[wie]])
    ontvangst = float(nieuw.bedrag[nieuw.soort == "ontvangst"].iloc[0])
    assert sum(o.kaart.ontvangsten.waarvan.sum() for o in uit.values()) == pytest.approx(-ontvangst, abs=0.02)


@pytest.mark.parametrize("rekeningen", [1, 2])
@pytest.mark.parametrize("entiteiten", [1, 2])
def test_gelijke_boekingen_op_een_dag_horen_elk_bij_hun_eigen_order(tmp_path, entiteiten, rekeningen):
    """Drie medewerkers met even grote orders worden op dezelfde dag betaald en de boekhouding heeft de betalingen nog
    niet afgeletterd: er staan drie even grote ontvangsten op de kaart. Elke ontvangst is één boeking van de bank en
    elke medewerker krijgt zijn eigen betaling, niet die van een ander en niet drie keer dezelfde."""
    s = bouw(
        tmp_path / "dossier", medewerkers=DRIE, tarieven=(40.0,), gelijk_betaald=True, kaart_ongelet=True,
        entiteiten=entiteiten, rekeningen=rekeningen,
    )  # fmt: skip
    uit = controleer_model(s)
    totaal = 0.0
    for naam, o in uit.items():
        eigen = sum(s.incl[nr] for (wie, _), nr in s.orders.items() if wie == naam)
        k = o.kaart
        assert o.orders.soort.eq(overzicht.ORDER_BETAALD).all()
        assert k.af_ontvangen == pytest.approx(eigen, abs=0.02), naam  # alleen zijn eigen betaling
        assert k.echt_open == pytest.approx(0, abs=0.02) and k.sluit
        assert k.ontvangsten.waarvan.sum() == pytest.approx(eigen, abs=0.02)
        totaal += k.ontvangsten.waarvan.sum()
    assert totaal == pytest.approx(sum(s.incl.values()), abs=0.05)  # samen is elke betaling één keer geteld


# ---------- de bronnen als Excel en een medewerker zonder orders ----------


def test_sjablonen_als_excel(tmp_path):
    opties = {**VOLLEDIG, "afwijkingen": ("blok_zonder_order", "ander_tarief", "restant_op_kaart", "vervangen_order")}
    csv, xlsx = (bouw(tmp_path / n, sjabloon=n, **opties) for n in ("csv", "xlsx"))
    assert not list(xlsx.map.glob("*.csv")) and not list(csv.map.glob("*.xlsx"))
    assert {p.stem for p in csv.map.glob("*.csv")} == {p.stem for p in xlsx.map.glob("*.xlsx")}
    a, b = controleer_model(csv), controleer_model(xlsx)
    for naam in a:
        assert a[naam].stand == b[naam].stand
        for tabel in ("verschillen", "orders", "weken", "perioden", "facturen"):
            pd.testing.assert_frame_equal(getattr(a[naam], tabel), getattr(b[naam], tabel), check_dtype=False)
        ka, kb = a[naam].kaart, b[naam].kaart
        assert (ka.open, ka.echt_open, ka.controle, ka.per_saldo, ka.eraf) == (
            kb.open, kb.echt_open, kb.controle, kb.per_saldo, kb.eraf
        )  # fmt: skip
        pd.testing.assert_frame_equal(ka.ontvangsten, kb.ontvangsten)
        pd.testing.assert_frame_equal(ka.facturen, kb.facturen, check_dtype=False)


def test_medewerker_zonder_orders(tmp_path):
    s = bouw(tmp_path / "dossier", medewerkers=DRIE, zonder_orders=(MIRJAM,))
    uit = controleer_model(s)
    o = uit[MIRJAM]
    assert o.stand["op_orders"] == 0 and o.stand["betaald"] == 0 and o.stand["gefactureerd"] > 0
    assert o.orders.empty and set(o.perioden.status) == {"Geen order"}
    assert set(o.verschillen.oorzaak) == {O_BLOK} and o.verschillen.verschil.sum() == o.stand["gefactureerd"]
    assert set(o.zonder_order.factuur) == set(o.facturen.factuur)  # elke factuur is zonder order
    assert (o.zonder_order.ontvangen == 0).all()
    # de andere twee medewerkers merken er niets van
    for naam in (SANNE, JEROEN):
        assert uit[naam].verschillen.empty and (uit[naam].orders.open.abs() < 0.011).all()
    r = controleer(s.map, tmp_path / "uit")  # het werkboek komt er, en zegt dat alles zonder order is gefactureerd
    assert (tmp_path / "uit" / f"Overzicht {MIRJAM}.xlsx").is_file() and MIRJAM in r["werkboeken"]
    eigen = r["bevindingen"][r["bevindingen"].medewerker == MIRJAM]
    assert set(eigen.soort) == {bevindingen.S_ZONDER_ORDER}
    assert eigen.bedrag.sum() == pytest.approx(o.stand["gefactureerd"], abs=0.005)
    assert not r["bevindingen"][r["bevindingen"].medewerker.isin((SANNE, JEROEN))].shape[0]
    # niemand betaalde iets: alle facturen van de medewerker staan open op de kaart
    assert o.kaart.open == pytest.approx(o.stand["gefactureerd"] * 1.21, abs=0.05) and o.kaart.echt_open == o.kaart.open


def test_medewerker_zonder_orders_heeft_een_ordertabel_met_kolommen(tmp_path):
    uit = overzichten(bouw(tmp_path / "dossier", medewerkers=DRIE, zonder_orders=(MIRJAM,)))
    assert list(uit[MIRJAM].orders.columns) == list(uit[SANNE].orders.columns)


# ---------- elke afwijking apart ----------


def test_blok_zonder_order(tmp_path):
    scenario_met(tmp_path, "blok_zonder_order")


def test_dag_zonder_order(tmp_path):
    s, uit = scenario_met(tmp_path, "dag_zonder_order")
    assert s.afwijkingen["dag_zonder_order"].bedrag == pytest.approx(329.60)


def test_order_niet_betaald(tmp_path):
    s, uit = scenario_met(tmp_path, "order_niet_betaald")
    assert uit[SANNE].verschillen.empty  # niet betaald is geen verschil tussen factuur en order
    onbetaald = uit[SANNE].stand["op_orders"] - uit[SANNE].stand["betaald"]
    assert onbetaald == pytest.approx(s.afwijkingen["order_niet_betaald"].bedrag)


def test_niet_gefactureerd(tmp_path):
    scenario_met(tmp_path, "niet_gefactureerd")


def test_ander_tarief(tmp_path):
    scenario_met(tmp_path, "ander_tarief")


def test_dagvergoeding_niet_op_order(tmp_path):
    scenario_met(tmp_path, "dagvergoeding_niet_op_order")


def test_dubbel_gefactureerd(tmp_path):
    """Het model ziet de dag twee keer gefactureerd."""
    scenario_met(tmp_path, "dubbel_gefactureerd")


def test_dubbel_gefactureerd_kaart(tmp_path):
    """De tweede factuur staat helemaal open, de eerste is afgeletterd: de ontvangst hoort bij de eerste."""
    s = bouw(tmp_path / "dossier", afwijkingen=("dubbel_gefactureerd",))
    a = s.afwijkingen["dubbel_gefactureerd"]
    o = controleer_model(s)[a.medewerker]
    assert o.kaart.sluit and o.kaart.echt_open == pytest.approx(a.incl(s.btw), abs=0.02)
    f = o.kaart.facturen.set_index("factuur")
    tweede = max(a.facturen)  # het hoogste factuurnummer is de tweede factuur
    assert f.ontvangen[tweede] == 0 and f.echt_open[tweede] == pytest.approx(a.incl(s.btw), abs=0.02)


def test_creditorder_later_verrekend(tmp_path):
    s, uit = scenario_met(tmp_path, "creditorder_later_verrekend")
    credit, betaler = uit[SANNE], uit[JEROEN]
    # de creditorder is verrekend na het afletteren: het bedrag staat weer open bij de medewerker van de creditorder
    assert credit.kaart.bij_credit == pytest.approx(19.36) and betaler.kaart.af_verrekend == pytest.approx(19.36)
    assert credit.stand["betaald"] == credit.stand["op_orders"]  # de order telt als betaald


def test_vervangen_order(tmp_path):
    scenario_met(tmp_path, "vervangen_order")


def test_dubbele_order(tmp_path):
    s, uit = scenario_met(tmp_path, "dubbele_order")
    assert uit[JEROEN].stand == controleer_model(bouw(tmp_path / "zonder"))[JEROEN].stand  # alsof er niets dubbel was


def test_dubbele_order_in_een_sjabloon_is_een_foutmelding(tmp_path):
    """Een sjabloon kan een dubbele order niet uitdrukken: de melding zegt dat de order twee keer in het bestand staat,
    ook als de kopie niet direct onder het origineel staat."""
    s = bouw(tmp_path / "dossier")
    regels = (s.map / "orders.csv").read_text(encoding="utf-8").splitlines()
    kopie = [r for r in regels[1:] if r.startswith("OR-0001;")]
    (s.map / "orders.csv").write_text("\n".join([*regels, *kopie]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"orders\.csv: order OR-0001 staat twee keer in het bestand"):
        s.lees()
    # één regel te veel is iets anders: dat blijft een verschil met het totaal
    (s.map / "orders.csv").write_text("\n".join([*regels, kopie[0]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"order OR-0001: de regels tellen op tot"):
        s.lees()


def test_overlappende_bankexport(tmp_path):
    scenario_met(tmp_path, "overlappende_bankexport")


def test_restant_op_kaart(tmp_path):
    s, uit = scenario_met(tmp_path, "restant_op_kaart")
    k = uit[JEROEN].kaart
    assert k.echt_open == 0 and k.per_saldo == pytest.approx(-116.16)  # per saldo is echt open min het restant


def test_meer_betaald(tmp_path):
    scenario_met(tmp_path, "meer_betaald")


def test_een_verbroken_koppeling_tussen_bank_en_betalingen_is_een_fout(tmp_path):
    """`bank.koppel` vult de bankboekingen en de betalingen uit dezelfde lijst. Staat een order op een boeking
    zonder betaling voor die rekening, dan is dat een fout in de tool: een melding in gewone taal, vóór het
    kaartblok gaat rekenen."""
    s = bouw(tmp_path / "dossier", kaart_ongelet=True)
    d = s.lees()
    a = aansluiten.sluit_aan(d.uren, d.kop, d.regels, SANNE, d.inst, bankregels=d.bank)
    assert a.bankregels.orders.map(len).sum() > 0 and overzicht.maak(a, d).kaart.sluit  # zo is het gewoon goed
    eerste = s.orders[(SANNE, s.perioden[0])]
    kapot = replace(a, betaald={nr: v for nr, v in a.betaald.items() if nr != eerste})
    with pytest.raises(
        ValueError,
        match=rf"order {eerste} staat op een bankboeking van \d\d-\d\d-2029 maar heeft geen betaling voor "
        r"(G|Gewone) ?-?rekening; dit is een fout in de koppeling van de bank, meld het",
    ):
        overzicht.maak(kapot, d)
    een_rekening = {nr: {k: x for k, x in v.items() if k != "G-rekening"} for nr, v in a.betaald.items()}
    with pytest.raises(ValueError, match=rf"order {eerste} .* geen betaling voor G-rekening"):
        overzicht.maak(replace(a, betaald=een_rekening), d)


# ---------- foute invoer ----------


def bederf(pad, kolom: str, regel: int, waarde: str) -> None:
    """Zet `waarde` in kolom `kolom` van regel `regel` (de kopregel is regel 1) van een csv-sjabloon."""
    tabel = pd.read_csv(pad, sep=";", dtype=str, keep_default_na=False)
    tabel.loc[regel - 2, kolom] = waarde
    tabel.to_csv(pad, sep=";", index=False)


ONBEKEND = "NL00RABO0000000099"  # een IBAN dat niet in de instellingen staat
NIET_BEKEND = "staat niet in de instellingen"
FOUTE_INVOER = [
    ("uren.csv", "datum", 5, "31-02-2025", r"uren\.csv: regel 5, kolom datum: '31-02-2025' is geen datum"),
    ("uren.csv", "bedrag", 5, "abc", r"uren\.csv: regel 5, kolom bedrag: 'abc' is geen getal"),
    ("uren.csv", "uren", 5, "", r"uren\.csv: regel 5: kolom uren is leeg"),
    ("bank.csv", "rekening", 3, ONBEKEND, rf"bank\.csv: regel 3, kolom rekening: '{ONBEKEND}' {NIET_BEKEND}"),
    ("orders.csv", "entiteit", 4, "XX", rf"orders\.csv: regel 4, kolom entiteit: 'XX' {NIET_BEKEND}"),
    (
        "debiteurenkaart.csv",
        "rekening",
        2,
        ONBEKEND,
        rf"debiteurenkaart\.csv: regel 2, kolom rekening: '{ONBEKEND}' {NIET_BEKEND}",
    ),
]


@pytest.mark.parametrize(("bestand", "kolom", "regel", "waarde", "melding"), FOUTE_INVOER)
def test_foute_invoer(tmp_path, capsys, bestand, kolom, regel, waarde, melding):
    """Een fout in de invoer: exitcode 1 en een melding met het bestand en de regel, geen stacktrace."""
    s = bouw(tmp_path / "dossier", afwijkingen=("restant_op_kaart",))  # met een ontvangst op de kaart
    bederf(s.map / bestand, kolom, regel, waarde)
    assert main(["aansluiten", str(s.map), "--uit", str(tmp_path / "uit")]) == 1
    uit, fout = capsys.readouterr()
    assert re.search(melding, fout), fout
    assert fout.startswith("weekstaat: ") and "Traceback" not in fout + uit and "File " not in fout


def test_een_leeg_urenbestand_is_een_foutmelding(tmp_path, capsys):
    s = bouw(tmp_path / "dossier")
    pad = s.map / "uren.csv"
    pad.write_text(pad.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    assert main(["aansluiten", str(s.map), "--uit", str(tmp_path / "uit")]) == 1
    uit, fout = capsys.readouterr()
    assert "uren.csv: er staan geen urenregels in" in fout and "Traceback" not in fout + uit
    assert not (tmp_path / "uit" / "bevindingen.csv").exists()  # er is niets geschreven


# ---------- doorrekenen met formulas (traag: een paar scenario's) ----------


def test_alles_sluit_aan_rekent_door_zonder_foutcel(tmp_path):
    """Alle werkboeken en het totaalbestand van een dossier waar alles sluit: geen cel met een # en geen "sluit niet
    aan"."""
    s = bouw(tmp_path / "dossier")
    r = controleer(s.map, tmp_path / "uit")
    reken_na(r["uit"], overzichten=r["overzichten"])


def test_volledig_rekent_door_zonder_foutcel(tmp_path):
    """Ook met alle afwijkingen zijn de formules heel: de afwijkingen staan er als bedrag, niet als fout."""
    s = bouw(tmp_path / "dossier", afwijkingen=AFWIJKINGEN, **VOLLEDIG)
    uit = controleer(s.map, tmp_path / "uit")["uit"]
    reken_na(
        uit, "Overzicht totaal.xlsx", f"Overzicht {SANNE}.xlsx"
    )  # het totaal en één werkboek: doorrekenen is traag


# ---------- alles tegelijk ----------


# Deze afwijkingen veranderen factuur min order; de andere veranderen alleen de bank, de kaart of het aantal orders.
MET_VERSCHIL = (
    "blok_zonder_order", "dag_zonder_order", "niet_gefactureerd", "ander_tarief", "dagvergoeding_niet_op_order",
    "dubbel_gefactureerd", "creditorder_later_verrekend", "restant_op_kaart", "meer_betaald",
)  # fmt: skip


# De oorzaak (blok 2 van de Stand) die bij elke afwijking met een verschil hoort.
OORZAAK = {
    "blok_zonder_order": O_BLOK, "dag_zonder_order": O_DAG, "niet_gefactureerd": O_NIETGEF, "ander_tarief": O_UREN,
    "dagvergoeding_niet_op_order": O_DAGV, "dubbel_gefactureerd": O_UREN, "creditorder_later_verrekend": O_UREN,
    "restant_op_kaart": O_UREN, "meer_betaald": O_UREN,
}  # fmt: skip


def controleer_alles_tegelijk(s: Scenario, uit: dict[str, Overzicht]) -> None:
    """Elke afwijking is nog met zijn eigen bedrag terug te vinden en de bedragen lopen niet in elkaar over."""
    assert set(s.afwijkingen) == set(AFWIJKINGEN)
    per_week = {}
    for a in s.afwijkingen.values():
        for w in a.weken:
            assert (a.medewerker, w) not in per_week, (a.naam, per_week[(a.medewerker, w)])
            per_week[(a.medewerker, w)] = a.naam
    for naam, o in uit.items():  # het totale verschil is de som van wat de afwijkingen van deze medewerker doen
        eigen = [a.bedrag for a in s.afwijkingen.values() if a.naam in MET_VERSCHIL and a.medewerker == naam]
        assert o.stand["gefactureerd"] - o.stand["op_orders"] == pytest.approx(sum(eigen), abs=0.05), naam
    assert uit[SANNE].stand["betaald"] < uit[SANNE].stand["op_orders"]  # een order is niet betaald
    for naam, o in uit.items():  # ook per oorzaak (blok 2 van de Stand) is het de som van de afwijkingen
        verwacht: dict[str, float] = {}
        for a in s.afwijkingen.values():
            if a.naam in OORZAAK and a.medewerker == naam:
                verwacht[OORZAAK[a.naam]] = round(verwacht.get(OORZAAK[a.naam], 0) + a.bedrag, 2)
        gevonden = o.per_oorzaak().set_index("oorzaak").bedrag.to_dict()
        assert gevonden == pytest.approx(verwacht, abs=0.005), naam


@pytest.mark.parametrize("blok", ["4 weken", "week", "maand"])
def test_volledig(tmp_path, blok):
    """Alle afwijkingen tegelijk."""
    # de opdrachtregel loopt hier alleen met "4 weken" mee (looptijd); `test_blok_per_week` doet de rest
    s, uit = scenario_met(tmp_path, *AFWIJKINGEN, opdrachtregel=blok == "4 weken", blok=blok, **VOLLEDIG)
    controleer_alles_tegelijk(s, uit)


def test_reken_na_zonder_enige_vergelijking_is_een_fout(tmp_path):
    """Een controle die niets vergelijkt (een bestandsnaam die niet bestaat) bewijst niets en slaagt dus niet."""
    s = bouw(tmp_path / "dossier")
    r = controleer(s.map, tmp_path / "uit")
    with pytest.raises(AssertionError, match="geen enkele rij vergeleken"):
        reken_na(r["uit"], "bestaat-niet.xlsx", overzichten=r["overzichten"])
