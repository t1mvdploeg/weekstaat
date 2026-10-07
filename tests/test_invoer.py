"""Invoer die een gebruiker per ongeluk aanlevert geeft een melding waar hij iets mee kan, geen stacktrace."""

import re
import shutil

import pytest
from conftest import VOORBEELD

from weekstaat import inlezen
from weekstaat.cli import main


@pytest.fixture
def map_(tmp_path):
    """Een kopie van de voorbeeldset zonder de orders: klein genoeg om per test te bewerken."""
    doel = tmp_path / "invoer"
    shutil.copytree(VOORBEELD, doel, ignore=shutil.ignore_patterns("orders", "mail", "ruw", "verwacht"))
    (doel / "orders").mkdir()
    shutil.copy(next((VOORBEELD / "orders").glob("*.pdf")), doel / "orders")
    return doel


def vervang(pad, oud, nieuw):
    tekst = pad.read_text()
    assert oud in tekst
    pad.write_text(tekst.replace(oud, nieuw, 1))


def zet_veld(pad, kolom, waarde):
    """Zet in de eerste gegevensregel (regel 2 van het bestand) de waarde van een kolom."""
    regels = pad.read_text().split("\n")
    velden = regels[1].split(";")
    velden[regels[0].split(";").index(kolom)] = waarde
    pad.write_text("\n".join([regels[0], ";".join(velden), *regels[2:]]))


def melding(map_, capsys, *extra):
    assert main(["aansluiten", str(map_), "--uit", str(map_ / "uit"), *extra]) == 1
    return capsys.readouterr().err


def test_een_verkeerde_datum_noemt_bestand_regel_en_kolom(map_, capsys):
    zet_veld(map_ / "uren.csv", "datum", "2025/01/06")
    fout = melding(map_, capsys)
    assert "uren.csv: regel 2, kolom datum: '2025/01/06' is geen datum" in fout


def test_tekst_in_een_getalkolom_noemt_de_waarde(map_, capsys):
    zet_veld(map_ / "bank.csv", "bedrag", "12,28,97")
    assert "bank.csv: regel 2, kolom bedrag: '12,28,97' is geen getal" in melding(map_, capsys)


def test_een_lege_medewerker_noemt_de_regel(map_, capsys):
    zet_veld(map_ / "uren.csv", "medewerker", "")
    assert "uren.csv: regel 2: kolom medewerker is leeg" in melding(map_, capsys)


def test_uren_met_alleen_een_kopregel(map_, capsys):
    (map_ / "uren.csv").write_text((map_ / "uren.csv").read_text().splitlines()[0] + "\n")
    assert "er staan geen urenregels in" in melding(map_, capsys)


@pytest.mark.parametrize(
    ("oud", "nieuw", "verwacht"),
    [
        ("aandeel = 0.30", 'aandeel = "30%"', "het aandeel van een rekening is een getal"),
        ('iban = "NL00RABO0991000001"\n', "", "een [[rekening]] heeft naam, iban en aandeel"),
        ("aandeel = 0.30", "aandeel = 0.40", "tellen niet op tot 1"),
        ("[[entiteit]]", "[[entiteit", "geen geldig TOML"),
    ],
)
def test_fouten_in_de_instellingen(map_, capsys, oud, nieuw, verwacht):
    vervang(map_ / "instellingen.toml", oud, nieuw)
    assert verwacht in melding(map_, capsys)


def test_een_kapotte_pdf_noemt_het_bestand(map_, capsys):
    (map_ / "orders" / "kapot.pdf").write_bytes(b"%PDF-1.4 dit is geen pdf")
    assert "kapot.pdf: geen leesbare pdf" in melding(map_, capsys)


def test_geen_enkele_order_is_een_fout_en_geen_lege_uitkomst(map_, capsys):
    shutil.rmtree(map_ / "orders")
    assert "geen orders gevonden" in melding(map_, capsys)


def test_een_rekening_die_niet_in_de_instellingen_staat(map_, capsys):
    vervang(map_ / "bank.csv", "NL00RABO0991000001", "NL00RABO0000000000")
    tekst = melding(map_, capsys)  # met het bestand, de regel en de kolom
    assert re.search(r"bank\.csv: regel \d+, kolom rekening: 'NL00RABO0000000000' staat niet in de instellingen", tekst)


def test_uit_wijst_naar_een_bestand(map_, capsys):
    (map_ / "uit").write_text("")
    assert "--uit moet een map zijn" in melding(map_, capsys)


def test_de_map_bestaat_niet(tmp_path, capsys):
    assert "instellingen.toml ontbreekt" in melding(tmp_path / "nergens", capsys)


def test_een_bank_met_alleen_een_kopregel_telt_als_geen_bank(map_, capsys):
    (map_ / "bank.csv").write_text("datum;rekening;bedrag;tegenpartij;omschrijving\n")
    naam = "Thijmen Lucas Van Dijk"
    assert main(["aansluiten", str(map_), "--uit", str(map_ / "uit"), "--medewerker", naam]) == 0
    assert "bankregels" not in capsys.readouterr().out


def test_een_pdf_zonder_order_wordt_gemeld(map_, capsys):
    from pypdf import PdfWriter

    leeg = PdfWriter()
    leeg.add_blank_page(width=200, height=200)
    leeg.write(map_ / "orders" / "brief.pdf")
    assert main(["aansluiten", str(map_), "--uit", str(map_ / "uit"), "--medewerker", "Thijmen Lucas Van Dijk"]) == 0
    assert "let op: overgeslagen (geen order): brief.pdf" in capsys.readouterr().err


def test_een_orders_sjabloon_dat_niet_optelt_noemt_de_order_en_de_bedragen(map_, capsys):
    kop = (VOORBEELD.parent / "sjablonen" / "orders.csv").read_text().splitlines()[0]
    regel = "I01250001;OI;11-03-2024;week 9-12;100,00;121,00;Sanne Bakker;;12-03-2024;Modelleur;Uren;2;45,00;90,00"
    (map_ / "orders.csv").write_text(f"{kop}\n{regel}\n")
    fout = melding(map_, capsys)
    assert "orders.csv: order I01250001: de regels tellen op tot 90,00, het totaal is 100,00" in fout


def test_uren_als_excel_werken_in_de_opdrachtregel(map_, capsys):
    import pandas as pd

    uren = pd.read_csv(map_ / "uren.csv", sep=";", decimal=",", dtype=str)
    for k in ("datum", "factuurdatum"):
        uren[k] = pd.to_datetime(uren[k], format="%d-%m-%Y")
    for k in ("rij", "uren", "tarief", "bedrag", "creditbedrag", "factuurbedrag"):
        uren[k] = pd.to_numeric(uren[k].str.replace(",", "."))
    uren.to_excel(map_ / "uren.xlsx", index=False)
    (map_ / "uren.csv").unlink()
    naam = "Thijmen Lucas Van Dijk"
    assert main(["aansluiten", str(map_), "--uit", str(map_ / "uit"), "--medewerker", naam]) == 0
    assert "154 urenregels" in capsys.readouterr().out


def test_een_csv_in_de_oude_tekencodering_van_excel_wordt_gelezen(map_, capsys, tmp_path):
    """Nederlandse Excel bewaart "csv" als cp1252: een é is dan één byte en geen geldige utf-8."""
    pad = map_ / "bank.csv"
    tekst = pad.read_text(encoding="utf-8")
    pad.write_bytes(tekst.replace("Oeverland", "Oeverländ é").encode("cp1252"))
    assert main(["aansluiten", str(map_), "--uit", str(tmp_path / "uit")]) == 0
    assert "geen leesbaar csv-bestand" not in capsys.readouterr().err
    assert "Oeverländ é" in inlezen.lees_bank(pad).tegenpartij.iloc[0]
    # utf-8 met een BOM blijft ook goed
    pad.write_bytes(b"\xef\xbb\xbf" + tekst.encode("utf-8"))
    assert inlezen.lees_bank(pad).columns[0] == "datum"
