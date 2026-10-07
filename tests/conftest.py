"""Gedeelde invoer voor de tests: de voorbeeldset en de verwachte uitkomst die erbij hoort."""

from pathlib import Path

import pandas as pd
import pytest

from weekstaat import inlezen, instellingen

VOORBEELD = Path(__file__).parent.parent / "voorbeeld"
VERWACHT = VOORBEELD / "verwacht"
MEDEWERKERS = ["Sanne Bakker", "Thijmen Lucas Van Dijk"]


@pytest.fixture(scope="session")
def inst():
    return instellingen.lees(VOORBEELD / "instellingen.toml")


@pytest.fixture(scope="session")
def uren():
    return inlezen.lees_uren(VOORBEELD / "uren.csv")


@pytest.fixture(scope="session")
def bankregels():
    return inlezen.lees_bank(VOORBEELD / "bank.csv")


@pytest.fixture(scope="session")
def vervallen():
    """De voorbeeldset heeft geen vervallen orders; het bestand is niet verplicht."""
    pad = VOORBEELD / "vervallen-orders.csv"
    return inlezen.lees_vervallen(pad) if pad.exists() else None


@pytest.fixture(scope="session")
def kop():
    """De koppen van alle ingelezen orders, zoals `orders.lees_orders` ze moet teruggeven."""
    return pd.read_csv(VERWACHT / "orders-kop.csv", parse_dates=["factuurdatum"]).fillna({"referentie": ""})


@pytest.fixture(scope="session")
def regels():
    """De regels van alle ingelezen orders, zoals `orders.lees_orders` ze moet teruggeven."""
    return pd.read_csv(VERWACHT / "orders-regels.csv", parse_dates=["datum"])


def saldo(naam: str) -> pd.DataFrame | None:
    """Het eigen oordeel van de gebruiker voor een medewerker; niet elke medewerker heeft zo'n bestand."""
    pad = VOORBEELD / f"saldo {naam}.csv"
    return inlezen.lees_saldo(pad) if pad.exists() else None


def verwacht_per_dag(naam: str) -> pd.DataFrame:
    tekst = ["urenstaten", "facturen", "entiteit", "projecten", "verzamel", "toelichting"]
    return pd.read_csv(VERWACHT / f"per-dag {naam}.csv", parse_dates=["datum"]).fillna({k: "" for k in tekst})


def verwacht_bank(naam: str) -> pd.DataFrame:
    tekst = ["orders", "medewerkers", "opmerking"]
    return pd.read_csv(VERWACHT / f"bank {naam}.csv", parse_dates=["datum"]).fillna({k: "" for k in tekst})


SJABLONEN = Path(__file__).parent.parent / "sjablonen"
_RABOBANK_KOPPEN = list(
    pd.read_csv(VOORBEELD / "ruw" / "bank-g-rekening.csv", dtype=str, encoding="utf-8-sig", nrows=0).columns
)


def schrijf_rabobank(pad: Path, regels: list[dict]) -> Path:
    """Schrijft een Rabobank-export met alle kolommen van het echte formaat. Een regel is een dict met de kolommen
    die ertoe doen ('IBAN/BBAN', 'Datum', 'Bedrag', 'Naam tegenpartij', 'Omschrijving - 1', 'Boekingsreferentie',
    'Transactiereferentie'); de rest blijft leeg."""
    tabel = pd.DataFrame(regels, columns=_RABOBANK_KOPPEN)
    tabel.to_csv(pad, index=False, encoding="utf-8-sig")
    return pad


def schrijf_kaart(pad: Path, regels: list[tuple]) -> Path:
    """Schrijft een debiteurenkaart uit de boekhouding (kopregel op rij 3). Een regel is (datum, dagboek,
    omschrijving, bedrag)."""
    kaart = pd.DataFrame(regels, columns=["Datum", "Dagboek", "Omschrijving", "Te vorderen"])
    kaart.to_excel(pad, index=False, startrow=2)
    return pad
