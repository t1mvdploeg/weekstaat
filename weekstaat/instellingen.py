"""Instellingen per opdrachtgever: zijn entiteiten, de rekeningen waarover hij een betaling verdeelt, en de
vaste waarden waarmee de tool rekent (btw, blokgrootte, betaaltermijn) en schrijft (de namen in de teksten)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ONBEKEND = "?"
BLOKKEN = ("week", "4 weken", "maand")


@dataclass(frozen=True)
class Entiteit:
    code: str
    naam: str


@dataclass(frozen=True)
class Rekening:
    naam: str
    iban: str
    aandeel: float


@dataclass(frozen=True)
class Instellingen:
    entiteiten: tuple[Entiteit, ...]
    rekeningen: tuple[Rekening, ...]
    bureau: str = "het bureau"  # naam in de teksten
    opdrachtgever: str = "de opdrachtgever"
    btw: float = 0.21
    blok: str = "4 weken"  # waarin de opdrachtgever zijn orders plaatst: "week", "4 weken" of "maand"
    betaaltermijn: int = 30  # dagen na de factuurdatum van een order voordat "niet ontvangen" geldt

    def week(self, datum: pd.Timestamp) -> str:
        """De week van een datum als tekst, met het ISO-jaar: '2024 wk 08'. Tekst, want een rekenblad leest
        '2024-08' als getal of datum."""
        iso = datum.isocalendar()
        return f"{iso.year} wk {iso.week:02d}"

    def periode(self, datum: pd.Timestamp) -> str:
        """Het blok waarin de datum valt: '2024 wk 05-08' (4 weken, te beginnen bij week 1), '2024 wk 08' (week)
        of '2024-03' (maand)."""
        if self.blok == "week":
            return self.week(datum)
        if self.blok == "maand":
            return f"{datum.year}-{datum.month:02d}"
        iso = datum.isocalendar()
        eerste = (iso.week - 1) // 4 * 4 + 1
        return f"{iso.year} wk {eerste:02d}-{eerste + 3:02d}"

    def entiteit(self, tekst: str) -> str:
        """Code van de entiteit waarvan de naam in de tekst staat. Passen er meer, dan wint de langste naam."""
        raak = [e for e in self.entiteiten if e.naam.lower() in tekst.lower()]
        return max(raak, key=lambda e: len(e.naam)).code if raak else ONBEKEND

    def rekening(self, iban: str) -> Rekening:
        """De rekening bij een IBAN; spaties in het IBAN tellen niet mee."""
        for r in self.rekeningen:
            if r.iban == iban.replace(" ", ""):
                return r
        raise ValueError(f"rekening {iban} staat niet in de instellingen")


def _iban(waarde):
    """Een IBAN zoals de bank het schrijft: zonder spaties en in hoofdletters ("NL00 rabo 0123 4567 89" wordt
    "NL00RABO0123456789"). Iets anders dan tekst blijft staan; `lees` meldt dat."""
    return waarde.replace(" ", "").upper() if isinstance(waarde, str) else waarde


def lees(pad: str | Path) -> Instellingen:
    with open(pad, "rb") as f:
        try:
            ruw = tomllib.load(f)
        except tomllib.TOMLDecodeError as fout:
            raise ValueError(f"{pad}: geen geldig TOML ({fout})") from None
    vast = ("bureau", "opdrachtgever", "btw", "blok", "betaaltermijn")
    onbekend = sorted(set(ruw) - {*vast, "entiteit", "rekening"})
    if onbekend:
        bestaand = ", ".join(sorted([*vast, "entiteit", "rekening"]))
        raise ValueError(f"{pad}: onbekende instelling {', '.join(onbekend)}. Dit staat erin: {bestaand}")
    algemeen = {k: ruw[k] for k in vast if k in ruw}
    try:
        inst = Instellingen(
            entiteiten=tuple(Entiteit(**e) for e in ruw.get("entiteit", [])),
            rekeningen=tuple(
                Rekening(**({**r, "iban": _iban(r["iban"])} if "iban" in r else r)) for r in ruw.get("rekening", [])
            ),
            **algemeen,
        )
    except TypeError:
        uitleg = "een [[entiteit]] heeft code en naam, een [[rekening]] heeft naam, iban en aandeel"
        raise ValueError(f"{pad}: {uitleg}") from None
    if not inst.entiteiten or not inst.rekeningen:
        raise ValueError(f"{pad}: er moet minstens één [[entiteit]] en één [[rekening]] in staan")
    # Eén entiteit mag onder meer dan één naam voorkomen (de bank schrijft een afkorting): dezelfde code twee keer is
    # goed. Dezelfde naam of hetzelfde IBAN twee keer is dat niet: de tool kan dan niet kiezen.
    for soort, waarden in (
        ("naam", [e.naam.lower() for e in inst.entiteiten if isinstance(e.naam, str)]),
        ("iban", [r.iban for r in inst.rekeningen]),
    ):
        dubbel = sorted({w for w in waarden if waarden.count(w) > 1})
        if dubbel:
            raise ValueError(f"{pad}: de {soort} {', '.join(dubbel)} staat meer dan één keer in de instellingen")
    for e in inst.entiteiten:
        if not isinstance(e.code, str) or not e.code.strip() or not isinstance(e.naam, str) or not e.naam.strip():
            raise ValueError(f"{pad}: een [[entiteit]] heeft een code en een naam, en geen van beide is leeg")
    if not all(isinstance(r.aandeel, int | float) for r in inst.rekeningen):
        raise ValueError(f"{pad}: het aandeel van een rekening is een getal, bijvoorbeeld 0.30")
    if any(r.aandeel < 0 for r in inst.rekeningen):
        raise ValueError(f"{pad}: het aandeel van een rekening is niet negatief, bijvoorbeeld 0.30")
    if abs(sum(r.aandeel for r in inst.rekeningen) - 1) > 1e-9:
        raise ValueError(f"{pad}: de aandelen van de rekeningen tellen niet op tot 1")
    for k in ("bureau", "opdrachtgever"):
        if not isinstance(getattr(inst, k), str):
            raise ValueError(f"{pad}: {k} is een tekst tussen aanhalingstekens")
    if not isinstance(inst.btw, int | float) or isinstance(inst.btw, bool) or not 0 <= inst.btw < 1:
        raise ValueError(f"{pad}: btw is een getal tussen 0 en 1, bijvoorbeeld btw = 0.21")
    if not isinstance(inst.betaaltermijn, int) or isinstance(inst.betaaltermijn, bool) or inst.betaaltermijn < 0:
        raise ValueError(f"{pad}: betaaltermijn is een aantal dagen, bijvoorbeeld betaaltermijn = 30")
    if inst.blok not in BLOKKEN:
        raise ValueError(f"{pad}: blok is 'week', '4 weken' of 'maand', niet '{inst.blok}'")
    return inst
