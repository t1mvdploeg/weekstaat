"""Tests voor het inlezen van orders uit pdf's en mails."""

import logging
import re
import shutil

import pandas as pd
import pytest
from conftest import VOORBEELD
from pypdf import PdfWriter

from weekstaat import orders

ORDERS = VOORBEELD / "orders"
MAIL = VOORBEELD / "mail"


@pytest.fixture(scope="module")
def ingelezen(inst):
    return orders.lees_orders([ORDERS, MAIL], inst)


def _per_bestand(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("bestand", kind="stable").reset_index(drop=True)


def test_voorbeeldset_geeft_de_verwachte_kop_en_regels(ingelezen, kop, regels):
    pd.testing.assert_frame_equal(_per_bestand(ingelezen[0]), _per_bestand(kop))
    pd.testing.assert_frame_equal(_per_bestand(ingelezen[1]), _per_bestand(regels))


def test_order_over_meer_pagina_s_is_een_order(ingelezen):
    kop, regels = ingelezen
    bestand = "Orders I01250324 en I02250348.pdf [I02250348]"  # 95 regels over drie pagina's
    assert (kop.bestand == bestand).sum() == 1
    assert (regels.bestand == bestand).sum() == 95
    assert regels[regels.bestand == bestand].bedrag.sum().round(2) == 9560.49


def test_twee_orders_in_een_pdf_krijgen_het_ordernummer_achter_de_naam(ingelezen):
    kop, regels = ingelezen
    naam = "Orders I01250324 en I02250348.pdf"
    assert sorted(kop[kop.bestand.str.startswith(naam)].bestand) == [f"{naam} [I01250324]", f"{naam} [I02250348]"]
    assert set(regels[regels.bestand.str.startswith(naam)].order) == {"I01250324", "I02250348"}


def test_order_die_alleen_in_een_mail_zit(ingelezen):
    kop, _ = ingelezen
    rij = kop[kop.bestand == "Order I01250361 (bijlage in Order per mail.eml).pdf"]
    assert rij.order.tolist() == ["I01250361"]


def test_doorgestuurde_mail_geeft_dezelfde_order_twee_keer(ingelezen):
    kop, _ = ingelezen
    rijen = kop[kop.order == "I02250362"]
    assert rijen.bestand.tolist() == ["Order I02250362.pdf", "Order I02250362 (bijlage in Doorgestuurde order.eml).pdf"]
    assert rijen.excl.nunique() == 1


def test_zonder_mailmap_ontbreken_de_bijlagen(inst):
    kop, _ = orders.lees_orders([ORDERS], inst)
    assert not kop.bestand.str.contains("bijlage").any()


def test_hetzelfde_bestand_in_twee_mappen_telt_een_keer(inst, tmp_path, caplog):
    for m in ("a", "b"):
        (tmp_path / m).mkdir()
        shutil.copy(ORDERS / "Order I01250491.pdf", tmp_path / m)
    with caplog.at_level(logging.INFO, logger="weekstaat.orders"):
        kop, regels = orders.lees_orders([tmp_path / "a", tmp_path / "b"], inst)
    assert kop.bestand.tolist() == ["Order I01250491.pdf"]
    assert len(regels) == 8
    assert "dubbel" in caplog.text


def test_twee_verschillende_bestanden_met_dezelfde_naam_is_een_fout(inst, tmp_path):
    for m, bron in (("a", "Order I01250491.pdf"), ("b", "Order I02250362.pdf")):
        (tmp_path / m).mkdir()
        shutil.copy(ORDERS / bron, tmp_path / m / "Order.pdf")
    with pytest.raises(ValueError, match=r"Order\.pdf"):
        orders.lees_orders([tmp_path / "a", tmp_path / "b"], inst)


def test_pdf_zonder_order_wordt_overgeslagen(inst, tmp_path, caplog):
    schrijver = PdfWriter()
    schrijver.add_blank_page(width=200, height=200)
    with open(tmp_path / "leeg.pdf", "wb") as f:
        schrijver.write(f)
    shutil.copy(ORDERS / "Order I01250491.pdf", tmp_path)
    with caplog.at_level(logging.INFO, logger="weekstaat.orders"):
        kop, _ = orders.lees_orders([tmp_path], inst)
    assert kop.bestand.tolist() == ["Order I01250491.pdf"]
    assert "leeg.pdf" in caplog.text


def test_regels_die_niet_optellen_tot_het_totaal_zijn_een_fout(inst, tmp_path, monkeypatch):
    shutil.copy(ORDERS / "Order I01250491.pdf", tmp_path)
    echt = orders._paginas
    monkeypatch.setattr(
        orders,
        "_paginas",
        lambda inhoud: [re.sub(r"(Dit bedrag is exclusief btw\s+)[\d.,]+", r"\g<1>1,00", p) for p in echt(inhoud)],
    )
    with pytest.raises(ValueError, match=r"Order I01250491\.pdf.*2387\.84.*1\.00"):
        orders.lees_orders([tmp_path], inst)


def test_een_zeer_lange_regel_kost_geen_rekentijd():
    lijn = "01-01-2025 " + "x " * 100_000 + "Uren 8,00 40,00 320,00"
    (regel,) = orders._regels(lijn)
    assert (regel["eenheid"], regel["aantal"], regel["tarief"], regel["bedrag"]) == ("Uren", 8.0, 40.0, 320.0)


def test_een_regel_wordt_van_achteren_gelezen():
    assert orders._ontleed("  06-10-2025  KM dienstreis   Kilometers   52,00") == (
        "06-10-2025", "KM dienstreis", "Kilometers", 52.0, None, None,
    )  # fmt: skip
    assert orders._ontleed("  10-10-2025  Maaltijdvergoeding  1,00  14,55  14,55")[2:] == (None, 1.0, 14.55, 14.55)
    assert orders._ontleed("  correctie uurtarief op 16-11-2025  Uren  5,00  17,03  85,15")[:2] == (
        None, "correctie uurtarief op 16-11-2025",
    )  # fmt: skip
    assert orders._ontleed("Naam medewerker:   Sanne Bakker") is None


@pytest.mark.parametrize(
    ("zoek", "vervang", "wat"),
    [
        (r"Te betalen in", "Totaal in", "het bedrag incl. btw"),
        (r"(I0\d{7}\s+-?[\d.,]+\s+)\d\d-\d\d-\d{4}", r"\g<1>onbekend", "de factuurdatum"),
    ],
)
def test_een_order_zonder_bedrag_incl_btw_of_factuurdatum_is_een_fout(inst, tmp_path, monkeypatch, zoek, vervang, wat):
    """Zonder `incl` of datum kan de order niet aan een betaling worden gekoppeld: dat verdwijnt niet stil."""
    shutil.copy(ORDERS / "Order I01250491.pdf", tmp_path)
    echt = orders._paginas
    monkeypatch.setattr(orders, "_paginas", lambda inhoud: [re.sub(zoek, vervang, p) for p in echt(inhoud)])
    with pytest.raises(ValueError, match=rf"{wat}.*niet gevonden.*Order I01250491\.pdf \(order I01250491\)"):
        orders.lees_orders([tmp_path], inst)
