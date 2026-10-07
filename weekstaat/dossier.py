"""Het dossier: alle bronnen van één opdrachtgever, ingelezen uit een map.

Een map heeft verplicht `instellingen.toml`, de uren (`uren.csv` of `uren.xlsx`) en orders: pdf's in `orders/` en
`mail/`, of een sjabloon `orders.csv` (of `.xlsx`), of allebei. Al het andere is niet verplicht en is `None` als het
ontbreekt of leeg is; alleen de debiteurenkaart is een leeg dataframe als het bestand er is zonder regels (alles
afgeletterd). Elk sjabloon mag een csv of een Excel-werkboek zijn; de kolommen staan in `sjablonen/`."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import inlezen, instellingen, omzetten, orders
from .instellingen import Instellingen

log = logging.getLogger(__name__)

# Bestandsnaam (zonder extensie) per bron, en de lezer die erbij hoort. `bank` en de orders hebben een eigen lezer.
HULPBESTANDEN = {
    "vervallen": ("vervallen-orders", inlezen.lees_vervallen),
    "opmerkingen": ("opmerkingen", inlezen.lees_opmerkingen),
    "zonder_order": ("zonder-order", inlezen.lees_zonder_order),
    "toezegging": ("toezegging", inlezen.lees_toezegging),
    "urenstaat_afwijkend": ("urenstaat-afwijkend", inlezen.lees_urenstaat_afwijkend),
    "bank_notities": ("bank-notities", inlezen.lees_bank_notities),
}
KAART = "debiteurenkaart"  # de kaart heeft een eigen lezer in `lees`: zij heeft de instellingen nodig
BESTANDSNAMEN = {
    "uren": "uren", "bank": "bank", "kaart": KAART, **{naam: stam for naam, (stam, _) in HULPBESTANDEN.items()}
}  # fmt: skip
# De kolommen waarmee `schrijf` een bron wegschrijft; het bestand heeft dan precies de kolommen van het sjabloon.
KOLOMMEN = {
    "uren": inlezen.UREN,
    "bank": [*inlezen.BANK, "referentie"],
    "kaart": inlezen.KAART,
    "vervallen": ["order", "vervangen_door", "toelichting"],
    "opmerkingen": inlezen.OPMERKINGEN,
    "zonder_order": inlezen.ZONDER_ORDER,
    "toezegging": inlezen.TOEZEGGING,
    "urenstaat_afwijkend": inlezen.URENSTAAT_AFWIJKEND,
    "bank_notities": inlezen.BANK_NOTITIES,
}


def in_naam(medewerker: str) -> str:
    """De naam van een medewerker als deel van een bestandsnaam. De naam komt uit een export; een schuine streep
    erin mag geen pad buiten de map worden."""
    return re.sub(r"[\\/:\x00]", "_", medewerker)


@dataclass
class Dossier:
    """Alles wat de aansluiting nodig heeft. `kop` en `regels` zijn de orders uit pdf's en sjabloon samen (een order
    die in beide staat, komt één keer voor en de pdf gaat voor). De overige bronnen zijn `None` als ze ontbreken;
    `kaart` is een leeg dataframe als de kaart er is zonder regels."""

    map: Path
    inst: Instellingen
    uren: pd.DataFrame
    kop: pd.DataFrame
    regels: pd.DataFrame
    bank: pd.DataFrame | None
    kaart: pd.DataFrame | None
    vervallen: pd.DataFrame | None
    opmerkingen: pd.DataFrame | None
    zonder_order: pd.DataFrame | None
    toezegging: pd.DataFrame | None
    urenstaat_afwijkend: pd.DataFrame | None
    bank_notities: pd.DataFrame | None
    todo: list | None

    def saldo(self, medewerker: str) -> pd.DataFrame | None:
        """Het eigen oordeel van de gebruiker voor een medewerker (`saldo <naam>.csv`), of `None`."""
        pad = inlezen.vind(self.map, f"saldo {in_naam(medewerker)}")
        return inlezen.lees_saldo(pad) if pad else None


def _orders(map_: Path, inst: Instellingen) -> tuple[pd.DataFrame, pd.DataFrame]:
    """De orders uit de pdf's en mails en uit het sjabloon. Een ordernummer dat uit allebei komt, telt één keer."""
    mappen = [m for m in (map_ / "orders", map_ / "mail") if m.is_dir()]
    kop, regels = orders.lees_orders(mappen, inst)
    if sjabloon := inlezen.vind(map_, "orders"):
        kop_s, regels_s = inlezen.lees_orders_sjabloon(sjabloon, inst)
        dubbel = kop_s.order.isin(kop.order)
        if dubbel.any():
            log.info("order in pdf en in %s, de pdf gaat voor: %s", sjabloon.name, ", ".join(kop_s.order[dubbel]))
        kop_s = kop_s[~dubbel]
        kop = pd.concat([kop, kop_s], ignore_index=True)
        regels = pd.concat([regels, regels_s[regels_s.bestand.isin(kop_s.bestand)]], ignore_index=True)
        # Een lege kant (geen pdf's) maakt van de samengevoegde kolommen het type object: bedragen als tekst.
        kop = kop.astype({"excl": float, "incl": float})
        regels = regels.astype({"aantal": float, "tarief": float, "bedrag": float})
    if kop.empty:
        # zonder orders zou elke dag "geen order" krijgen; dat is bijna altijd een verkeerde map of een andere opmaak
        raise ValueError(f"geen orders gevonden in {map_ / 'orders'} en {map_ / 'mail'}: staan de pdf's daar?")
    return kop, regels


# De hulpbestanden met een kolom `medewerker`, en de namen die erin mogen staan zonder dat ze in de uren voorkomen.
MET_MEDEWERKER = {
    "opmerkingen": {"", "Algemeen"},
    "zonder_order": {""},
    "toezegging": {""},
    "urenstaat_afwijkend": {""},
}


def _waarschuw_voor_onbekende_namen(map_: Path, namen: set[str], bronnen: dict) -> None:
    """Een naam in een hulpbestand die niet in de uren voorkomt, is bijna altijd een typfout: de regel hoort dan bij
    niemand en valt weg. Eén waarschuwing per bestand en naam; het dossier wordt wel gelezen. Hetzelfde voor de naam
    in de bestandsnaam van `saldo <naam>.csv`."""
    for bron, toegestaan in MET_MEDEWERKER.items():
        tabel = bronnen.get(bron)
        if tabel is None:
            continue
        pad = inlezen.vind(map_, HULPBESTANDEN[bron][0])
        for naam in dict.fromkeys(tabel.medewerker.fillna("")):
            if naam not in namen and naam not in toegestaan:
                log.warning("%s: '%s' komt niet voor in de uren", pad.name, naam)
    bestandsnamen = {in_naam(n): n for n in namen}
    for pad in sorted(p for p in map_.glob("saldo *") if p.suffix.lower() in (".csv", ".xlsx")):
        naam = pad.stem[len("saldo ") :]
        if naam not in bestandsnamen:
            log.warning("%s: '%s' komt niet voor in de uren", pad.name, naam)


def lees(map_: str | Path) -> Dossier:
    """Leest alle bronnen uit de map. Een fout in de invoer is een `ValueError` met bestand, regel en kolom."""
    map_ = Path(map_)
    nodig = "De map heeft instellingen.toml, uren.csv en orders/ nodig."
    if not (map_ / "instellingen.toml").is_file():
        raise ValueError(f"{map_ / 'instellingen.toml'} ontbreekt. {nodig}")
    if not inlezen.vind(map_, "uren"):
        raise ValueError(f"{map_ / 'uren.csv'} ontbreekt. {nodig}")
    inst = instellingen.lees(map_ / "instellingen.toml")
    uren = inlezen.lees_uren(inlezen.vind(map_, "uren"))
    kop, regels = _orders(map_, inst)
    bronnen = {}
    for naam, (stam, lees_bron) in HULPBESTANDEN.items():
        pad = inlezen.vind(map_, stam)
        df = lees_bron(pad) if pad else None
        bronnen[naam] = None if df is None or df.empty else df
    # Een kaart die er is zonder regels is een boekhouding die helemaal bij is: dat is een lege kaart, geen ontbrekende.
    # Alleen bij de kaart zegt "niets openstaan" iets; bij de andere bronnen is leeg gelijk aan geen.
    pad = inlezen.vind(map_, KAART)
    bronnen["kaart"] = inlezen.lees_kaart(pad, inst) if pad else None
    pad = inlezen.vind(map_, "bank")
    bank = inlezen.lees_bank(pad, inst) if pad else None
    bank = None if bank is None or bank.empty else bank
    # een onbekende rekening is een fout in de invoer, geen regel om stil te laten vallen
    for tabel in (bank, bronnen["kaart"]):
        for iban in set() if tabel is None else set(tabel.rekening) - {""}:
            inst.rekening(iban)
    todo = inlezen.lees_todo(map_ / "to-do.md") if (map_ / "to-do.md").is_file() else None
    _waarschuw_voor_onbekende_namen(map_, set(uren.medewerker.dropna()), bronnen)
    return Dossier(map_, inst, uren, kop, regels, bank, todo=todo or None, **bronnen)


def schrijf(map_: Path, **bronnen) -> None:
    """Schrijft bronnen als sjabloonbestanden in de map, voor tests. De naam van elke bron is die van een veld van
    `Dossier`, met drie uitzonderingen: `instellingen` is de tekst van `instellingen.toml`; `kop` en `regels` horen
    bij elkaar en worden samen `orders.csv` (een order die er meer dan één keer in staat, komt er één keer in); en
    `saldo` is een dict van medewerker naar tabel. `todo` is de lijst die `inlezen.lees_todo` teruggeeft."""
    map_ = Path(map_)
    onbekend = set(bronnen) - {*KOLOMMEN, "instellingen", "kop", "regels", "todo", "saldo"}
    if onbekend:
        raise ValueError(f"onbekende bron: {', '.join(sorted(onbekend))}")
    if ("kop" in bronnen) != ("regels" in bronnen):
        raise ValueError("kop en regels horen bij elkaar: geef allebei of geen van beide")
    map_.mkdir(parents=True, exist_ok=True)
    if "instellingen" in bronnen:
        (map_ / "instellingen.toml").write_text(bronnen["instellingen"], encoding="utf-8")
    for naam, df in bronnen.items():
        if naam in KOLOMMEN:
            omzetten.schrijf_sjabloon(
                df[[k for k in KOLOMMEN[naam] if k in df.columns]], map_ / f"{BESTANDSNAMEN[naam]}.csv"
            )
    if "kop" in bronnen:
        omzetten.schrijf_sjabloon(_orders_sjabloon(bronnen["kop"], bronnen["regels"]), map_ / "orders.csv")
    for medewerker, df in bronnen.get("saldo", {}).items():
        omzetten.schrijf_sjabloon(df, map_ / f"saldo {in_naam(medewerker)}.csv")
    if "todo" in bronnen:
        tekst = "".join(
            f"## {groep}\n\n"
            + "".join(f"- **{titel}** {toelichting}".rstrip() + "\n" for titel, toelichting in acties)
            + "\n"
            for groep, acties in bronnen["todo"]
        )
        (map_ / "to-do.md").write_text(tekst, encoding="utf-8")


def _orders_sjabloon(kop: pd.DataFrame, regels: pd.DataFrame) -> pd.DataFrame:
    """De kop en de regels van de orders als één tabel met de kolommen van `inlezen.ORDERS`."""
    kop = kop.drop_duplicates("order").rename(columns={"excl": "totaal_excl", "incl": "totaal_incl"})
    tabel = kop[["bestand", "order", "entiteit", "factuurdatum", "referentie", "totaal_excl", "totaal_incl"]].merge(
        regels.drop(columns=["order", "entiteit"]), on="bestand", how="left"
    )
    return tabel[inlezen.ORDERS]
