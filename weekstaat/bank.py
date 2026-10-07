"""Koppelt de bankontvangsten aan de orders.

De opdrachtgever betaalt per order, niet per factuur van het bureau, en verdeelt elke betaling over de rekeningen
van het bureau volgens vaste aandelen (zie `Instellingen.rekeningen`). Het bedrag incl. btw van een order wordt dus
per rekening teruggevonden als aandeel van dat bedrag."""

import re
from typing import NamedTuple

import pandas as pd

from .instellingen import Instellingen

UITVOER = (
    "datum rekening aandeel entiteit bedrag omschrijving orders medewerkers verwacht rest betreft opmerking".split()
)


class Betaling(NamedTuple):
    """Wat een rekening van een order ontving."""

    datum: pd.Timestamp
    bedrag: float  # het bedrag van de bankregel
    verzameld: bool  # de bankregel is groter dan of anders dan het aandeel van deze order
    deel: float  # het aandeel van deze order
    sluit: bool  # de bankregel sluit op de cent aan op de gekoppelde orders


def noemt_ander(omschrijving: str, anderen: list[str]) -> bool:
    """Een betaling zonder order noemt soms een andere medewerker: de voornaam als heel woord, of het begin van de
    achternaam (de eerste drie letters van het laatste woord, want een bank kapt namen af) als begin van een woord.
    Losse letters midden in een woord tellen niet: "Jan" past niet in "januari" en "Bas" niet in "basis"."""
    woorden = re.findall(r"\w+", omschrijving.lower())
    for naam in anderen:
        delen = naam.lower().split()
        if not delen:
            continue
        if delen[0] in woorden or any(w.startswith(delen[-1][:3]) for w in woorden):
            return True
    return False


def koppel(
    bankregels: pd.DataFrame,
    kop: pd.DataFrame,
    regels: pd.DataFrame,
    medewerker: str,
    anderen: list[str],
    inst: Instellingen,
) -> tuple[pd.DataFrame, dict[str, dict[str, Betaling]]]:
    """Geeft de bankregels van alle rekeningen met de orders waar ze bij horen, en per order wat elke rekening ontving.

    `bankregels` heeft de kolommen van `inlezen.lees_bank`. Per rekening staan de regels uit de bankexport eerst en de
    ontvangsten die alleen op de debiteurenkaart stonden daarna; de volgorde telt, want een order koppelt aan één
    betaling en de eerste betaling die past, krijgt hem. `kop` heeft per order één regel (met `incl`), `regels` zijn
    de orderregels van alle medewerkers, en `anderen` zijn de namen van de andere medewerkers van het bureau."""
    if not kop.order.is_unique:
        raise ValueError("kop heeft een order meer dan één keer")
    k = kop.set_index("order")
    wie = regels.groupby("order").medewerker.agg(lambda s: ", ".join(sorted(set(s.dropna()))))
    for iban in set(bankregels.rekening):
        inst.rekening(iban)  # een onbekende rekening is een fout in de instellingen, geen regel om stil te laten vallen
    delen, betaald = [], {}
    for rekening in inst.rekeningen:
        b = bankregels[bankregels.rekening.str.replace(" ", "") == rekening.iban].copy()
        b["entiteit"] = b.tegenpartij.map(inst.entiteit)
        b = b.sort_values("datum", kind="stable").reset_index(drop=True)
        deel = (k.incl * rekening.aandeel).round(2)
        vrij = set(k.index)
        orders = [[] for _ in b.index]
        verwacht = [0.0] * len(b)
        for i, r in enumerate(b.itertuples()):  # 1. één betaling = één order
            v = k[k.index.isin(vrij)]
            # De boekhouding noemt de betalende entiteit niet betrouwbaar: zonder tegenpartij past alleen het bedrag.
            c = v[((v.entiteit == r.entiteit) | (r.entiteit == "?")) & ((deel[v.index] - r.bedrag).abs() < 0.015)]
            c = c[c.factuurdatum <= r.datum]
            if len(c):
                nr = (r.datum - c.factuurdatum).idxmin()
                orders[i], verwacht[i] = [nr], deel[nr]
                vrij.discard(nr)
        for i, r in enumerate(b.itertuples()):  # 1b. één order min één creditorder, op de cent, ongeacht de datum
            if orders[i]:
                continue
            v = k[k.index.isin(vrij) & (k.entiteit == r.entiteit) & (k.factuurdatum <= r.datum)]
            paar = [
                (x, y)
                for x in v.index[v.incl > 0]
                for y in v.index[v.incl < 0]
                if abs(deel[x] + deel[y] - r.bedrag) < 0.025
            ]
            if paar:
                orders[i], verwacht[i] = list(paar[0]), round(deel[list(paar[0])].sum(), 2)
                vrij -= set(paar[0])
        for i, r in enumerate(b.itertuples()):  # 2. verzamelbetaling: orders van rond de betaaltermijn eerder
            if orders[i]:
                continue
            v = k[k.index.isin(vrij) & (k.entiteit == r.entiteit)]
            # De orders liggen van betaaltermijn min 10 t/m betaaltermijn plus 10 dagen terug (bij 30 dagen: 20 tot 40),
            # maar nooit van ná de betaaldatum.
            dagen = (r.datum - v.factuurdatum).dt.days
            c = v[(v.incl > 0) & dagen.between(max(0, inst.betaaltermijn - 10), inst.betaaltermijn + 10)]
            # Grove ondergrens, geen subset-som: verfijnen als het misgrijpt.
            if len(c) and deel[c.index].sum() < r.bedrag * 2:
                # De opdrachtgever verrekent een creditorder met een latere betaling, soms maanden later:
                # past het tekort precies, dan hoort hij erbij.
                tekort = r.bedrag - deel[c.index].sum()
                cr = v[(v.incl < 0) & (v.factuurdatum <= r.datum) & ((deel[v.index] - tekort).abs() < 0.025)]
                nrs = list(c.index) + list(cr.index[:1])
                orders[i], verwacht[i] = nrs, round(deel[nrs].sum(), 2)
                vrij -= set(nrs)
        b["orders"], b["verwacht"] = orders, verwacht
        b["rekening"], b["aandeel"] = rekening.naam, rekening.aandeel
        b["rest"] = (b.bedrag - b.verwacht).round(2)
        b.loc[b.rest.abs() < 0.025, "rest"] = 0.0  # afronding van het aandeel per order
        for r in b.itertuples():
            for nr in r.orders:
                betaald.setdefault(nr, {})[rekening.naam] = Betaling(
                    r.datum, r.bedrag, len(r.orders) > 1 or abs(r.rest) >= 0.015, deel[nr], abs(r.rest) < 0.015
                )
        delen.append(b)
    b = pd.concat(delen).sort_values(["datum", "rekening"]).reset_index(drop=True)
    b["medewerkers"] = b.orders.map(
        lambda ls: ", ".join(sorted({w for nr in ls for w in wie.get(nr, "").split(", ") if w}))
    )
    b["betreft"] = [
        ("ja" if medewerker in r.medewerkers else "nee")
        if r.orders
        else ("nee" if noemt_ander(r.omschrijving, anderen) else "mogelijk")
        for r in b.itertuples()
    ]
    b["opmerking"] = [
        "" if r.orders and abs(r.rest) < 0.015
        else f"verzamelbetaling; rest {r.rest:.2f} hoort bij een order die niet in de map zit" if r.orders
        else "geen order in de map bij deze betaling"
        for r in b.itertuples()
    ]  # fmt: skip
    kolommen = list(UITVOER)
    return b[kolommen], betaald
