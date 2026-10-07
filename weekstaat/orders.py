"""Leest de orders van de opdrachtgever uit pdf's en uit de pdf-bijlagen van mails (.eml).

Een order is een "Factuur Uitgereikt Door Afnemer": de opdrachtgever factureert zelf namens het bureau.
Het resultaat is een kop per order en een regel per gewerkte dag of vergoeding."""

import io
import logging
import re
from collections.abc import Iterator
from email import message_from_binary_file, message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from weekstaat.instellingen import Instellingen

log = logging.getLogger(__name__)

# De opmaak van de orders. Alle vaste teksten en patronen waar het leesscript op zoekt staan hier bij elkaar.
TITEL = "Factuur Uitgereikt Door Afnemer|Creditfactuur"  # een creditorder heeft negatieve bedragen
ORDERNUMMER = re.compile(r"\b(I0\d{7})\b")
EXCL = re.compile(r"Dit bedrag is exclusief btw\s+(-?[\d.,]+)")
INCL = re.compile(r"Te (?:betalen|ontvangen) in\s*EUR:\s+(-?[\d.,]+)")
FACTUURDATUM = re.compile(r"I0\d{7}\s+-?[\d.,]+\s+(\d\d-\d\d-\d{4})")
REFERENTIE = re.compile(r"week\s+\d+(?:\s*-\s*\d+)?", re.I)
PROJECT = re.compile(r"\s*Project\s*(.*)")
MEDEWERKER = re.compile(r"\s*Naam medewerker:\s*(.*)")
BEDRAG = re.compile(r"-?[\d.]+,\d\d")
DATUM = re.compile(r"\d\d-\d\d-\d{4}")
EENHEDEN = ("Uren", "Kilometers")

KOP = ["bestand", "order", "entiteit", "factuurdatum", "excl", "incl", "referentie"]
REGELS = [
    "bestand", "order", "entiteit", "medewerker", "project", "datum",
    "omschrijving", "eenheid", "aantal", "tarief", "bedrag",
]  # fmt: skip


def _getal(tekst: str | None) -> float | None:
    """Nederlandse notatie (1.234,56) naar een getal."""
    return float(tekst.replace(".", "").replace(",", ".")) if tekst else None


def _paginas(inhoud: bytes) -> list[str]:
    """De tekst van een pdf, per pagina, met de plaatsing van de regels behouden."""
    paginas = PdfReader(io.BytesIO(inhoud)).pages
    return [p.extract_text(extraction_mode="layout") if "/Contents" in p else "" for p in paginas]


def _bijlagen(mail: EmailMessage, bron: str) -> Iterator[tuple[str, bytes]]:
    """De pdf-bijlagen van een mail, ook uit doorgestuurde mails erin: (naam, inhoud)."""
    for deel in mail.iter_attachments():
        naam = deel.get_filename() or ""
        if deel.get_content_type() == "message/rfc822":
            yield from _bijlagen(deel.get_content(), bron)
        elif naam.lower().endswith(".eml"):
            yield from _bijlagen(message_from_bytes(deel.get_payload(decode=True), policy=policy.default), bron)
        elif naam.lower().endswith(".pdf"):
            yield f"{naam[:-4]} (bijlage in {bron}).pdf".replace("/", "_"), deel.get_payload(decode=True)


def _pdfs(mappen: list[str | Path]) -> Iterator[tuple[str, bytes]]:
    """Alle pdf's uit de mappen, daarna de bijlagen uit de mails: (naam, inhoud)."""
    for pad in sorted(p for m in mappen for p in Path(m).glob("*.pdf")):
        yield pad.name, pad.read_bytes()
    bijlagen = []
    for pad in sorted(p for m in mappen for p in Path(m).glob("*.eml")):
        with open(pad, "rb") as f:
            bijlagen += _bijlagen(message_from_binary_file(f, policy=policy.default), pad.name)
    yield from sorted(bijlagen, key=lambda b: b[0])


def _stukken(paginas: list[str]) -> list[tuple[str, str]]:
    """Splitst de pagina's van een pdf in losse orders: (ordernummer, tekst). Meestal is dat er één.
    Een pdf kan ook de eigen factuur van het bureau bevatten (geen order, die valt weg) met daarachter één of
    meer orders, soms dubbel. Een pagina zonder titel en zonder ordernummer hoort bij de order ervoor."""
    uit: list[list[str]] = []
    for pagina in paginas:
        nr = ORDERNUMMER.search(pagina)
        if nr and re.search(TITEL, pagina):
            if uit and uit[-1][0] == nr.group(1) and "Dit bedrag is exclusief btw" not in uit[-1][1]:
                uit[-1][1] += "\n" + pagina  # vervolgpagina; na de pagina met het totaal begint een nieuw exemplaar
            else:
                uit.append([nr.group(1), pagina])
        elif uit and not nr:
            uit[-1][1] += "\n" + pagina
    eerste: dict[str, str] = {}
    for nr, tekst in uit:
        eerste.setdefault(nr, tekst)  # dezelfde order twee keer in één pdf: de eerste telt
    return list(eerste.items())


def _ontleed(lijn: str) -> tuple | None:
    """Eén orderregel: datum, omschrijving, eenheid, aantal, tarief, bedrag. Alleen de omschrijving en het aantal
    zijn verplicht. De regel wordt van achteren af gelezen, woord voor woord: hooguit drie getallen, daarvoor
    eventueel de eenheid, vooraan eventueel de datum, en wat overblijft is de omschrijving."""
    woorden = lijn.split()
    getallen: list[float | None] = []
    while woorden and len(getallen) < 3 and BEDRAG.fullmatch(woorden[-1]):
        getallen.insert(0, _getal(woorden.pop()))
    eenheid = woorden.pop() if woorden and woorden[-1] in EENHEDEN else None
    datum = woorden.pop(0) if woorden and DATUM.fullmatch(woorden[0]) else None
    if not getallen or not woorden:
        return None
    return datum, " ".join(woorden), eenheid, *(getallen + [None, None])[:3]


def _regels(tekst: str) -> Iterator[dict]:
    medewerker = project = None
    for lijn in tekst.splitlines():
        if m := PROJECT.match(lijn):
            project = m.group(1).strip() or None
        if m := MEDEWERKER.match(lijn):
            medewerker = m.group(1).strip() or None
        r = _ontleed(lijn)
        # een orderregel heeft een datum; een correctieregel heeft er geen maar noemt zichzelf zo
        if r and (r[0] or "correctie" in lijn) and not lijn.strip().startswith("Totaal"):
            velden = ("datum", "omschrijving", "eenheid", "aantal", "tarief", "bedrag")
            yield dict(medewerker=medewerker, project=project, **dict(zip(velden, r, strict=True)))


def lees_orders(mappen: list[str | Path], inst: Instellingen) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Leest de pdf's en de mails (.eml) in de mappen. Geeft `(kop, regels)`: een kop per order en een regel per
    dag of vergoeding.

    Hetzelfde bestand in twee mappen telt één keer; twee verschillende bestanden met dezelfde naam zijn een fout.
    Een pdf waar meer orders in zitten, krijgt het ordernummer achter de bestandsnaam.
    Een pdf zonder order wordt overgeslagen.
    Tellen de regels van een order niet op tot het totaal exclusief btw, dan is dat een fout."""
    koppen, regels, gezien = [], [], {}
    for naam, inhoud in _pdfs(mappen):
        if naam in gezien:
            if gezien[naam] != inhoud:
                raise ValueError(f"twee verschillende bestanden heten {naam}")
            log.info("dubbel (zelfde bestand in twee mappen): %s", naam)
            continue
        gezien[naam] = inhoud
        try:
            delen = _stukken(_paginas(inhoud))
        except PyPdfError as fout:
            raise ValueError(f"{naam}: geen leesbare pdf ({fout})") from None
        if not delen:
            log.info("overgeslagen (geen order): %s", naam)
        for nr, tekst in delen:
            bestand = naam if len(delen) == 1 else f"{naam} [{nr}]"
            entiteit = inst.entiteit(re.split(TITEL, tekst)[0])
            excl, incl = EXCL.search(tekst), INCL.search(tekst)
            datum, ref = FACTUURDATUM.search(tekst), REFERENTIE.search(tekst)
            koppen.append(dict(
                bestand=bestand, order=nr, entiteit=entiteit, factuurdatum=datum.group(1) if datum else None,
                excl=_getal(excl.group(1)) if excl else None, incl=_getal(incl.group(1)) if incl else None,
                referentie=ref.group(0) if ref else "",
            ))  # fmt: skip
            regels += [dict(bestand=bestand, order=nr, entiteit=entiteit, **r) for r in _regels(tekst)]
    kop, regels = pd.DataFrame(koppen, columns=KOP), pd.DataFrame(regels, columns=REGELS)
    kop["factuurdatum"] = pd.to_datetime(kop.factuurdatum, format="%d-%m-%Y")
    regels["datum"] = pd.to_datetime(regels.datum, format="%d-%m-%Y")
    som = kop.bestand.map(regels.groupby("bestand").bedrag.sum().round(2)).fillna(0)
    fout = kop.assign(som=som)[(som - kop.excl).abs().fillna(1) > 0.005]
    if not fout.empty:
        regel = "{bestand}: regels tellen op tot {som:.2f}, totaal exclusief btw is {excl}"
        raise ValueError("regels tellen niet op tot het totaal van de order:\n" + "\n".join(
            regel.format(bestand=r.bestand, som=r.som, excl="niet gevonden" if pd.isna(r.excl) else f"{r.excl:.2f}")
            for r in fout.itertuples()
        ))  # fmt: skip
    ontbreekt = {"factuurdatum": "de factuurdatum", "incl": "het bedrag incl. btw (Te betalen in EUR)"}
    for kolom, wat in ontbreekt.items():
        zonder = kop[kop[kolom].isna()]
        if not zonder.empty:
            lijst = ", ".join(f"{r.bestand} (order {r.order})" for r in zonder.itertuples())
            raise ValueError(
                f"{wat} is op de order niet gevonden: {lijst}. Zonder dat telt de order niet mee in de betaling; "
                "zet de order in een sjabloon (orders.csv) als de pdf een andere opmaak heeft"
            )
    return kop, regels
