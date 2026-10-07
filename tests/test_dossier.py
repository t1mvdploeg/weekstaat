"""Tests voor het dossier: alle bronnen uit sjablonen lezen, ook als Excel, en de nieuwe instellingen."""

import dataclasses
import logging
import shutil

import pandas as pd
import pytest
from conftest import MEDEWERKERS, SJABLONEN, VOORBEELD, schrijf_kaart, schrijf_rabobank

from weekstaat import aansluiten, dossier, inlezen, instellingen, omzetten
from weekstaat.cli import main

KOP_ORDERS = (SJABLONEN / "orders.csv").read_text(encoding="utf-8").splitlines()[0] + "\n"
IBAN_G, IBAN_GEWOON = "NL00RABO0991000001", "NL00RABO0123456789"


def bankrij(datum, bedrag, bref="", tref="", iban=IBAN_G, naam="Oeverland infra", oms="week 21-24"):
    return {
        "IBAN/BBAN": iban,
        "Datum": datum,
        "Bedrag": bedrag,
        "Naam tegenpartij": naam,
        "Omschrijving - 1": oms,
        "Boekingsreferentie": bref,
        "Transactiereferentie": tref,
    }


def kleine_map(tmp_path):
    """Een dossier van de voorbeeldset met alleen instellingen en uren; de orders legt de test erbij."""
    map_ = tmp_path / "dossier"
    map_.mkdir()
    shutil.copy(VOORBEELD / "instellingen.toml", map_)
    shutil.copy(VOORBEELD / "uren.csv", map_)
    return map_


# --- Excel ---------------------------------------------------------------------------------------------------------


def test_sjabloon_als_excel(tmp_path):
    """Een sjabloon als .xlsx, waarin datums en bedragen echte Excel-waarden zijn, leest hetzelfde als de csv."""
    uren = inlezen.lees_uren(VOORBEELD / "uren.csv")
    ruw = pd.read_csv(VOORBEELD / "uren.csv", sep=";", decimal=",", dtype=inlezen.TEKST)
    for k in ("datum", "factuurdatum"):
        ruw[k] = pd.to_datetime(ruw[k], format="%d-%m-%Y")
    ruw.to_excel(tmp_path / "uren.xlsx", index=False)
    pd.testing.assert_frame_equal(inlezen.lees_uren(tmp_path / "uren.xlsx"), uren, check_dtype=False)


def test_excel_met_datums_en_bedragen_als_tekst(tmp_path):
    """Een gebruiker die in Excel een datum of bedrag als tekst typt (15-03-2024, 12,50), krijgt hetzelfde resultaat."""
    pd.DataFrame(
        {
            "datum": ["15-03-2024", "16-03-2024"],
            "rekening": [IBAN_G] * 2,
            "bedrag": ["12,50", "7,25"],
            "tegenpartij": ["", ""],
            "omschrijving": ["", ""],
        }
    ).to_excel(tmp_path / "bank.xlsx", index=False)
    bank = inlezen.lees_bank(tmp_path / "bank.xlsx")
    assert list(bank.datum) == [pd.Timestamp("2024-03-15"), pd.Timestamp("2024-03-16")]
    assert list(bank.bedrag) == [12.5, 7.25]


def test_excel_met_een_fout_noemt_bestand_regel_en_kolom(tmp_path):
    ruw = {"datum": ["niet"], "rekening": [IBAN_G], "bedrag": [1.0], "tegenpartij": [""], "omschrijving": [""]}
    pd.DataFrame(ruw).to_excel(tmp_path / "bank.xlsx", index=False)
    with pytest.raises(ValueError, match=r"bank\.xlsx: regel 2, kolom datum: 'niet' is geen datum"):
        inlezen.lees_bank(tmp_path / "bank.xlsx")


def test_een_kapot_excelbestand_noemt_het_bestand(tmp_path):
    (tmp_path / "bank.xlsx").write_bytes(b"dit is geen excel")
    with pytest.raises(ValueError, match=r"bank\.xlsx: geen leesbaar Excel-bestand"):
        inlezen.lees_bank(tmp_path / "bank.xlsx")


def test_vind_kiest_csv_voor_xlsx(tmp_path):
    assert inlezen.vind(tmp_path, "bank") is None
    (tmp_path / "bank.xlsx").write_bytes(b"")
    assert inlezen.vind(tmp_path, "bank") == tmp_path / "bank.xlsx"
    (tmp_path / "bank.csv").write_text("")
    assert inlezen.vind(tmp_path, "bank") == tmp_path / "bank.csv"


# --- orders uit een sjabloon ---------------------------------------------------------------------------------------


def test_orders_sjabloon_telt_niet_op(tmp_path, inst):
    regel = "I01250001;OI;11-03-2024;week 9-12;100,00;121,00;Sanne Bakker;Project A;"
    regel += "12-03-2024;Modelleur;Uren;2;45,00;90,00\n"
    (tmp_path / "orders.csv").write_text(KOP_ORDERS + regel, encoding="utf-8")
    with pytest.raises(ValueError, match="I01250001.*90,00.*100,00"):
        inlezen.lees_orders_sjabloon(tmp_path / "orders.csv", inst)


def eerste_per_order(kop, regels):
    """De voorbeeldset heeft één order twee keer (als pdf en als bijlage van een mail). Een sjabloon heeft elke order
    één keer, met de regels van de eerste."""
    eerste = kop.drop_duplicates("order")
    return eerste.reset_index(drop=True), regels[regels.bestand.isin(eerste.bestand)].reset_index(drop=True)


def test_orders_sjabloon_gelijk_aan_pdf(inst, kop, regels, tmp_path):
    """De orders van de voorbeeldset, weggeschreven als sjabloon en teruggelezen, zijn dezelfde orders."""
    kop, regels = eerste_per_order(kop, regels)
    dossier.schrijf(tmp_path, kop=kop, regels=regels)
    k, r = inlezen.lees_orders_sjabloon(tmp_path / "orders.csv", inst)
    assert list(k.columns) == list(kop.columns) and list(r.columns) == list(regels.columns)
    pd.testing.assert_frame_equal(k.drop(columns="bestand"), kop.drop(columns="bestand"), check_dtype=False)
    pd.testing.assert_frame_equal(r.drop(columns="bestand"), regels.drop(columns="bestand"), check_dtype=False)
    assert set(k.bestand) == {f"orders.csv [{nr}]" for nr in kop.order}
    assert set(r.bestand) == set(k.bestand)


def test_orders_sjabloon_als_excel(inst, kop, regels, tmp_path):
    dossier.schrijf(tmp_path, kop=kop, regels=regels)
    ruw = pd.read_csv(tmp_path / "orders.csv", sep=";", decimal=",", dtype=str)
    for k in ("factuurdatum", "datum"):
        ruw[k] = pd.to_datetime(ruw[k], format="%d-%m-%Y")
    for k in ("totaal_excl", "totaal_incl", "aantal", "tarief", "bedrag"):
        ruw[k] = pd.to_numeric(ruw[k].str.replace(",", "."))
    ruw.to_excel(tmp_path / "orders.xlsx", index=False)
    csv = inlezen.lees_orders_sjabloon(tmp_path / "orders.csv", inst)
    xlsx = inlezen.lees_orders_sjabloon(tmp_path / "orders.xlsx", inst)
    for uit_csv, uit_xlsx in zip(csv, xlsx, strict=True):
        pd.testing.assert_frame_equal(
            uit_xlsx.drop(columns="bestand"), uit_csv.drop(columns="bestand"), check_dtype=False
        )


def test_orders_sjabloon_met_onbekende_entiteit(tmp_path, inst):
    regel = "I01250001;XX;11-03-2024;;90,00;108,90;Sanne Bakker;;12-03-2024;Modelleur;Uren;2;45,00;90,00\n"
    (tmp_path / "orders.csv").write_text(KOP_ORDERS + regel, encoding="utf-8")
    with pytest.raises(ValueError, match=r"orders\.csv: regel 2, kolom entiteit: 'XX'.*OI"):
        inlezen.lees_orders_sjabloon(tmp_path / "orders.csv", inst)


def test_orders_sjabloon_met_ongelijk_totaal_op_de_regels(tmp_path, inst):
    regels = [
        "I01250001;OI;11-03-2024;;90,00;108,90;Sanne Bakker;;12-03-2024;Modelleur;Uren;1;45,00;45,00\n",
        "I01250001;OI;11-03-2024;;95,00;108,90;Sanne Bakker;;13-03-2024;Modelleur;Uren;1;45,00;45,00\n",
    ]
    (tmp_path / "orders.csv").write_text(KOP_ORDERS + "".join(regels), encoding="utf-8")
    with pytest.raises(ValueError, match=r"order I01250001: totaal_excl is niet op elke regel gelijk"):
        inlezen.lees_orders_sjabloon(tmp_path / "orders.csv", inst)


def test_orders_sjabloon_met_kolom_die_ontbreekt(tmp_path, inst):
    (tmp_path / "orders.csv").write_text("order;entiteit\nI01250001;OI\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"orders\.csv: kolom factuurdatum.*ontbreekt"):
        inlezen.lees_orders_sjabloon(tmp_path / "orders.csv", inst)


# --- bank ----------------------------------------------------------------------------------------------------------


def test_bank_referentie_telt_een_keer(tmp_path):
    kop_bank = "datum;rekening;bedrag;tegenpartij;omschrijving;referentie\n"
    rijen = [
        f"27-06-2025;{IBAN_G};108,34;Oeverland infra;week 17-20;REF-A",
        f"27-06-2025;{IBAN_G};108,34;Oeverland infra;week 17-20;REF-A",  # dezelfde boeking uit een tweede export
        f"27-06-2025;{IBAN_G};50,00;Oeverland infra;zonder referentie;",
        f"27-06-2025;{IBAN_G};50,00;Oeverland infra;zonder referentie;",  # zonder referentie telt elke regel
        f"28-06-2025;{IBAN_G};9,99;Oeverland infra;week 21;REF-B",
    ]
    (tmp_path / "bank.csv").write_text(kop_bank + "\n".join(rijen) + "\n", encoding="utf-8")
    bank = inlezen.lees_bank(tmp_path / "bank.csv")
    assert list(bank.referentie) == ["REF-A", "", "", "REF-B"]
    assert list(bank.bedrag) == [108.34, 50.0, 50.0, 9.99]


def test_bank_zonder_referentiekolom_blijft_zoals_het_was(tmp_path):
    (tmp_path / "bank.csv").write_text(f"datum;rekening;bedrag;tegenpartij;omschrijving\n27-06-2025;{IBAN_G};1,00;;\n")
    assert list(inlezen.lees_bank(tmp_path / "bank.csv").columns) == inlezen.BANK


def test_overlappende_exporten_tellen_een_keer(tmp_path, inst):
    """Een volledige export en een zoekresultaat die er deels op overlapt: elke boeking komt één keer voor."""
    een = schrijf_rabobank(
        tmp_path / "een.csv",
        [
            bankrij("27-06-2025", "100,00", bref="B1"),
            bankrij("28-06-2025", "200,00", tref="T2"),  # alleen een transactiereferentie
            bankrij("29-06-2025", "300,00"),  # zonder enige referentie
        ],
    )
    twee = schrijf_rabobank(
        tmp_path / "twee.csv",
        [
            bankrij("27-06-2025", "100,00", bref="B1"),
            bankrij("28-06-2025", "200,00", tref="T2"),
            bankrij("30-06-2025", "400,00", bref="B4"),
        ],
    )
    bank = omzetten.bank_uit_rabobank([een, twee], inst)
    assert list(bank.bedrag) == [100.0, 200.0, 300.0, 400.0]
    assert list(bank.referentie) == ["B1", "T2", "", "B4"]
    # twee keer dezelfde export: elke boeking met referentie telt één keer, de regel zonder referentie telt dubbel
    dubbel = omzetten.bank_uit_rabobank([een, een], inst)
    assert list(dubbel.bedrag) == [100.0, 200.0, 300.0, 300.0]


def test_twee_keer_dezelfde_export_telt_een_keer(tmp_path, inst, caplog):
    export = schrijf_rabobank(
        tmp_path / "g.csv", [bankrij("19-03-2024", "100,00", bref="B1"), bankrij("20-03-2024", "50,00", bref="B2")]
    )
    caplog.set_level(logging.INFO, logger="weekstaat")
    bank = omzetten.bank_uit_rabobank([export, export], inst)
    assert list(bank.bedrag) == [100.0, 50.0]
    assert not caplog.records  # een echte dubbele boeking is geen reden voor een melding


def test_zelfde_referentie_met_ander_bedrag_blijft_staan_en_geeft_een_melding(tmp_path, inst, caplog):
    een = schrijf_rabobank(tmp_path / "een.csv", [bankrij("19-03-2024", "100,00", bref="REF-X")])
    twee = schrijf_rabobank(tmp_path / "twee.csv", [bankrij("19-03-2024", "75,00", bref="REF-X")])
    caplog.set_level(logging.INFO, logger="weekstaat")
    bank = omzetten.bank_uit_rabobank([een, twee], inst)
    assert list(bank.bedrag) == [100.0, 75.0]
    assert [r.getMessage() for r in caplog.records if "REF-X" in r.getMessage()] == [
        "referentie REF-X staat bij meer dan één boeking; alle blijven staan"
    ]
    # een andere datum met hetzelfde bedrag is ook een andere ontvangst
    drie = schrijf_rabobank(tmp_path / "drie.csv", [bankrij("20-03-2024", "100,00", bref="REF-X")])
    assert len(omzetten.bank_uit_rabobank([een, drie], inst)) == 2


def test_zelfde_referentie_op_twee_rekeningen_blijft_staan(tmp_path, inst):
    g = schrijf_rabobank(tmp_path / "g.csv", [bankrij("19-03-2024", "100,00", bref="REF-X")])
    gewoon = schrijf_rabobank(
        tmp_path / "gewoon.csv", [bankrij("19-03-2024", "100,00", bref="REF-X", iban=IBAN_GEWOON)]
    )
    bank = omzetten.bank_uit_rabobank([g, gewoon], inst)
    assert list(bank.rekening) == [IBAN_G, IBAN_GEWOON]


def test_bank_sjabloon_ontdubbelt_op_alle_vier_en_meldt_de_rest(tmp_path, caplog):
    kop_bank = "datum;rekening;bedrag;tegenpartij;omschrijving;referentie\n"
    rijen = [
        f"19-03-2024;{IBAN_G};100,00;;;REF-X",
        f"19-03-2024;{IBAN_G};100,00;;;REF-X",  # echt dubbel
        f"19-03-2024;{IBAN_G};75,00;;;REF-X",  # ander bedrag
        f"19-03-2024;{IBAN_GEWOON};100,00;;;REF-X",  # andere rekening
    ]
    (tmp_path / "bank.csv").write_text(kop_bank + "\n".join(rijen) + "\n")
    caplog.set_level(logging.INFO, logger="weekstaat")
    bank = inlezen.lees_bank(tmp_path / "bank.csv")
    assert list(bank.bedrag) == [100.0, 75.0, 100.0]
    assert sum("REF-X" in r.getMessage() for r in caplog.records) == 1


def test_omzetten_bank_meldt_een_gedeelde_referentie_op_de_opdrachtregel(tmp_path, capsys):
    een = schrijf_rabobank(tmp_path / "een.csv", [bankrij("19-03-2024", "100,00", bref="REF-X")])
    twee = schrijf_rabobank(tmp_path / "twee.csv", [bankrij("19-03-2024", "75,00", bref="REF-X")])
    opdracht = ["omzetten-bank", str(tmp_path / "bank.csv"), str(een), str(twee)]
    assert main(opdracht + ["--instellingen", str(VOORBEELD / "instellingen.toml")]) == 0
    assert "let op: referentie REF-X staat bij meer dan één boeking" in capsys.readouterr().err


def test_overlappende_exporten_van_twee_rekeningen(tmp_path, inst):
    g = schrijf_rabobank(tmp_path / "g.csv", [bankrij("27-06-2025", "100,00", bref="B1")])
    gewoon = schrijf_rabobank(tmp_path / "gewoon.csv", [bankrij("27-06-2025", "233,33", bref="B2", iban=IBAN_GEWOON)])
    g_zoek = schrijf_rabobank(tmp_path / "g_zoek.csv", [bankrij("27-06-2025", "100,00", bref="B1")])
    bank = omzetten.bank_uit_rabobank([g, gewoon, g_zoek], inst)
    assert list(bank.rekening) == [IBAN_G, IBAN_GEWOON]
    assert list(bank.bedrag) == [100.0, 233.33]


def test_export_zonder_referentiekolommen_en_export_zonder_regels(tmp_path, inst):
    """Een export zonder de kolommen met referenties geeft lege referenties; een export zonder regels geeft niets."""
    koppen = list(pd.read_csv(VOORBEELD / "ruw" / "bank-g-rekening.csv", nrows=0, encoding="utf-8-sig").columns[:20])
    rij = [""] * 20
    rij[0], rij[4], rij[6], rij[9], rij[19] = IBAN_G, "27-06-2025", "10,00", "Oeverland infra", "week 21"
    pad = tmp_path / "kort.csv"
    pad.write_text(",".join(f'"{k}"' for k in koppen) + "\n" + ",".join(f'"{v}"' for v in rij) + "\n")
    bank = omzetten.bank_uit_rabobank([pad], inst)
    assert list(bank.referentie) == [""] and list(bank.bedrag) == [10.0]
    leeg = tmp_path / "leeg.csv"
    leeg.write_text(",".join(f'"{k}"' for k in koppen) + "\n")
    assert omzetten.bank_uit_rabobank([leeg], inst).empty


def test_kaart_restant_komt_niet_dubbel(tmp_path, inst):
    """De kaart toont 40,00 van een betaling van 100,00 op dezelfde dag: dat restant staat al in de bank."""
    export = schrijf_rabobank(tmp_path / "g.csv", [bankrij("19-03-2024", "100,00", bref="B1")])
    kaart = schrijf_kaart(
        tmp_path / "kaart.xlsx",
        [
            (pd.Timestamp("2024-03-19"), "2 - NL00 RABO 0991 0000 01", "restant van de betaling", -40.0),
            (pd.Timestamp("2024-03-20"), "2 - NL00 RABO 0991 0000 01", "betaling zonder bankregel", -25.0),
            (pd.Timestamp("2024-03-21"), "V - Verkoopboek", "2025-0001", 500.0),
        ],
    )
    bank = omzetten.bank_uit_rabobank([export], inst, kaart)
    assert list(bank.bedrag) == [100.0, 25.0]
    assert omzetten.UIT_KAART in bank.omschrijving.iloc[1]


# --- instellingen --------------------------------------------------------------------------------------------------


def test_blok_week_maand_vier_weken(inst):
    datums = [pd.Timestamp("2014-12-29"), pd.Timestamp("2015-01-01"), pd.Timestamp("2015-12-28")]  # de laatste is wk 53
    assert [inst.periode(d) for d in datums] == ["2015 wk 01-04", "2015 wk 01-04", "2015 wk 53-56"]
    assert [inst.week(d) for d in datums] == ["2015 wk 01", "2015 wk 01", "2015 wk 53"]
    per_week = dataclasses.replace(inst, blok="week")
    assert [per_week.periode(d) for d in datums] == ["2015 wk 01", "2015 wk 01", "2015 wk 53"]
    per_maand = dataclasses.replace(inst, blok="maand")
    assert [per_maand.periode(d) for d in datums] == ["2014-12", "2015-01", "2015-12"]
    assert [per_maand.week(d) for d in datums] == ["2015 wk 01", "2015 wk 01", "2015 wk 53"]


def test_vier_weken_geeft_dezelfde_tekst_als_de_aansluiting(inst, uren, kop, regels):
    """`week` en `periode` geven voor blok '4 weken' precies wat `sluit_aan` nu zelf berekent."""
    per_dag = aansluiten.sluit_aan(uren, kop, regels, MEDEWERKERS[0], inst).per_dag
    assert inst.blok == "4 weken" and len(per_dag) > 50
    for r in per_dag.itertuples():
        assert (inst.week(r.datum), inst.periode(r.datum)) == (r.week, r.periode)


def test_instellingen_hebben_standaardwaarden(inst):
    assert (inst.bureau, inst.opdrachtgever, inst.btw, inst.blok, inst.betaaltermijn) == (
        "het bureau",
        "de opdrachtgever",
        0.21,
        "4 weken",
        30,
    )


def test_instellingen_uit_toml(tmp_path):
    toml = (VOORBEELD / "instellingen.toml").read_text()
    kop = 'bureau = "Bureau Voorbeeld"\nopdrachtgever = "Oeverland"\nbtw = 0.09\nblok = "maand"\nbetaaltermijn = 45\n\n'
    (tmp_path / "instellingen.toml").write_text(kop + toml)
    inst = instellingen.lees(tmp_path / "instellingen.toml")
    assert (inst.bureau, inst.opdrachtgever, inst.btw, inst.blok, inst.betaaltermijn) == (
        "Bureau Voorbeeld",
        "Oeverland",
        0.09,
        "maand",
        45,
    )


def test_onbekend_blok_is_een_fout(tmp_path):
    (tmp_path / "instellingen.toml").write_text('blok = "kwartaal"\n' + (VOORBEELD / "instellingen.toml").read_text())
    with pytest.raises(ValueError, match=r"instellingen\.toml: blok is 'week', '4 weken' of 'maand', niet 'kwartaal'"):
        instellingen.lees(tmp_path / "instellingen.toml")


@pytest.mark.parametrize(
    ("regel", "verwacht"),
    [
        ('btw = "21%"', "btw is een getal"),
        ("btw = 21", "btw is een getal"),
        ("betaaltermijn = -1", "betaaltermijn is een aantal dagen"),
        ('betaaltermijn = "30 dagen"', "betaaltermijn is een aantal dagen"),
        ("bureau = 3", "bureau is een tekst"),
    ],
)
def test_fouten_in_de_nieuwe_instellingen(tmp_path, regel, verwacht):
    (tmp_path / "instellingen.toml").write_text(regel + "\n" + (VOORBEELD / "instellingen.toml").read_text())
    with pytest.raises(ValueError, match=verwacht):
        instellingen.lees(tmp_path / "instellingen.toml")


def _toml(tmp_path, tekst: str):
    (tmp_path / "instellingen.toml").write_text(tekst, encoding="utf-8")
    return tmp_path / "instellingen.toml"


def test_een_iban_met_spaties_en_kleine_letters_is_gelijk_aan_dat_van_de_bank(tmp_path):
    toml = (VOORBEELD / "instellingen.toml").read_text().replace("NL00RABO0123456789", "nl00 rabo 0123 4567 89")
    inst = instellingen.lees(_toml(tmp_path, toml))
    assert [r.iban for r in inst.rekeningen] == ["NL00RABO0991000001", "NL00RABO0123456789"]
    assert inst.rekening("NL00RABO0123456789").naam == "Gewone rekening"


@pytest.mark.parametrize(
    ("oud", "nieuw", "verwacht"),
    [
        ("btw = 0.21", "btw = 0.21\nbetaal_termijn = 30", r"onbekende instelling betaal_termijn.*betaaltermijn"),
        ('naam = "Oeverland Infra"', 'naam = ""', "geen van beide is leeg"),
        ('naam = "Oeverland Infra Asset Management"', 'naam = "oeverland INFRA"', "de naam oeverland infra staat meer"),
        ("NL00RABO0123456789", "NL00RABO0991000001", "de iban NL00RABO0991000001 staat meer dan één keer"),
        ("aandeel = 0.70", "aandeel = 1.5\n", "aandelen.*niet op tot 1"),
    ],
)
def test_fouten_in_entiteiten_en_rekeningen_en_onbekende_sleutels(tmp_path, oud, nieuw, verwacht):
    basis = (VOORBEELD / "instellingen.toml").read_text()
    toml = ("btw = 0.21\n" if "btw" in oud else "") + basis
    assert oud in toml, oud
    with pytest.raises(ValueError, match=verwacht):
        instellingen.lees(_toml(tmp_path, toml.replace(oud, nieuw, 1)))


def test_een_entiteit_onder_twee_namen_is_goed(tmp_path):
    """De bank schrijft de naam soms afgekort: dezelfde code onder twee namen is één entiteit."""
    toml = (VOORBEELD / "instellingen.toml").read_text()
    toml += '\n[[entiteit]]\ncode = "OIAM"\nnaam = "Oeverland infra asset manag."\n'
    inst = instellingen.lees(_toml(tmp_path, toml))
    assert inst.entiteit("Oeverland infra asset manag. B.V.") == "OIAM"
    assert [e.code for e in inst.entiteiten] == ["OI", "OIAM", "OIAM"]


def test_een_negatief_aandeel_is_een_fout(tmp_path):
    toml = (VOORBEELD / "instellingen.toml").read_text().replace("aandeel = 0.30", "aandeel = -0.30")
    toml = toml.replace("aandeel = 0.70", "aandeel = 1.30")
    with pytest.raises(ValueError, match="aandeel van een rekening is niet negatief"):
        instellingen.lees(_toml(tmp_path, toml))


# --- to-do ---------------------------------------------------------------------------------------------------------


def test_todo_wordt_gelezen(tmp_path):
    (tmp_path / "to-do.md").write_text(
        "# Acties\n\n"
        "## Naar de opdrachtgever\n\n"
        "- **Order vragen** voor week 20 tot en met 23\n"
        "  want er staat niets op `I01250001`.\n"
        "- **Betaling navragen**\n\n"
        "## Zelf doen\n"
        "- **Afletteren** de *betaalde* factuur\n",
        encoding="utf-8",
    )
    assert inlezen.lees_todo(tmp_path / "to-do.md") == [
        (
            "Naar de opdrachtgever",
            [
                ("Order vragen", "voor week 20 tot en met 23 want er staat niets op I01250001."),
                ("Betaling navragen", ""),
            ],
        ),
        ("Zelf doen", [("Afletteren", "de betaalde factuur")]),
    ]


def test_todo_slaat_alle_tekst_voor_de_eerste_groep_over(tmp_path):
    """Een titel, een inleiding en lege regels vóór de eerste `## ` horen bij geen actie en tellen niet."""
    (tmp_path / "to-do.md").write_text(
        "# Acties\n\nDit is een inleiding over het dossier.\nOok een tweede regel.\n\n## Groep\n\n- **Eerste** zo\n",
        encoding="utf-8",
    )
    assert inlezen.lees_todo(tmp_path / "to-do.md") == [("Groep", [("Eerste", "zo")])]


def test_todo_met_alleen_een_inleiding_is_leeg(tmp_path):
    (tmp_path / "to-do.md").write_text("# Acties\n\nNog niets te doen.\n", encoding="utf-8")
    assert inlezen.lees_todo(tmp_path / "to-do.md") == []


def test_todo_zonder_titel_is_een_fout(tmp_path):
    (tmp_path / "to-do.md").write_text("## Groep\n- **Goed** zo\n- Een actie zonder titel\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"to-do\.md: regel 3.*Een actie zonder titel"):
        inlezen.lees_todo(tmp_path / "to-do.md")


def test_todo_regel_met_streepje_voor_de_eerste_groep_is_inleiding(tmp_path):
    tekst = "- **Los** zonder groep\n- een punt\n\n## Groep\n- **Echt** zo\n"
    (tmp_path / "to-do.md").write_text(tekst, encoding="utf-8")
    assert inlezen.lees_todo(tmp_path / "to-do.md") == [("Groep", [("Echt", "zo")])]


def test_todo_tekst_zonder_actie_is_een_fout(tmp_path):
    (tmp_path / "to-do.md").write_text("## Groep\n\nLosse tekst\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"to-do\.md: regel 3.*Losse tekst"):
        inlezen.lees_todo(tmp_path / "to-do.md")


# --- kaart en de overige hulpbestanden ------------------------------------------------------------------------------


def test_kaart_wordt_gelezen(tmp_path):
    (tmp_path / "debiteurenkaart.csv").write_text(
        "datum;soort;nummer;omschrijving;rekening;bedrag\n"
        "16-03-2024;factuur;2024-0001;;;1714,25\n"
        f"19-03-2024;ontvangst;;Betaling week 9;{IBAN_G[:4]} {IBAN_G[4:]};-805,10\n"
        "20-03-2024;ontvangst;;Zonder rekening;;-1,00\n",
        encoding="utf-8",
    )
    kaart = inlezen.lees_kaart(tmp_path / "debiteurenkaart.csv")
    assert list(kaart.columns) == inlezen.KAART
    assert list(kaart.soort) == ["factuur", "ontvangst", "ontvangst"]
    assert list(kaart.bedrag) == [1714.25, -805.1, -1.0]
    assert list(kaart.rekening) == ["", IBAN_G, ""]  # spaties in het IBAN vallen weg
    assert list(kaart.nummer) == ["2024-0001", "", ""]


def test_kaart_met_onbekende_soort_is_een_fout(tmp_path):
    (tmp_path / "debiteurenkaart.csv").write_text(
        "datum;soort;nummer;omschrijving;rekening;bedrag\n16-03-2024;betaling;;;;1,00\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"debiteurenkaart\.csv: regel 2, kolom soort: 'betaling'.*factuur.*ontvangst"):
        inlezen.lees_kaart(tmp_path / "debiteurenkaart.csv")


def test_opmerkingen_en_andere_hulpbestanden_worden_gelezen(tmp_path):
    (tmp_path / "opmerkingen.csv").write_text(
        "medewerker;nr;belangrijk;onderwerp;bedrag;btw;wat;bewijs;actie;wie;bevinding\n"
        "Sanne Bakker;1;ja;Uren zonder order;1234,50;ex;Wat er speelt;Mail van 11-03-2024;Navragen;Bureau;sleutel\n"
        ";2;nee;Algemeen;;;Een algemene opmerking;;;;\n",
        encoding="utf-8",
    )
    opm = inlezen.lees_opmerkingen(tmp_path / "opmerkingen.csv")
    assert list(opm.columns) == inlezen.OPMERKINGEN
    assert list(opm.belangrijk) == ["ja", "nee"] and opm.bedrag[0] == 1234.5 and pd.isna(opm.bedrag[1])
    assert list(opm.nr) == ["1", "2"] and opm.medewerker[1] == "" and opm.bevinding[0] == "sleutel"

    (tmp_path / "zonder-order.csv").write_text(
        "medewerker;week;stuk;toelichting\nSanne Bakker;2024 wk 08;Mail van 11-03-2024;Gevraagd\n", encoding="utf-8"
    )
    assert list(inlezen.lees_zonder_order(tmp_path / "zonder-order.csv").columns) == inlezen.ZONDER_ORDER

    (tmp_path / "toezegging.csv").write_text(
        "medewerker;nr;datum;week;maand;omschrijving;eenheid;aantal;prijs;bedrag\n"
        "Sanne Bakker;T-001;09-04-2024;2024 wk 14;;Modelleur;Uren;32;45,00;1440,00\n"
        "Sanne Bakker;T-001;09-04-2024;;2024-03;Reiskosten;Kilometers;150;0,19;28,50\n",
        encoding="utf-8",
    )
    toezegging = inlezen.lees_toezegging(tmp_path / "toezegging.csv")
    assert list(toezegging.columns) == inlezen.TOEZEGGING
    assert toezegging.datum[0] == pd.Timestamp("2024-04-09") and list(toezegging.bedrag) == [1440.0, 28.5]

    (tmp_path / "urenstaat-afwijkend.csv").write_text(
        "medewerker;week;bedrag;toelichting\nSanne Bakker;2024 wk 06;1234,56;Goedgekeurde versie\n", encoding="utf-8"
    )
    assert inlezen.lees_urenstaat_afwijkend(tmp_path / "urenstaat-afwijkend.csv").bedrag[0] == 1234.56

    (tmp_path / "bank-notities.csv").write_text(
        "tekst;betreft;opmerking\nnavraag;Sanne;Hoort bij een andere medewerker\n", encoding="utf-8"
    )
    assert list(inlezen.lees_bank_notities(tmp_path / "bank-notities.csv").columns) == inlezen.BANK_NOTITIES


def test_toezegging_uit_excel_leest_hetzelfde_als_de_csv(tmp_path):
    """Excel maakt van 2024-03 een datum en van 4711 een getal; beide komen terug zoals in de csv."""
    csv = tmp_path / "toezegging.csv"
    csv.write_text(
        "medewerker;nr;datum;week;maand;omschrijving;eenheid;aantal;prijs;bedrag\n"
        "Sanne Bakker;4711;09-04-2024;2024 wk 14;;Modelleur;Uren;24;37,31;895,44\n"
        "Sanne Bakker;4711;09-04-2024;;2024-03;Reiskosten;Kilometers;150;0,19;28,50\n",
        encoding="utf-8",
    )
    xlsx = tmp_path / "excel" / "toezegging.xlsx"
    xlsx.parent.mkdir()
    ruw = pd.read_csv(csv, sep=";", decimal=",", dtype=str)
    ruw["nr"] = pd.to_numeric(ruw.nr)
    ruw["datum"] = pd.to_datetime(ruw.datum, format="%d-%m-%Y")
    ruw["maand"] = pd.to_datetime(ruw.maand, format="%Y-%m")
    for k in ("aantal", "prijs", "bedrag"):
        ruw[k] = pd.to_numeric(ruw[k].str.replace(",", "."))
    ruw.to_excel(xlsx, index=False)
    uit_csv, uit_xlsx = inlezen.lees_toezegging(csv), inlezen.lees_toezegging(xlsx)
    assert list(uit_xlsx.maand) == ["", "2024-03"] and list(uit_xlsx.nr) == ["4711", "4711"]
    pd.testing.assert_frame_equal(uit_xlsx, uit_csv, check_dtype=False)


def test_toezegging_met_een_maand_die_geen_maand_is(tmp_path):
    (tmp_path / "toezegging.csv").write_text(
        "medewerker;nr;datum;week;maand;omschrijving;eenheid;aantal;prijs;bedrag\n"
        "Sanne Bakker;4711;09-04-2024;;maart 2024;Reiskosten;Kilometers;150;0,19;28,50\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"toezegging\.csv: regel 2, kolom maand: 'maart 2024' is geen maand"):
        inlezen.lees_toezegging(tmp_path / "toezegging.csv")


@pytest.mark.parametrize("week", ["week 14", "2024-14", "2024 wk 4", "14"])
def test_toezegging_met_een_week_die_geen_week_is(tmp_path, week):
    (tmp_path / "toezegging.csv").write_text(
        "medewerker;nr;datum;week;maand;omschrijving;eenheid;aantal;prijs;bedrag\n"
        f"Sanne Bakker;4711;09-04-2024;2024 wk 13;;Uren;Uren;8;40;320\n"
        f"Sanne Bakker;4711;09-04-2024;{week};;Uren;Uren;8;40;320\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=rf"toezegging\.csv: regel 3, kolom week: '{week}' is geen week"):
        inlezen.lees_toezegging(tmp_path / "toezegging.csv")
    assert (tmp_path / "toezegging.csv").read_text().count("2024 wk 13") == 1  # regel 2 was goed


def test_nummers_uit_excel_komen_als_tekst_terug_zonder_punt_nul(tmp_path, inst):
    """Een ordernummer, factuurnummer, opmerkingnummer of bankreferentie die alleen uit cijfers bestaat is in Excel
    een getal; de lezers geven er tekst van zoals '2001'."""
    orders = pd.DataFrame(
        {k: [None] for k in inlezen.ORDERS}
        | {
            "order": [2001.0],
            "entiteit": ["OI"],
            "factuurdatum": [pd.Timestamp("2024-03-22")],
            "totaal_excl": [0.0],
            "totaal_incl": [0.0],
        }
    )
    orders.to_excel(tmp_path / "orders.xlsx", index=False)
    assert list(inlezen.lees_orders_sjabloon(tmp_path / "orders.xlsx", inst)[0].order) == ["2001"]

    kaart = pd.DataFrame(
        {
            "datum": [pd.Timestamp("2024-03-19")],
            "soort": ["factuur"],
            "nummer": [20240001.0],
            "omschrijving": [""],
            "rekening": [""],
            "bedrag": [1.0],
        }
    )
    kaart.to_excel(tmp_path / "kaart.xlsx", index=False)
    assert list(inlezen.lees_kaart(tmp_path / "kaart.xlsx").nummer) == ["20240001"]

    opmerkingen = pd.DataFrame(
        {k: ["x"] for k in inlezen.OPMERKINGEN[:-1]} | {"nr": [7.0], "bedrag": [1.5], "belangrijk": ["ja"]}
    )
    opmerkingen.to_excel(tmp_path / "opmerkingen.xlsx", index=False)
    assert list(inlezen.lees_opmerkingen(tmp_path / "opmerkingen.xlsx").nr) == ["7"]

    bank = pd.DataFrame(
        {
            "datum": [pd.Timestamp("2024-03-19")],
            "rekening": [IBAN_G],
            "bedrag": [1.0],
            "tegenpartij": [""],
            "omschrijving": [""],
            "referentie": [123456.0],
        }
    )
    bank.to_excel(tmp_path / "bank.xlsx", index=False)
    assert list(inlezen.lees_bank(tmp_path / "bank.xlsx").referentie) == ["123456"]


def test_opmerking_met_onbekend_belangrijk_is_een_fout(tmp_path):
    (tmp_path / "opmerkingen.csv").write_text(
        "medewerker;nr;belangrijk;onderwerp;bedrag;btw;wat;bewijs;actie;wie;bevinding\n"
        "Sanne Bakker;1;misschien;Onderwerp;;;Wat;;;;\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"opmerkingen\.csv: regel 2, kolom belangrijk: 'misschien'.*ja.*nee"):
        inlezen.lees_opmerkingen(tmp_path / "opmerkingen.csv")


def test_tekst_die_met_een_formuleteken_begint_blijft_tekst(tmp_path):
    (tmp_path / "bank-notities.csv").write_text(
        'tekst;betreft;opmerking\nnavraag;Sanne;\'=HYPERLINK("x")\n', encoding="utf-8"
    )
    assert inlezen.lees_bank_notities(tmp_path / "bank-notities.csv").opmerking[0] == '=HYPERLINK("x")'


def test_de_sjablonen_zijn_leesbaar():
    """Elk sjabloon in `sjablonen/` leest met de lezer die erbij hoort, en bevat minstens één voorbeeldregel."""
    inst = instellingen.lees(SJABLONEN / "instellingen.toml")
    lezers = {
        "uren.csv": inlezen.lees_uren,
        "bank.csv": inlezen.lees_bank,
        "vervallen-orders.csv": inlezen.lees_vervallen,
        "saldo.csv": inlezen.lees_saldo,
        "orders.csv": lambda pad: inlezen.lees_orders_sjabloon(pad, inst)[1],
        "debiteurenkaart.csv": inlezen.lees_kaart,
        "opmerkingen.csv": inlezen.lees_opmerkingen,
        "zonder-order.csv": inlezen.lees_zonder_order,
        "toezegging.csv": inlezen.lees_toezegging,
        "urenstaat-afwijkend.csv": inlezen.lees_urenstaat_afwijkend,
        "bank-notities.csv": inlezen.lees_bank_notities,
        "to-do.md": inlezen.lees_todo,
    }
    for naam, lees in lezers.items():
        assert len(lees(SJABLONEN / naam)) > 0, naam
    todo = inlezen.lees_todo(SJABLONEN / "to-do.md")
    assert len(todo) == 2 and sum(len(acties) for _, acties in todo) == 3
    bank = inlezen.lees_bank(SJABLONEN / "bank.csv")
    assert {inst.rekening(i).iban for i in bank.rekening}  # de rekeningen in het bank-sjabloon staan in de instellingen


# --- omzetten: debiteurenkaart -------------------------------------------------------------------------------------


def test_kaart_uit_boekhouding_geeft_het_sjabloon(tmp_path):
    kaart = omzetten.kaart_uit_boekhouding(VOORBEELD / "ruw" / "debiteurenkaart.xlsx")
    assert list(kaart.columns) == inlezen.KAART
    facturen, ontvangsten = kaart[kaart.soort == "factuur"], kaart[kaart.soort == "ontvangst"]
    assert len(facturen) == 10 and len(ontvangsten) == 6
    assert (facturen.bedrag > 0).all() and (ontvangsten.bedrag < 0).all()
    assert facturen.nummer.iloc[0] == "202510-0419" and (facturen.rekening == "").all()
    assert set(ontvangsten.rekening) == {IBAN_G, IBAN_GEWOON}
    assert round(kaart.bedrag.sum(), 2) == 9921.16
    omzetten.schrijf_sjabloon(kaart, tmp_path / "debiteurenkaart.csv")
    pd.testing.assert_frame_equal(inlezen.lees_kaart(tmp_path / "debiteurenkaart.csv"), kaart, check_dtype=False)


# --- het dossier ---------------------------------------------------------------------------------------------------


def test_dossier_van_de_voorbeeldset(inst):
    d = dossier.lees(VOORBEELD)
    assert d.map == VOORBEELD and d.inst == inst
    assert len(d.uren) == 154 and d.kop.order.nunique() == 5 and len(d.bank) == 18
    assert d.zonder_order is d.toezegging is d.urenstaat_afwijkend is d.bank_notities is d.vervallen is None
    # de kaart, de opmerkingen en de actielijst horen sinds de uitbreiding van de voorbeeldset erbij
    assert len(d.kaart) == 16 and len(d.opmerkingen) == 3 and sum(len(acties) for _, acties in d.todo) > 0
    assert d.saldo(MEDEWERKERS[0]) is not None and d.saldo(MEDEWERKERS[1]) is None


def test_pdf_gaat_voor_sjabloon(tmp_path):
    map_ = kleine_map(tmp_path)
    (map_ / "orders").mkdir()
    shutil.copy(VOORBEELD / "orders" / "Order I01250491.pdf", map_ / "orders")
    regels = [
        # dezelfde order als de pdf, met andere bedragen: de pdf gaat voor
        "I01250491;OI;05-11-2025;week 41-44;50,00;60,50;Sanne Bakker;;06-10-2025;Tekenaar;Uren;1;50,00;50,00\n",
        "I01259999;OI;22-03-2024;week 9-12;90,00;108,90;Sanne Bakker;;12-03-2024;Tekenaar;Uren;2;45,00;90,00\n",
    ]
    (map_ / "orders.csv").write_text(KOP_ORDERS + "".join(regels), encoding="utf-8")
    d = dossier.lees(map_)
    assert sorted(d.kop.order) == ["I01250491", "I01259999"]
    assert d.kop.set_index("order").excl.to_dict() == {"I01250491": 2387.84, "I01259999": 90.0}
    assert round(d.regels[d.regels.order == "I01250491"].bedrag.sum(), 2) == 2387.84
    assert d.kop.order.is_unique and set(d.kop.bestand) == set(d.regels.bestand)


def test_dossier_met_alleen_een_orders_sjabloon(tmp_path):
    map_ = kleine_map(tmp_path)
    regel = "I01259999;OI;22-03-2024;;90,00;108,90;Sanne Bakker;;12-03-2024;Tekenaar;Uren;2;45,00;90,00\n"
    (map_ / "orders.csv").write_text(KOP_ORDERS + regel, encoding="utf-8")
    assert list(dossier.lees(map_).kop.order) == ["I01259999"]


def test_orders_alleen_uit_sjabloon_hebben_getallen_als_getal(tmp_path):
    """Zonder pdf's is de kop uit de pdf's leeg; samenvoegen met het sjabloon mag de bedragen geen tekst maken: een
    kolom van het type object rondt anders af op een andere manier (32,35 x 0,30 wordt 9,71 in plaats van 9,70)."""
    map_ = kleine_map(tmp_path)
    regel = "I01259999;OI;22-03-2024;;32,35;32,35;Sanne Bakker;;12-03-2024;Tekenaar;Uren;1;32,35;32,35\n"
    (map_ / "orders.csv").write_text(KOP_ORDERS + regel, encoding="utf-8")
    d = dossier.lees(map_)
    assert d.kop[["excl", "incl"]].dtypes.eq("float64").all()
    assert d.regels[["aantal", "tarief", "bedrag"]].dtypes.eq("float64").all()
    assert (d.kop.incl * 0.3).round(2).iloc[0] == 9.7


def test_dossier_zonder_orders_is_een_fout(tmp_path):
    with pytest.raises(ValueError, match="geen orders gevonden"):
        dossier.lees(kleine_map(tmp_path))


def test_dossier_zonder_uren_is_een_fout(tmp_path):
    map_ = kleine_map(tmp_path)
    (map_ / "uren.csv").unlink()
    with pytest.raises(ValueError, match=r"uren\.csv ontbreekt"):
        dossier.lees(map_)


def test_dossier_met_onbekende_rekening_in_de_kaart_is_een_fout(tmp_path, inst):
    map_ = kleine_map(tmp_path)
    (map_ / "orders.csv").write_text(
        KOP_ORDERS + "I01259999;OI;22-03-2024;;90,00;108,90;Sanne Bakker;;12-03-2024;Tekenaar;Uren;2;45,00;90,00\n"
    )
    (map_ / "debiteurenkaart.csv").write_text(
        "datum;soort;nummer;omschrijving;rekening;bedrag\n19-03-2024;ontvangst;;;NL00RABO0000000000;-1,00\n"
    )
    with pytest.raises(
        ValueError,
        match=r"debiteurenkaart\.csv: regel 2, kolom rekening: 'NL00RABO0000000000' staat niet in de instellingen",
    ):
        dossier.lees(map_)


def test_schrijf_en_lees_geeft_hetzelfde_dossier(tmp_path, inst, kop, regels, uren, bankregels):
    kaart = pd.DataFrame(
        {
            "datum": pd.to_datetime(["2024-03-16", "2024-03-27"]),
            "soort": ["factuur", "ontvangst"],
            "nummer": ["2024-0001", ""],
            "omschrijving": ["", "betaling"],
            "rekening": ["", IBAN_G],
            "bedrag": [100.0, -30.0],
        }
    )
    opmerkingen = pd.DataFrame(
        {k: ["x"] for k in inlezen.OPMERKINGEN} | {"belangrijk": ["ja"], "bedrag": [1.5], "nr": ["1"]}
    )
    zonder = pd.DataFrame({k: ["x"] for k in inlezen.ZONDER_ORDER})
    todo = [("Groep", [("Titel", "Toelichting"), ("Tweede", "")])]
    saldo = pd.DataFrame(
        {
            "week": ["2025 wk 24"],
            "soort": ["Uren"],
            "gefactureerd": [1.0],
            "order": [0.0],
            "toewijzing": ["x"],
            "toelichting": ["Een toelichting"],
        }
    )
    map_ = tmp_path / "nieuw"
    dossier.schrijf(
        map_,
        instellingen=(VOORBEELD / "instellingen.toml").read_text(),
        uren=uren,
        kop=kop,
        regels=regels,
        bank=bankregels,
        kaart=kaart,
        opmerkingen=opmerkingen,
        zonder_order=zonder,
        todo=todo,
        saldo={"Sanne Bakker": saldo},
    )
    d = dossier.lees(map_)
    assert d.inst == inst
    pd.testing.assert_frame_equal(d.uren, uren, check_dtype=False)
    _, verwacht_regels = eerste_per_order(kop, regels)
    pd.testing.assert_frame_equal(
        d.regels.drop(columns="bestand"), verwacht_regels.drop(columns="bestand"), check_dtype=False
    )
    pd.testing.assert_frame_equal(d.bank, bankregels, check_dtype=False)
    pd.testing.assert_frame_equal(d.kaart, kaart, check_dtype=False)
    assert d.opmerkingen.bedrag[0] == 1.5 and d.zonder_order.stuk[0] == "x"
    assert d.todo == todo
    assert d.toezegging is None and d.vervallen is None
    pd.testing.assert_frame_equal(d.saldo("Sanne Bakker"), saldo, check_dtype=False)


def test_schrijf_met_onbekende_bron_is_een_fout(tmp_path):
    with pytest.raises(ValueError, match="onbekende bron: tarieven"):
        dossier.schrijf(tmp_path, tarieven=pd.DataFrame())
    with pytest.raises(ValueError, match="kop en regels horen bij elkaar"):
        dossier.schrijf(tmp_path, kop=pd.DataFrame())


@pytest.mark.parametrize(
    ("kolom", "regel"),
    [
        ("totaal_incl", "I01259999;OI;22-03-2024;;90,00;;Sanne Bakker;;12-03-2024;Tekenaar;Uren;2;45,00;90,00\n"),
        ("factuurdatum", "I01259999;OI;;;90,00;108,90;Sanne Bakker;;12-03-2024;Tekenaar;Uren;2;45,00;90,00\n"),
    ],
)
def test_een_order_in_een_sjabloon_zonder_bedrag_incl_btw_of_factuurdatum_is_een_fout(tmp_path, kolom, regel):
    map_ = kleine_map(tmp_path)
    (map_ / "orders.csv").write_text(KOP_ORDERS + regel, encoding="utf-8")
    with pytest.raises(ValueError, match=rf"orders\.csv: regel 2: kolom {kolom} is leeg"):
        dossier.lees(map_)


# --- een typfout in een naam in een hulpbestand ---------------------------------------------------------------------


def _voorbeeld_met(tmp_path, **bronnen):
    """Een kopie van de voorbeeldset met extra of vervangen hulpbestanden (zie `dossier.schrijf`)."""
    map_ = tmp_path / "kopie"
    shutil.copytree(VOORBEELD, map_, ignore=shutil.ignore_patterns("ruw", "verwacht"))
    dossier.schrijf(map_, **bronnen)
    return map_


def _waarschuwingen(map_, caplog) -> list[str]:
    with caplog.at_level(logging.WARNING, logger="weekstaat.dossier"):
        dossier.lees(map_)
    return [r.getMessage() for r in caplog.records]


def test_de_voorbeeldset_geeft_geen_waarschuwing_over_namen(caplog):
    with caplog.at_level(logging.WARNING, logger="weekstaat.dossier"):
        dossier.lees(VOORBEELD)
    assert caplog.records == []


@pytest.mark.parametrize(
    ("bron", "bestand", "tabel"),
    [
        (
            "opmerkingen", "opmerkingen.csv",
            pd.DataFrame(
                [["Sanne Bakkr", "9", "nee", "Typfout", None, "", "x", "", "", "", ""]], columns=inlezen.OPMERKINGEN
            ),
        ),
        (
            "zonder_order", "zonder-order.csv",
            pd.DataFrame([["Sanne Bakkr", "2025 wk 21", "Een mail", "Toelichting"]], columns=inlezen.ZONDER_ORDER),
        ),
        (
            "urenstaat_afwijkend", "urenstaat-afwijkend.csv",
            pd.DataFrame([["Sanne Bakkr", "2025 wk 21", 10.0, "Toelichting"]], columns=inlezen.URENSTAAT_AFWIJKEND),
        ),
        (
            "toezegging", "toezegging.csv",
            pd.DataFrame(
                [["Sanne Bakkr", "T1", "09-04-2025", "2025 wk 21", "", "Uren", "Uren", 8.0, 40.0, 320.0]],
                columns=inlezen.TOEZEGGING,
            ),
        ),
    ],
)  # fmt: skip
def test_een_typfout_in_een_naam_in_een_hulpbestand_geeft_een_waarschuwing(tmp_path, caplog, bron, bestand, tabel):
    map_ = _voorbeeld_met(tmp_path, **{bron: tabel})
    assert _waarschuwingen(map_, caplog) == [f"{bestand}: 'Sanne Bakkr' komt niet voor in de uren"]
    assert dossier.lees(map_).uren is not None  # het dossier wordt wel gelezen


def test_algemeen_en_een_lege_naam_in_de_opmerkingen_zijn_goed(tmp_path, caplog):
    tabel = pd.DataFrame(
        [["Algemeen", "8", "nee", "Voor iedereen", None, "", "x", "", "", "", ""],
         ["", "9", "nee", "Zonder naam", None, "", "x", "", "", "", ""]],
        columns=inlezen.OPMERKINGEN,
    )  # fmt: skip
    assert _waarschuwingen(_voorbeeld_met(tmp_path, opmerkingen=tabel), caplog) == []


def test_een_typfout_in_de_naam_van_een_saldobestand_geeft_een_waarschuwing(tmp_path, caplog):
    map_ = _voorbeeld_met(tmp_path)
    shutil.copy(map_ / "saldo Sanne Bakker.csv", map_ / "saldo Sanne Bakkr.csv")
    assert _waarschuwingen(map_, caplog) == ["saldo Sanne Bakkr.csv: 'Sanne Bakkr' komt niet voor in de uren"]
