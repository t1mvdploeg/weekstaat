"""Bouwt ruwe exporten om naar de sjablonen van `inlezen.py`.
Gebruik dit als de bron een export in een bekende vorm is."""

import re
from pathlib import Path

import pandas as pd

from weekstaat import inlezen
from weekstaat.instellingen import Instellingen

# Kolommen van het Salesforce-uitvoerrapport: kop in de export -> kolom in het sjabloon voor uren.
SALESFORCE = {
    "Werknemer": "medewerker",
    "Timesheet: Naam van Timesheet": "urenstaat",
    "Datum": "datum",
    "Uren": "uren",
    "Urenregel: Tariefregel: Naam Tariefregel": "soort",
    "Extern tarief": "tarief",
    "Status Timesheet": "status",
    "Factuurnummer": "factuurnummer",
    "Factuurdatum": "factuurdatum",
    "Omzet": "bedrag",
    "Credit factuurnummer": "creditnummer",
    "Credit omzet": "creditbedrag",
    "Factuurbedrag": "factuurbedrag",
}

# Rabobank-export: positie en kop van de kolommen die we gebruiken, in de volgorde van het sjabloon voor de bank.
RABOBANK = {4: "Datum", 0: "IBAN/BBAN", 6: "Bedrag", 9: "Naam tegenpartij", 19: "Omschrijving - 1"}
# De referentie van een boeking: de kolom met de boekingsreferentie, anders die met de transactiereferentie.
REFERENTIES = ("Boekingsreferentie", "Transactiereferentie")
# Debiteurenkaart: de kolommen die we gebruiken.
KAART = ["Datum", "Dagboek", "Omschrijving", "Te vorderen"]
IBAN = re.compile(r"[A-Z]{2}\d{2}[ A-Z0-9]{10,}")
UIT_KAART = " (ontvangst uit de debiteurenkaart; ontbreekt in de bankexport)"


def _controleer(pad: str | Path, aanwezig: list, verwacht: list) -> None:
    mist = [k for k in verwacht if k not in aanwezig]
    if mist:
        raise ValueError(f"{pad}: kolom {', '.join(mist)} ontbreekt. Verwacht: {'; '.join(verwacht)}")


def uren_uit_salesforce(pad: str | Path) -> pd.DataFrame:
    """Zet het eerste blad van een Salesforce-urenrapport om naar de kolommen van `inlezen.UREN`.
    `rij` is het rijnummer in het blad (de eerste gegevensrij is 2). Voetregels zonder urenstaat vallen af."""
    df = pd.read_excel(pad, sheet_name=0, header=0)
    _controleer(pad, list(df.columns), list(SALESFORCE))
    df.insert(0, "rij", df.index + 2)
    df = df[df["Timesheet: Naam van Timesheet"].notna()]
    df = df.rename(columns=SALESFORCE)[["rij", *SALESFORCE.values()]].reset_index(drop=True)
    for k in ("datum", "factuurdatum"):
        df[k] = pd.to_datetime(df[k], dayfirst=True)
    return df


def _kaart(pad: str | Path) -> pd.DataFrame:
    """De regels van de debiteurenkaart (kopregel op rij 3) met een bedrag."""
    df = pd.read_excel(pad, sheet_name=0, header=2)
    _controleer(pad, list(df.columns), KAART)
    df = df[pd.to_numeric(df["Te vorderen"], errors="coerce").notna()]
    return df.assign(Dagboek=df.Dagboek.fillna("").astype(str), Datum=pd.to_datetime(df.Datum))


def kaart_uit_boekhouding(pad: str | Path) -> pd.DataFrame:
    """Zet de debiteurenkaart uit de boekhouding om naar de kolommen van `inlezen.KAART`.

    Een dagboek dat met "V" begint (het verkoopboek) is een factuur; `nummer` is dan het factuurnummer, dat in de
    omschrijving staat. Alle andere regels zijn ontvangsten; het IBAN van de rekening staat in het dagboek. Het
    bedrag blijft zoals op de kaart: een factuur positief, een ontvangst negatief."""
    kaart = _kaart(pad)
    factuur = kaart.Dagboek.str.startswith("V")
    omschrijving = kaart.Omschrijving.fillna("").astype(str).str.strip()
    iban = kaart.Dagboek.map(lambda d: m.group().replace(" ", "") if (m := IBAN.search(d)) else "")
    uit = pd.DataFrame(
        dict(
            datum=kaart.Datum,
            soort=factuur.map({True: "factuur", False: "ontvangst"}),
            nummer=omschrijving.where(factuur, ""),
            omschrijving=omschrijving.where(~factuur, ""),
            rekening=iban.where(~factuur, ""),
            bedrag=kaart["Te vorderen"].astype(float),
        )
    )
    return uit.reset_index(drop=True)


def _export(pad: str | Path) -> pd.DataFrame:
    df = pd.read_csv(pad, dtype=str, encoding="utf-8-sig")
    _controleer(pad, [df.columns[i] if i < len(df.columns) else None for i in RABOBANK], list(RABOBANK.values()))
    # De referentie is niet verplicht (een export van een andere bank heeft hem niet); zonder blijft hij leeg.
    boeking, transactie = (
        df[k] if k in df.columns else pd.Series(None, index=df.index, dtype=str) for k in REFERENTIES
    )
    referentie = boeking.fillna(transactie).fillna("").str.strip()
    df = df.iloc[:, list(RABOBANK)].set_axis(["datum", "rekening", "bedrag", "tegenpartij", "omschrijving"], axis=1)
    df["datum"] = pd.to_datetime(df.datum, format="%d-%m-%Y")
    df["bedrag"] = df.bedrag.str.replace(".", "", regex=False).str.replace(",", ".").astype(float)
    df["rekening"] = df.rekening.str.replace(" ", "")
    df["referentie"] = referentie
    return df.fillna({"tegenpartij": "", "omschrijving": ""})


def bank_uit_rabobank(paden: list, inst: Instellingen, debiteurenkaart: str | Path | None = None) -> pd.DataFrame:
    """Zet Rabobank-exporten (csv) om naar de kolommen van `inlezen.BANK` plus `referentie`.

    De exporten mogen elkaar overlappen (een volledige export naast een zoekresultaat): ze worden samengevoegd en een
    boeking die er twee keer in staat (zelfde referentie, rekening, datum en bedrag) telt één keer, de eerste.
    Boekingen zonder referentie tellen allemaal; zie `inlezen.ontdubbel_bank`. Daarna
    volgt per rekening, in de volgorde waarin de rekeningen voorkomen, wat de export had.

    Met een debiteurenkaart komen direct na de regels van een rekening de ontvangsten die wel op de kaart staan
    maar niet in de export, met een lege tegenpartij. Dat zijn alleen ontvangsten op een dag waarop de export op die
    rekening niets heeft: de kaart toont soms alleen het restant van een betaling dat nog niet is afgeletterd, en
    dat staat dan al in de export. De kaart hoort bij de rekening waarvan het IBAN in het dagboek staat;
    verkoopboekregels tellen niet mee."""
    kaart = _kaart(debiteurenkaart) if debiteurenkaart else None
    alles = pd.concat([_export(pad) for pad in paden]).reset_index(drop=True)
    alles = inlezen.ontdubbel_bank(alles)
    delen = []
    for iban, df in alles.groupby("rekening", sort=False):
        if kaart is not None:
            iban = inst.rekening(iban).iban
            eigen = kaart[~kaart.Dagboek.str.startswith("V") & kaart.Dagboek.str.replace(" ", "").str.contains(iban)]
            eigen = eigen[~eigen.Datum.isin(set(df.datum))]
            extra = pd.DataFrame(dict(
                datum=eigen.Datum, rekening=iban, bedrag=-eigen["Te vorderen"], tegenpartij="",
                omschrijving=eigen.Omschrijving.astype(str) + UIT_KAART, referentie="",
            ))  # fmt: skip
            df = pd.concat([df, extra])
        delen.append(df)
    return (pd.concat(delen) if delen else alles).reset_index(drop=True)


def schrijf_sjabloon(df: pd.DataFrame, pad: str | Path) -> None:
    """Schrijft in de vorm van de sjablonen: puntkomma, decimale komma en datums als dag-maand-jaar.

    Tekst die Excel als formule zou lezen, krijgt een apostrof ervoor; `inlezen` haalt die er weer af."""
    df = df.copy()
    for k in df.select_dtypes(include=["object", "string"]).columns:
        df[k] = df[k].map(lambda v: "'" + v if isinstance(v, str) and v.startswith(inlezen.FORMULETEKENS) else v)
    df.to_csv(pad, sep=";", decimal=",", date_format="%d-%m-%Y", index=False)
