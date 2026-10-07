"""De opdrachtregel: `weekstaat aansluiten <map>` maakt per medewerker een overzicht (een werkboek), een
totaalbestand voor alle medewerkers samen en `bevindingen.csv`. `weekstaat omzetten-uren`, `omzetten-bank` en
`omzetten-kaart` bouwen een export om naar het sjabloon."""

import argparse
import logging
import sys
import zipfile
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException

from . import aansluiten, bevindingen, dossier, instellingen, omzetten, overzicht, totaal, werkboek

log = logging.getLogger("weekstaat")


def _tabel(samenvatting: pd.DataFrame) -> str:
    """De samenvatting per status met een totaalregel, bedragen in Nederlandse notatie."""
    s = samenvatting.rename_axis(None)
    s.loc["Totaal"] = s.sum()
    bedrag = dict.fromkeys(["factuurkant", "orderkant", "verschil"], aansluiten.nl_bedrag)
    return s.astype({"dagen": int}).to_string(formatters=bedrag)


def _toon(tekst: str) -> str:
    """Tekst van buiten voor de terminal, op één regel. Namen van bestanden en medewerkers kunnen een stuurteken
    bevatten (een escape-reeks die de terminal bestuurt) of een regeleinde dat een eigen regel in de uitvoer lijkt;
    elk teken dat niet af te drukken is, wordt een vraagteken."""
    return "".join(t if t.isprintable() else "?" for t in tekst)


class _Melding(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _toon(super().format(record))


_in_naam = dossier.in_naam  # de naam van een medewerker in een bestandsnaam


@contextmanager
def _schrijven(pad: Path):
    """Een fout bij het wegschrijven van `pad` (het bestand staat open in Excel op Windows, de map is alleen-lezen, het
    pad is een map) wordt een melding met het pad in plaats van de tekst van het besturingssysteem."""
    try:
        yield
    except OSError:
        raise ValueError(f"kan {pad} niet schrijven: staat het bestand open in Excel?") from None


def _oserror(fout: OSError) -> str:
    """De melding bij een bestand dat niet te lezen is, in gewone taal en met de naam van het bestand."""
    naam = fout.filename or "bestand"
    if isinstance(fout, FileNotFoundError):
        return f"{naam}: niet gevonden"
    if isinstance(fout, PermissionError):
        return f"{naam}: geen toegang"
    if isinstance(fout, IsADirectoryError):
        return f"{naam}: is een map, geen bestand"
    return f"{naam}: {fout.strerror or fout}"


@contextmanager
def _excel(pad: str, verwacht: str):
    """Rond het lezen van een Excel-bestand (.xlsx). Is het bestand geen werkboek (een csv, een ander formaat), dan
    meldt dat de bestandsnaam en wat er verwacht wordt, niet de tekst van pandas."""
    bestand = Path(pad)
    melding = f"{pad}: geen Excel-bestand (.xlsx) dat te lezen is. Verwacht {verwacht} als .xlsx"
    if bestand.is_file() and not zipfile.is_zipfile(bestand):
        raise ValueError(melding)
    try:
        yield
    except (zipfile.BadZipFile, InvalidFileException, KeyError):  # een zip die geen werkboek is
        raise ValueError(melding) from None


def _csv(pad: str, verwacht: str) -> None:
    """Een export die een csv moet zijn, maar een Excel-bestand blijkt: meld welk bestand."""
    bestand = Path(pad)
    if bestand.is_file() and zipfile.is_zipfile(bestand):
        raise ValueError(f"{pad}: dit is een Excel-bestand. Verwacht {verwacht} als .csv")


def _aansluiten(args: argparse.Namespace) -> None:
    map_, uit = Path(args.map), Path(args.uit)
    d = dossier.lees(map_)
    if uit.exists() and not uit.is_dir():
        raise ValueError(f"{uit} is een bestand; --uit moet een map zijn")
    namen = args.medewerker or sorted(d.uren.medewerker.unique())
    onbekend = set(namen) - set(d.uren.medewerker)
    if onbekend:
        raise ValueError(f"geen uren gevonden voor {', '.join(sorted(onbekend))}")
    uit.mkdir(parents=True, exist_ok=True)
    print(
        f"{len(d.kop.order.unique())} orders, {len(d.uren)} urenregels"
        + (f", {len(d.bank)} bankregels" if d.bank is not None else "")
    )
    # eerst alle overzichten (een bestaand overzicht wordt gelezen voor de keuze van de gebruiker, daarna overschreven)
    stukken = []
    for naam in namen:
        a = aansluiten.sluit_aan(
            d.uren, d.kop, d.regels, naam, d.inst, bankregels=d.bank, vervallen=d.vervallen, saldo=d.saldo(naam)
        )
        pad = uit / f"Overzicht {_in_naam(naam)}.xlsx"
        stukken.append((a, pad, overzicht.maak(a, d, werkboek.eigen_keuze(pad))))
    overzichten = [o for _, _, o in stukken]
    gevonden = bevindingen.leid_af(overzichten, d)
    opmerkingen = bevindingen.opmerkingen(gevonden, d)
    korte = totaal.korte_namen(namen)
    for a, pad, o in stukken:  # "Algemeen" en opmerkingen voor iedereen horen alleen in het totaalbestand
        with _schrijven(pad):
            werkboek.schrijf(o, opmerkingen[opmerkingen.medewerker == o.medewerker].reset_index(drop=True), pad)
        print(f"\n{_toon(o.medewerker)}  ->  {_toon(str(pad))}")
        print(_tabel(a.samenvatting()))
    pad_totaal = uit / "Overzicht totaal.xlsx"
    with _schrijven(pad_totaal):
        totaal.schrijf(overzichten, opmerkingen, bevindingen.acties(gevonden, d, korte), pad_totaal)
    with _schrijven(uit / "bevindingen.csv"):
        bevindingen.schrijf_csv(gevonden, uit / "bevindingen.csv")
    print(f"\nTotaal van alle medewerkers  ->  {_toon(str(pad_totaal))}")
    print(f"{len(gevonden)} bevindingen  ->  {_toon(str(uit / 'bevindingen.csv'))}")


def _omzetten_uren(args: argparse.Namespace) -> None:
    with _excel(args.export, "het urenrapport uit Salesforce"):
        uren = omzetten.uren_uit_salesforce(args.export)
    with _schrijven(Path(args.naar)):
        omzetten.schrijf_sjabloon(uren, args.naar)
    print(f"geschreven: {args.naar}")


def _omzetten_bank(args: argparse.Namespace) -> None:
    inst = instellingen.lees(args.instellingen)
    for export in args.export:
        _csv(export, "een export van de Rabobank")
    kaart = args.debiteurenkaart
    with _excel(kaart, "de debiteurenkaart uit de boekhouding") if kaart else nullcontext():
        bank = omzetten.bank_uit_rabobank(args.export, inst, kaart)
    with _schrijven(Path(args.naar)):
        omzetten.schrijf_sjabloon(bank, args.naar)
    print(f"geschreven: {args.naar}")


def _omzetten_kaart(args: argparse.Namespace) -> None:
    with _excel(args.export, "de debiteurenkaart uit de boekhouding"):
        kaart = omzetten.kaart_uit_boekhouding(args.export)
    with _schrijven(Path(args.naar)):
        omzetten.schrijf_sjabloon(kaart, args.naar)
    print(f"geschreven: {args.naar}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="weekstaat", description=__doc__)
    sub = p.add_subparsers(required=True, metavar="opdracht")

    a = sub.add_parser(
        "aansluiten", help="leg uren, orders en bank naast elkaar en schrijf per medewerker een overzicht"
    )
    a.add_argument(
        "map", help="map met instellingen.toml, uren.csv, orders/ en eventueel mail/, bank.csv en hulpbestanden"
    )
    a.add_argument("--uit", default="uitvoer", help="map voor de overzichten (standaard: uitvoer)")
    a.add_argument("--medewerker", action="append", help="alleen deze medewerker; mag vaker")
    a.set_defaults(doe=_aansluiten)

    u = sub.add_parser("omzetten-uren", help="bouw een urenrapport uit Salesforce om naar het sjabloon uren.csv")
    u.add_argument("export", help="het rapport als .xlsx")
    u.add_argument("naar", help="het csv-bestand dat geschreven wordt")
    u.set_defaults(doe=_omzetten_uren)

    b = sub.add_parser("omzetten-bank", help="bouw bankexporten van de Rabobank om naar het sjabloon bank.csv")
    b.add_argument("naar", help="het csv-bestand dat geschreven wordt")
    b.add_argument("export", nargs="+", help="een of meer exporten als .csv, één per rekening")
    b.add_argument("--instellingen", required=True, help="instellingen.toml met de rekeningen")
    b.add_argument(
        "--debiteurenkaart", help="debiteurenkaart als .xlsx, voor ontvangsten die in de bankexport ontbreken"
    )
    b.set_defaults(doe=_omzetten_bank)

    k = sub.add_parser("omzetten-kaart", help="bouw de debiteurenkaart uit de boekhouding om naar het sjabloon")
    k.add_argument("export", help="de kaart als .xlsx")
    k.add_argument("naar", help="het csv-bestand dat geschreven wordt")
    k.set_defaults(doe=_omzetten_kaart)

    args = p.parse_args(argv)
    # overgeslagen en dubbele pdf's meldt het leesscript via logging; laat ze zien, want een overgeslagen pdf kan
    # een order in een andere opmaak zijn
    meld = logging.StreamHandler(sys.stderr)
    meld.setFormatter(_Melding("let op: %(message)s"))
    log.addHandler(meld)
    log.setLevel(logging.INFO)
    try:
        args.doe(args)
    except (ValueError, OSError) as fout:
        # een melding van meer regels blijft als één blok herkenbaar: elke vervolgregel springt in
        melding = _oserror(fout) if isinstance(fout, OSError) else str(fout)
        print("weekstaat: " + "\n  ".join(_toon(r) for r in melding.split("\n")), file=sys.stderr)
        return 1
    finally:
        log.removeHandler(meld)
    return 0


if __name__ == "__main__":
    sys.exit(main())
