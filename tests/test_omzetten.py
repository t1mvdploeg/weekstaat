"""Tests voor het omzetten van ruwe exporten naar de sjablonen."""

import pandas as pd
import pytest
from conftest import VOORBEELD

from weekstaat import inlezen, omzetten

RUW = VOORBEELD / "ruw"


def test_uren_uit_salesforce_geeft_het_sjabloon(tmp_path):
    uren = omzetten.uren_uit_salesforce(RUW / "uren-export.xlsx")
    assert list(uren.columns) == inlezen.UREN
    omzetten.schrijf_sjabloon(uren, tmp_path / "uren.csv")
    pd.testing.assert_frame_equal(inlezen.lees_uren(tmp_path / "uren.csv"), inlezen.lees_uren(VOORBEELD / "uren.csv"))


def test_uren_rij_is_het_rijnummer_in_het_blad():
    uren = omzetten.uren_uit_salesforce(RUW / "uren-export.xlsx")
    assert uren.rij.iloc[0] == 2
    assert uren.rij.is_monotonic_increasing


def test_bank_uit_rabobank_met_debiteurenkaart(inst):
    paden = [RUW / "bank-g-rekening.csv", RUW / "bank-gewone-rekening.csv"]
    bank = omzetten.bank_uit_rabobank(paden, inst, RUW / "debiteurenkaart.xlsx")
    assert (
        list(bank.columns) == [*inlezen.BANK, "referentie"] and (bank.referentie == "").all()
    )  # de export heeft er geen
    pd.testing.assert_frame_equal(bank.drop(columns="referentie"), inlezen.lees_bank(VOORBEELD / "bank.csv"))


def test_bank_zonder_debiteurenkaart_heeft_alleen_de_exportregels(inst):
    paden = [RUW / "bank-g-rekening.csv", RUW / "bank-gewone-rekening.csv"]
    zonder = omzetten.bank_uit_rabobank(paden, inst)
    met = inlezen.lees_bank(VOORBEELD / "bank.csv")
    assert list(zonder.columns) == [*inlezen.BANK, "referentie"]
    assert len(zonder) == len(met) - met.omschrijving.str.contains("ontvangst uit de debiteurenkaart").sum()


def test_bank_sjabloon_wordt_teruggelezen(inst, tmp_path):
    bank = omzetten.bank_uit_rabobank([RUW / "bank-gewone-rekening.csv"], inst)
    omzetten.schrijf_sjabloon(bank, tmp_path / "bank.csv")
    pd.testing.assert_frame_equal(inlezen.lees_bank(tmp_path / "bank.csv"), bank, check_dtype=False)


def test_bank_met_verkeerde_kolommen_geeft_een_duidelijke_fout(inst, tmp_path):
    pad = tmp_path / "bank.csv"
    pad.write_text("datum;bedrag\n01-01-2026;1,00\n")
    with pytest.raises(ValueError, match=r"bank\.csv.*IBAN/BBAN"):
        omzetten.bank_uit_rabobank([pad], inst)


def test_uren_met_verkeerde_kolommen_geeft_een_duidelijke_fout(tmp_path):
    pad = tmp_path / "uren.xlsx"
    pd.DataFrame({"Werknemer": ["a"], "Uren": [1.0]}).to_excel(pad, index=False)
    with pytest.raises(ValueError, match=r"uren\.xlsx.*Timesheet"):
        omzetten.uren_uit_salesforce(pad)


def test_debiteurenkaart_met_andere_kolommen_geeft_een_duidelijke_fout(inst, tmp_path):
    pad = tmp_path / "kaart.xlsx"
    pd.DataFrame({"Iets": range(4)}).to_excel(pad, index=False)
    with pytest.raises(ValueError, match=r"kaart\.xlsx.*Dagboek"):
        omzetten.bank_uit_rabobank([RUW / "bank-g-rekening.csv"], inst, pad)


def test_tekst_die_excel_als_formule_leest_wordt_geen_formule(tmp_path):
    bank = pd.DataFrame(
        {
            "datum": pd.to_datetime(["2025-06-27"]),
            "rekening": ["NL00RABO0991000001"],
            "bedrag": [10.0],
            "tegenpartij": ["+31 afdeling inkoop"],
            "omschrijving": ['=HYPERLINK("http://voorbeeld.example";"klik")'],
        }
    )
    pad = tmp_path / "bank.csv"
    omzetten.schrijf_sjabloon(bank, pad)
    regel = pad.read_text().splitlines()[1]
    assert ";'+31 afdeling inkoop;" in regel and "'=HYPERLINK" in regel
    terug = inlezen.lees_bank(pad)
    assert terug.omschrijving[0] == bank.omschrijving[0] and terug.tegenpartij[0] == bank.tegenpartij[0]
