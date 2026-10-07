"""Leest de invoerbestanden die de gebruiker zelf aanlevert: uren, bank, orders uit een sjabloon, de debiteurenkaart
en de hulpbestanden.

Een csv-bestand heeft in alle gevallen dezelfde vorm, zodat het in een Nederlandse Excel te openen en te bewerken is:
puntkomma tussen de kolommen, komma als decimaalteken en datums als dag-maand-jaar. Elk bestand mag ook een
Excel-werkboek (.xlsx) zijn met dezelfde kolommen op het eerste blad; de lezer kiest op de extensie. In Excel mogen
datums en bedragen echte waarden zijn of tekst in dezelfde schrijfwijze als in de csv.
De kolommen staan in `sjablonen/`. Orders komen ook uit pdf's en mails; zie `orders.py`."""

import datetime
import logging
import re
import zipfile
from pathlib import Path

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException

from . import orders
from .instellingen import Instellingen
from .opmaak import nl_bedrag

log = logging.getLogger(__name__)

UREN = [
    "rij", "medewerker", "urenstaat", "datum", "uren", "soort", "tarief", "status",
    "factuurnummer", "factuurdatum", "bedrag", "creditnummer", "creditbedrag", "factuurbedrag",
]  # fmt: skip
BANK = ["datum", "rekening", "bedrag", "tegenpartij", "omschrijving"]
# De kolommen van de sjablonen die de tool erbij leest. Voor orders staat elke regel van een order met de kop van die
# order erbij; `lees_orders_sjabloon` maakt daar weer een kop en regels van, zoals `orders.lees_orders`.
ORDERS = [
    "order", "entiteit", "factuurdatum", "referentie", "totaal_excl", "totaal_incl", "medewerker", "project",
    "datum", "omschrijving", "eenheid", "aantal", "tarief", "bedrag",
]  # fmt: skip
KAART = ["datum", "soort", "nummer", "omschrijving", "rekening", "bedrag"]
OPMERKINGEN = [
    "medewerker", "nr", "belangrijk", "onderwerp", "bedrag", "btw", "wat", "bewijs", "actie", "wie", "bevinding",
]  # fmt: skip
ZONDER_ORDER = ["medewerker", "week", "stuk", "toelichting"]
TOEZEGGING = ["medewerker", "nr", "datum", "week", "maand", "omschrijving", "eenheid", "aantal", "prijs", "bedrag"]
URENSTAAT_AFWIJKEND = ["medewerker", "week", "bedrag", "toelichting"]
BANK_NOTITIES = ["tekst", "betreft", "opmerking"]
SOORTEN_KAART = ("factuur", "ontvangst")
# Tekst die zo begint, leest Excel als formule. Bij het schrijven van een sjabloon komt er een apostrof voor;
# bij het lezen gaat die er weer af. Een omschrijving van een betaler mag in Excel geen formule worden.
FORMULETEKENS = ("=", "+", "-", "@", "\t", "\r")
TEKST = {"medewerker": str, "urenstaat": str, "soort": str, "status": str, "factuurnummer": str, "creditnummer": str}


def vind(map_: Path, stam: str) -> Path | None:
    """Het bestand `<stam>.csv` in de map, anders `<stam>.xlsx`, anders niets."""
    for extensie in (".csv", ".xlsx"):
        if (pad := Path(map_) / f"{stam}{extensie}").is_file():
            return pad
    return None


def _ruw(pad: str | Path, **extra) -> pd.DataFrame:
    """Leest een csv-bestand (puntkomma, decimale komma; utf-8, anders cp1252) of het eerste blad van een .xlsx, naar de
    extensie."""
    if Path(pad).suffix.lower() == ".xlsx":
        try:
            return pd.read_excel(pad, sheet_name=0, **extra)
        except (zipfile.BadZipFile, InvalidFileException, ValueError) as fout:  # een kapotte .xlsx is een ValueError
            raise ValueError(f"{pad}: geen leesbaar Excel-bestand ({fout})") from None
    try:
        try:
            return pd.read_csv(pad, sep=";", decimal=",", encoding="utf-8-sig", **extra)  # utf-8, met of zonder BOM
        except UnicodeDecodeError:  # een Nederlandse Excel bewaart een csv in de oude tekencodering
            return pd.read_csv(pad, sep=";", decimal=",", encoding="cp1252", **extra)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as fout:
        raise ValueError(f"{pad}: geen leesbaar csv-bestand met puntkomma's ({fout})") from None


def _lees(pad: str | Path, kolommen: list[str], datums=(), getallen=(), verplicht=(), **extra) -> pd.DataFrame:
    """Leest een bestand in de vorm van de sjablonen. Een fout in de inhoud geeft een melding met het bestand, de
    regel en de kolom, zodat de gebruiker hem in zijn rekenblad kan opzoeken (regel 1 is de kopregel)."""
    df = _ruw(pad, **extra)
    mist = [k for k in kolommen if k not in df.columns]
    if mist:
        raise ValueError(f"{pad}: kolom {', '.join(mist)} ontbreekt. Verwacht: {'; '.join(kolommen)}")
    for k in getallen:
        als_getal = pd.to_numeric(df[k].astype(str).str.replace(",", ".", regex=False), errors="coerce")
        fout = df[k].notna() & als_getal.isna()
        if fout.any():
            i = fout.idxmax()
            raise ValueError(f"{pad}: regel {i + 2}, kolom {k}: '{df[k][i]}' is geen getal (verwacht zoals 1234,56)")
        df[k] = als_getal.where(df[k].notna())
    for k in datums:
        als_datum = pd.to_datetime(df[k], format="%d-%m-%Y", errors="coerce")
        fout = df[k].notna() & als_datum.isna()
        if fout.any():
            i = fout.idxmax()
            raise ValueError(f"{pad}: regel {i + 2}, kolom {k}: '{df[k][i]}' is geen datum (verwacht zoals 31-12-2025)")
        df[k] = als_datum
    for k in verplicht:
        if df[k].isna().any():
            raise ValueError(f"{pad}: regel {df[k].isna().idxmax() + 2}: kolom {k} is leeg")
    for k in df.select_dtypes(include=["object", "string"]).columns:
        df[k] = df[k].map(
            lambda v: v[1:] if isinstance(v, str) and v[:1] == "'" and v[1:].startswith(FORMULETEKENS) else v
        )
    return df


def lees_uren(pad: str | Path) -> pd.DataFrame:
    """De gefactureerde uren en onkosten, één regel per urenregel.

    `netto` is bedrag plus creditbedrag: een gecorrigeerde urenstaat heeft bedrag 0 en een gecrediteerde factuur
    valt weg, zodat alleen de geldende regel overblijft."""
    getallen = ["uren", "tarief", "bedrag", "creditbedrag", "factuurbedrag"]
    verplicht = ["medewerker", "datum", "uren", "bedrag"]
    df = _lees(pad, UREN[1:], ["datum", "factuurdatum"], getallen, verplicht, dtype=TEKST)
    if df.empty:
        raise ValueError(f"{pad}: er staan geen urenregels in")
    if "rij" not in df.columns:
        df.insert(0, "rij", range(2, len(df) + 2))  # rijnummer zoals in een rekenblad met een kopregel
    df["creditbedrag"] = df.creditbedrag.fillna(0)
    df["netto"] = df.bedrag + df.creditbedrag
    return df


def ontdubbel_bank(bank: pd.DataFrame) -> pd.DataFrame:
    """Haalt boekingen weg die in twee exporten staan. Een regel is alleen dubbel van een eerdere regel als referentie,
    rekening, datum en bedrag alle vier gelijk zijn; de eerste blijft. De referentie komt van de bank maar is deels
    door de betaler bepaald: dezelfde referentie bij een andere rekening, datum of een ander bedrag is een andere
    ontvangst en blijft staan, met een melding. Een regel zonder referentie telt altijd."""
    heeft = bank.referentie != ""
    sleutel = pd.DataFrame(
        {
            "referentie": bank.referentie,
            "rekening": bank.rekening.str.replace(" ", ""),
            "datum": bank.datum,
            "bedrag": bank.bedrag.round(2),
        }
    )
    dubbel = heeft & sleutel.duplicated()
    rest = bank[heeft & ~dubbel]
    for referentie in rest.referentie[rest.referentie.duplicated()].unique():
        log.info("referentie %s staat bij meer dan één boeking; alle blijven staan", referentie)
    return bank[~dubbel]


def _bekende_rekeningen(pad: str | Path, df: pd.DataFrame, inst: Instellingen) -> None:
    """Elk IBAN in kolom `rekening` moet in de instellingen staan; de melding noemt het bestand en de regel."""
    bekend = [r.iban for r in inst.rekeningen]
    for i, iban in df.rekening.items():
        if isinstance(iban, str) and iban.strip() and iban.replace(" ", "") not in bekend:
            raise ValueError(
                f"{pad}: regel {i + 2}, kolom rekening: '{iban}' staat niet in de instellingen "
                f"(bekend: {', '.join(bekend)})"
            )


def lees_bank(pad: str | Path, inst: Instellingen | None = None) -> pd.DataFrame:
    """De ontvangsten op alle rekeningen. `rekening` is het IBAN van de eigen rekening; een lege tegenpartij mag.

    De kolom `referentie` (het referentienummer van de bank) is niet verplicht. Staat hij erin, dan telt een boeking
    die er twee keer in staat (zelfde referentie, rekening, datum en bedrag) één keer: zo mogen exporten die elkaar
    overlappen achter elkaar staan. Zie `ontdubbel_bank`. Regels zonder referentie tellen allemaal. Met `inst` is een
    IBAN dat niet in de instellingen staat een fout, met bestand en regel."""
    tekst = {"rekening": str, "tegenpartij": str, "omschrijving": str, "referentie": str}
    df = _lees(pad, BANK, ["datum"], ["bedrag"], ["datum", "rekening", "bedrag"], dtype=tekst)
    if inst is not None:
        _bekende_rekeningen(pad, df, inst)
    df = df.fillna({"tegenpartij": "", "omschrijving": ""})
    if "referentie" in df.columns:
        df["referentie"] = df.referentie.fillna("").str.strip()
        df = ontdubbel_bank(df).reset_index(drop=True)
    return df


def lees_vervallen(pad: str | Path) -> pd.DataFrame:
    """Orders die de opdrachtgever door een andere heeft vervangen zonder ze te crediteren.

    Kolommen: order; vervangen_door; toelichting."""
    return _lees(pad, ["order", "vervangen_door", "toelichting"], verplicht=["order"], dtype=str).fillna("")


def lees_saldo(pad: str | Path) -> pd.DataFrame:
    """Eigen oordeel per week en soort: week; soort; gefactureerd; order; toewijzing; toelichting."""
    kolommen = ["week", "soort", "gefactureerd", "order", "toewijzing", "toelichting"]
    return _lees(pad, kolommen, getallen=["gefactureerd", "order"], verplicht=["week", "soort"], dtype={"week": str})


def _tekst(kolommen: list[str], getallen_en_datums=()) -> dict:
    """Leest deze kolommen als tekst (een ordernummer of week mag geen getal of datum worden). Kolommen die als getal
    of datum gelezen worden, horen er niet in: een Excel-datum zou dan een tekst als 2024-03-14 00:00:00 worden."""
    return dict.fromkeys([k for k in kolommen if k not in getallen_en_datums], str)


def lees_orders_sjabloon(pad: str | Path, inst: Instellingen) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Orders uit een sjabloon in plaats van uit pdf's: `(kop, regels)` met de kolommen van `orders.KOP` en
    `orders.REGELS`.

    Elke regel van een order herhaalt de kop van de order (entiteit, factuurdatum, totaal exclusief en inclusief btw).
    De entiteit is de code uit de instellingen. De regels moeten optellen tot het totaal exclusief btw: zo ziet de
    gebruiker meteen of een omgebouwde order compleet is. Een order krijgt als `bestand` de naam van het sjabloon
    met het ordernummer erachter."""
    kolommen = ["order", "entiteit", "factuurdatum", "totaal_excl", "totaal_incl"]
    getallen = ["totaal_excl", "totaal_incl", "aantal", "tarief", "bedrag"]
    tekst = _tekst(ORDERS, [*getallen, "factuurdatum", "datum"])
    df = _lees(pad, ORDERS, ["factuurdatum", "datum"], getallen, kolommen, dtype=tekst)
    df = df[ORDERS]
    bekend = {e.code for e in inst.entiteiten}
    onbekend = ~df.entiteit.isin(bekend)
    if onbekend.any():
        i = onbekend.idxmax()
        raise ValueError(
            f"{pad}: regel {i + 2}, kolom entiteit: '{df.entiteit[i]}' staat niet in de instellingen "
            f"(bekend: {', '.join(sorted(bekend))})"
        )
    per_order = df.groupby("order", sort=False)
    for nr, regels_van in per_order:
        # De order is twee keer in het bestand gezet als de regels samen twee keer het ordertotaal zijn én elke unieke
        # regel precies twee keer voorkomt. Twee identieke regels in een gewone order tellen op tot het totaal en zijn
        # geen dubbele order.
        totaal = regels_van.totaal_excl.iloc[0]
        keer = regels_van.groupby(list(regels_van.columns), dropna=False).size()
        if abs(totaal) > 0.005 and abs(regels_van.bedrag.sum() - 2 * totaal) < 0.005 and (keer == 2).all():
            raise ValueError(f"{pad}: order {nr} staat twee keer in het bestand")
    for k in ("entiteit", "factuurdatum", "totaal_excl", "totaal_incl"):
        ongelijk = per_order[k].nunique(dropna=False) > 1
        if ongelijk.any():
            raise ValueError(f"{pad}: order {ongelijk.idxmax()}: {k} is niet op elke regel gelijk")
    kop = per_order[["entiteit", "factuurdatum", "referentie", "totaal_excl", "totaal_incl"]].first().reset_index()
    kop = kop.rename(columns={"totaal_excl": "excl", "totaal_incl": "incl"})
    kop["referentie"] = kop.referentie.fillna("")
    kop["bestand"] = [f"{Path(pad).name} [{nr}]" for nr in kop.order]
    # een regel zonder inhoud hoort bij een order zonder regels; die telt dan voor niets mee
    regelkolommen = ["medewerker", "project", "datum", "omschrijving", "eenheid", "aantal", "tarief", "bedrag"]
    regels = df[df[regelkolommen].notna().any(axis=1)].reset_index(drop=True)
    regels = regels.assign(bestand=regels.order.map(dict(zip(kop.order, kop.bestand, strict=True))))
    som = regels.groupby("order").bedrag.sum().round(2)
    for r in kop.itertuples():
        if abs(som.get(r.order, 0.0) - r.excl) > 0.005:
            a, b = nl_bedrag(som.get(r.order, 0.0)), nl_bedrag(r.excl)
            raise ValueError(f"{pad}: order {r.order}: de regels tellen op tot {a}, het totaal is {b}")
    return kop[orders.KOP], regels[orders.REGELS]


def lees_kaart(pad: str | Path, inst: Instellingen | None = None) -> pd.DataFrame:
    """De debiteurenkaart: wat in de boekhouding nog openstaat. `soort` is `factuur` of `ontvangst`.

    Het bedrag staat zoals op de kaart: een factuur positief, een ontvangst negatief. Bij een factuur is `nummer` het
    factuurnummer; bij een ontvangst is `rekening` het IBAN van de eigen rekening (mag leeg; spaties vallen weg). Met
    `inst` is een IBAN dat niet in de instellingen staat een fout."""
    tekst = _tekst(KAART, ["datum", "bedrag"])
    df = _lees(pad, KAART, ["datum"], ["bedrag"], ["datum", "soort", "bedrag"], dtype=tekst)
    if inst is not None:
        _bekende_rekeningen(pad, df, inst)
    soort = df.soort.str.strip().str.lower()
    fout = ~soort.isin(SOORTEN_KAART)
    if fout.any():
        i = fout.idxmax()
        raise ValueError(f"{pad}: regel {i + 2}, kolom soort: '{df.soort[i]}' is geen factuur of ontvangst")
    df = df.assign(soort=soort, rekening=df.rekening.fillna("").str.replace(" ", ""))
    return df[KAART].fillna({"nummer": "", "omschrijving": ""})


def lees_opmerkingen(pad: str | Path) -> pd.DataFrame:
    """De eigen opmerkingen bij het werkboek, één per regel. `belangrijk` is `ja` of `nee` (leeg telt als `nee`),
    `bedrag` een getal of leeg en `btw` zegt of het bedrag ex of incl btw is. `bevinding` (de sleutel van een
    automatische bevinding waar de opmerking bij hoort) mag ontbreken. Een lege `medewerker` is een opmerking
    voor alle medewerkers."""
    tekst = _tekst(OPMERKINGEN, ["bedrag"])
    df = _lees(pad, OPMERKINGEN[:-1], getallen=["bedrag"], verplicht=["onderwerp"], dtype=tekst)
    if "bevinding" not in df.columns:
        df["bevinding"] = ""
    belangrijk = df.belangrijk.fillna("").str.strip().str.lower().replace("", "nee")
    fout = ~belangrijk.isin(("ja", "nee"))
    if fout.any():
        i = fout.idxmax()
        raise ValueError(f"{pad}: regel {i + 2}, kolom belangrijk: '{df.belangrijk[i]}' is geen ja of nee")
    tekst = [k for k in OPMERKINGEN if k != "bedrag"]
    return df.assign(belangrijk=belangrijk).fillna(dict.fromkeys(tekst, ""))[OPMERKINGEN]


def lees_zonder_order(pad: str | Path) -> pd.DataFrame:
    """Per week zonder order het andere schriftelijke stuk van de opdrachtgever (meestal een mail) en de toelichting."""
    df = _lees(pad, ZONDER_ORDER, verplicht=["medewerker", "week"], dtype=_tekst(ZONDER_ORDER))
    return df[ZONDER_ORDER].fillna("")


MAAND = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
WEEK = re.compile(r"\d{4} wk \d{2}")


def _maand(pad: str | Path, regel: int, waarde) -> str:
    """Een maand als tekst `JJJJ-MM`. Excel maakt van `2024-03` een datum; die wordt weer de maand ervan."""
    if pd.isna(waarde):
        return ""
    if isinstance(waarde, datetime.date):  # ook een Timestamp
        return f"{waarde.year}-{waarde.month:02d}"
    if isinstance(waarde, str) and MAAND.fullmatch(waarde.strip()):
        return waarde.strip()
    raise ValueError(f"{pad}: regel {regel}, kolom maand: '{waarde}' is geen maand (verwacht zoals 2024-03)")


def lees_toezegging(pad: str | Path) -> pd.DataFrame:
    """Een toezegging van de opdrachtgever: een gewone inkooporder, geen order van de opdrachtgever zelf. Hij betaalt
    er pas op na een factuur van het bureau met het nummer erop. Eén regel per post: uren hebben een `week`
    ('2024 wk 14'), reiskosten een `maand` ('2024-03'; in Excel mag dat ook een datum zijn, dan telt de maand ervan).
    `nr` is tekst: een nummer dat Excel als getal bewaart, komt terug als '2001' en niet als '2001.0'. Een voorloopnul
    blijft alleen staan als de cel in Excel tekst is."""
    getallen = ["aantal", "prijs", "bedrag"]
    tekst = _tekst(TOEZEGGING, ["datum", "maand", *getallen])
    df = _lees(pad, TOEZEGGING, ["datum"], getallen, ["medewerker", "nr"], dtype=tekst)
    df["maand"] = [_maand(pad, i + 2, v) for i, v in df.maand.items()]
    for i, week in df.week.items():
        if isinstance(week, str) and week.strip() and not WEEK.fullmatch(week.strip()):
            raise ValueError(f"{pad}: regel {i + 2}, kolom week: '{week}' is geen week (verwacht zoals 2024 wk 14)")
    return df[TOEZEGGING].fillna({k: "" for k in ("week", "omschrijving", "eenheid")})


def lees_urenstaat_afwijkend(pad: str | Path) -> pd.DataFrame:
    """De goedgekeurde urenstaat voor weken waar die niet zomaar uit de uren op te tellen is: medewerker, week,
    bedrag en toelichting. Voor elke andere week is de urenstaat wat gefactureerd is."""
    df = _lees(
        pad,
        URENSTAAT_AFWIJKEND,
        getallen=["bedrag"],
        verplicht=["medewerker", "week", "bedrag"],
        dtype=_tekst(URENSTAAT_AFWIJKEND, ["bedrag"]),
    )
    return df[URENSTAAT_AFWIJKEND].fillna("")


def lees_bank_notities(pad: str | Path) -> pd.DataFrame:
    """Een eigen opmerking bij elke bankboeking waarvan de omschrijving `tekst` bevat. `betreft` noemt de voornaam of
    voornamen van de medewerkers (`Sanne`, `Sanne, Thijmen`), of begint met `onbekend` of `waarschijnlijk`."""
    df = _lees(pad, BANK_NOTITIES, verplicht=["tekst"], dtype=_tekst(BANK_NOTITIES))
    return df[BANK_NOTITIES].fillna("")


ACTIE = re.compile(r"-\s+\*\*(.+?)\*\*\s*(.*)")


def lees_todo(pad: str | Path) -> list[tuple[str, list[tuple[str, str]]]]:
    """De actielijst: `## groep` begint een groep en `- **titel** toelichting` een actie. Regels die er direct onder
    staan en niet met een streepje beginnen, horen bij de toelichting. Een regel met één `#` is een titel van het
    bestand en telt niet. Alles vóór de eerste `## groep` wordt overgeslagen, ook een regel met een streepje: dat is
    een inleiding. Backticks en sterretjes gaan uit de tekst. Geeft `[(groep, [(titel, toelichting), ...])]`."""

    def schoon(tekst: str) -> str:
        return re.sub(r"[`*]", "", tekst).strip()

    groepen: list[tuple[str, list[list]]] = []  # per actie [titel, regels van de toelichting]
    open_actie: list | None = None  # de actie waar een volgende regel bij mag horen
    for n, regel in enumerate(Path(pad).read_text(encoding="utf-8-sig").splitlines(), start=1):
        tekst = regel.strip()
        if not groepen and not regel.startswith("## "):
            continue
        if not tekst:
            open_actie = None
        elif regel.startswith("## "):
            groepen.append((schoon(tekst[3:]), []))
            open_actie = None
        elif regel.startswith("#"):
            open_actie = None
        elif regel.startswith("- "):
            m = ACTIE.fullmatch(tekst)
            if not m:
                raise ValueError(f"{pad}: regel {n}: een actie begint met '- **titel**', dit niet: {tekst}")
            open_actie = [m.group(1), [m.group(2)]]
            groepen[-1][1].append(open_actie)
        elif open_actie is not None:
            open_actie[1].append(tekst)
        else:
            raise ValueError(f"{pad}: regel {n}: deze tekst hoort bij geen actie: {tekst}")
    return [(naam, [(schoon(titel), schoon(" ".join(delen))) for titel, delen in acties]) for naam, acties in groepen]
