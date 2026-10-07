"""De bevindingen die de tool zelf uit het model afleidt, en wat daaruit volgt: opmerkingen bij het werkboek, een
actielijst en `bevindingen.csv`.

`leid_af` leest alleen de tabellen van de `Overzicht`-objecten en rekent niets opnieuw: elk bedrag staat op een blad
van het werkboek (`bewijs` noemt het). Een bevinding zegt wat de tool in de bronnen ziet, of vraagt de lezer iets na
te gaan; de tool weet niet waarom iets zo is, en ook niet wat de opdrachtgever of het bureau gewoonlijk doet. Houd de
lijst overzienbaar: één bevinding per blok of per losse dag voor uren zonder order, één per order, één per oorzaak per
medewerker voor de onkosten."""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import inlezen, omzetten
from .aansluiten import (
    CV,
    CW,
    NOG,
    OV,
    OW,
    naamsleutel,  # zo vergelijkt `aansluiten` namen: voornaam en laatste woord
)
from .dossier import Dossier
from .instellingen import Instellingen
from .opmaak import nl_bedrag, zin
from .overzicht import (
    AFRONDING,
    KLEIN,
    O_BLOK,
    O_DAG,
    O_DAGV,
    O_DUBBEL,
    O_LOS,
    O_NIETGEF,
    O_ONK,
    O_TOEZEGGING,
    O_UREN,
    ORDER_BOEKHOUDING,
    ORDER_CREDIT_NIET,
    ORDER_DEELS,
    ORDER_NIET,
    ORDER_VERREKEND,
    T_ALLEEN,
    T_DUBBEL,
    Overzicht,
)

log = logging.getLogger(__name__)

ALGEMEEN = "Algemeen"
GROEPEN = ("Naar de opdrachtgever", "Zelf doen in de boekhouding", "Navragen", "Nog uitzoeken", "Aanleveren")
G_OPDRACHTGEVER, G_BOEKHOUDING, G_NAVRAGEN, G_UITZOEKEN, G_AANLEVEREN = GROEPEN
KOLOMMEN = ["sleutel", "soort", "medewerker", "onderwerp", "bedrag", "btw", "wat", "bewijs", "actie", "wie", "groep"]

BOVEN = 1000.0  # een automatische opmerking boven dit bedrag is belangrijk

# De soorten, in de volgorde waarin ze in de lijst staan. Het soort-deel van de sleutel volgt uit de naam.
S_ZONDER_ORDER = "Uren zonder order"
S_DAGEN = "Dagen ontbreken op de order"
S_TOEZEGGING = "Uren op een toezegging"
S_TOEZ_DUBBEL = "Week dubbel op toezegging en order"
S_NIET_BETAALD = "Order niet betaald"
S_DEELS = "Order deels betaald"
S_CREDIT_NIET = "Creditorder niet verrekend"
S_CREDIT = "Creditorder verrekend"
S_NIETGEF = "Wel op order, niet gefactureerd"
S_UREN = "Uren of tarief anders"
S_DAGV = "Dagvergoeding niet op order"
S_LOS = "Losse onkostenpost niet op order"
S_ONK = "Onkosten anders dan op de order"
S_DUBBEL_FACT = "Dubbel gefactureerd"
S_DUBBEL_ORDER = "Dubbel op orders"
S_MEER_FACT = "Meer gefactureerd dan de urenstaat"
S_MINDER_FACT = "Minder gefactureerd dan de urenstaat"
S_MEER_BETAALD = "Meer betaald dan gefactureerd"
S_AFLETTEREN = "Kan worden afgeletterd"
S_KAART = "Debiteurenkaart sluit niet aan"
S_VERVALLEN = "Vervangen order"
S_BRON = "Bron ontbreekt"
S_ONBEKEND = "Order voor een medewerker zonder uren"
S_BUITEN_UREN = "Open facturen buiten de uren"


@dataclass(frozen=True)
class Bevinding:
    """Eén bevinding. `sleutel` is `<soort>|<medewerker>|<periode, week of ordernummer>` en blijft gelijk zolang de
    invoer gelijk blijft; een opmerking verwijst er in kolom `bevinding` naar. `bedrag` is `None` als er geen bedrag
    bij hoort, `btw` is `ex`, `incl` of leeg, `bewijs` het blad waar het te zien is en `wie` is aan zet."""

    sleutel: str
    soort: str
    medewerker: str  # of "Algemeen"
    onderwerp: str
    bedrag: float | None
    btw: str
    wat: str
    bewijs: str
    actie: str
    wie: str
    groep: str


# ---------- kleine hulpen ----------


def _slug(soort: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", soort.lower()).strip("-")


def _maak(
    soort: str, medewerker: str, deel: str, onderwerp: str, bedrag: float | None, btw: str, wat: str, bewijs: str,
    actie: str, wie: str, groep: str,
) -> Bevinding:  # fmt: skip
    """Een bevinding met zijn sleutel. Een komma in het laatste deel wordt een plus: in kolom `bevinding` van de
    opmerkingen staan sleutels achter elkaar met een komma ertussen."""
    sleutel = f"{_slug(soort)}|{medewerker}|{deel.replace(', ', '+').replace(',', '+')}"
    bedrag = None if bedrag is None else round(float(bedrag), 2)
    return Bevinding(sleutel, soort, medewerker, zin(onderwerp), bedrag, btw, zin(wat), bewijs, zin(actie), wie, groep)


def _wie(inst: Instellingen, toewijzingen: set[str]) -> str:
    """Wie aan zet is volgens de toewijzing in het model: één toewijzing voor alle regels, anders nog uitzoeken."""
    namen = {OW: inst.opdrachtgever, OV: inst.opdrachtgever, CW: inst.bureau, CV: inst.bureau}
    return namen.get(next(iter(toewijzingen)), NOG) if len(toewijzingen) == 1 else NOG


def _groep(inst: Instellingen, wie: str) -> str:
    return G_OPDRACHTGEVER if wie == inst.opdrachtgever else G_BOEKHOUDING if wie == inst.bureau else G_UITZOEKEN


def _opsomming(items, hoogstens: int = 4, getoond: int = 3) -> str:
    """'a, b en c'; bij meer dan `hoogstens` de eerste `getoond` en het aantal andere."""
    lijst = list(dict.fromkeys(items))
    if len(lijst) > hoogstens:
        return f"{', '.join(lijst[:getoond])} en {len(lijst) - getoond} andere"
    return lijst[0] if len(lijst) == 1 else f"{', '.join(lijst[:-1])} en {lijst[-1]}" if lijst else ""


def _weken(weken) -> str:
    w = sorted(set(weken))
    return ", ".join(w) if len(w) <= 3 else f"{len(w)} weken ({w[0]} t/m {w[-1]})"


def _perioden(perioden) -> str:
    """Blokken of maanden (geen weken): '2025 wk 13-16', of bij meer dan drie '8 perioden (eerste t/m laatste)'."""
    p = sorted(set(perioden))
    return ", ".join(p) if len(p) <= 3 else f"{len(p)} perioden ({p[0]} t/m {p[-1]})"


def _lijst(kolom: pd.Series) -> list[str]:
    """De losse namen uit een kolom waarin namen met ', ' zijn samengevoegd, zonder dubbelen."""
    return sorted({x for t in kolom for x in str(t).split(", ") if x and x != "—"})


def _dagen(v: pd.DataFrame) -> str:
    """De dagen van de regels als '14-03, 15-03'; zijn het er meer dan drie, dan '5 dagen (17-03 t/m 21-03)'."""
    dagen = [x for t in v.dagen for x in t.split(", ") if x]
    return ", ".join(dagen) if len(dagen) <= 3 else f"{len(dagen)} dagen ({dagen[0]} t/m {dagen[-1]})"


def _aantal_dagen(v: pd.DataFrame) -> str:
    n = sum(len([x for x in t.split(", ") if x]) for t in v.dagen)
    return f"{n} dag" if n == 1 else f"{n} dagen"


def _posten(n: int, een: str = "extra post", meer: str = "extra posten") -> str:
    return f"{n} {een if n == 1 else meer}"


def _datum(x: pd.Timestamp) -> str:
    return f"{x:%d-%m-%Y}"


# ---------- uit de tabel Verschillen ----------


def _verschillen_per(o: Overzicht, oorzaak: str, per: str | None):
    """De regels van een oorzaak van blad Verschillen, per `per` (een kolom) of allemaal samen (`None`)."""
    v = o.verschillen[o.verschillen.oorzaak == oorzaak]
    if v.empty:
        return
    if per is None:
        yield "alle", v
        return
    for sleutel, g in v.groupby(per, sort=True):
        yield str(sleutel), g


def _zonder_order(o: Overzicht) -> list[Bevinding]:
    inst, uit = o.inst, []
    for periode, v in _verschillen_per(o, O_BLOK, "periode"):
        wie = _wie(inst, set(v.toewijzing))
        nrs = _lijst(v.facturen)
        facturen = f"{'factuur' if len(nrs) == 1 else 'facturen'} {_opsomming(nrs)}"
        wat = (
            f"{inst.bureau} factureerde {nl_bedrag(v.verschil.sum())} ex btw in {_weken(v.week)} ({facturen}); "
            f"voor {periode} is geen order gevonden."
        )
        uit.append(_maak(
            S_ZONDER_ORDER, o.medewerker, periode, f"Uren zonder order, {periode}", abs(v.verschil.sum()), "ex", wat,
            "Blad Verschillen", f"Vraag {inst.opdrachtgever} om een order voor {periode}.", wie, _groep(inst, wie),
        ))  # fmt: skip
    return uit


def _dagen_ontbreken(o: Overzicht) -> list[Bevinding]:
    inst, uit = o.inst, []
    for periode, v in _verschillen_per(o, O_DAG, "periode"):
        wie, dagen = _wie(inst, set(v.toewijzing)), _dagen(v)
        wat = (
            f"{inst.bureau} factureerde {nl_bedrag(v.verschil.sum())} ex btw aan uren op {dagen} in {_weken(v.week)}; "
            "die dagen staan niet op de order van dit blok."
        )
        actie = (
            f"Vraag {inst.opdrachtgever} om een order voor {dagen}, of ga na of die dagen aan de order van dit blok "
            "kunnen worden toegevoegd."
        )
        uit.append(_maak(
            S_DAGEN, o.medewerker, periode, f"Dagen ontbreken op de order, {periode}", abs(v.verschil.sum()), "ex", wat,
            "Blad Verschillen", actie, wie, _groep(inst, wie),
        ))  # fmt: skip
    return uit


def _toezeggingen(o: Overzicht) -> list[Bevinding]:
    """Weken die niet op een order staan maar wel op een toezegging, en weken die op allebei staan. Per toezegging."""
    if o.toezegging is None:
        return []
    inst, t, uit = o.inst, o.toezegging, []
    nummer = {r.week_of_maand: r.toezegging for r in t.itertuples() if r.soort in (T_ALLEEN, T_DUBBEL)}
    v = o.verschillen[o.verschillen.oorzaak == O_TOEZEGGING]
    for nr, g in v.groupby(v.week.map(nummer).fillna("onbekend")):
        wie = _wie(inst, set(g.toewijzing))
        wat = (
            f"Voor {_weken(g.week)} staat geen order, wel een regel op toezegging {nr}. {inst.bureau} factureerde "
            f"{nl_bedrag(g.verschil.sum())} ex btw. Er is geen betaling voor gevonden."
        )
        actie = (
            f"Ga na of de facturen voor deze weken het nummer {nr} dragen, en stuur ze zo nodig opnieuw met dat nummer "
            f"aan {inst.opdrachtgever}."
        )
        uit.append(_maak(
            S_TOEZEGGING, o.medewerker, str(nr), f"Uren op toezegging {nr}, geen order", abs(g.verschil.sum()), "ex",
            wat, "Blad Zonder order, blok D", actie, wie, _groep(inst, wie),
        ))  # fmt: skip
    for nr, g in t[t.soort == T_DUBBEL].groupby("toezegging"):
        orders = _opsomming(_lijst(g.op_order))
        wat = (
            f"{_weken(g.week_of_maand)} staan op toezegging {nr} en ook op order {orders}: samen "
            f"{nl_bedrag(g.bedrag.sum())} ex btw volgens de toezegging."
        )
        actie = (
            f"Ga na of {inst.opdrachtgever} deze weken niet twee keer opneemt, en vraag zo nodig om een van de twee "
            "aan te passen."
        )
        uit.append(_maak(
            S_TOEZ_DUBBEL, o.medewerker, str(nr), f"Weken dubbel op toezegging {nr} en order", g.bedrag.sum(), "ex",
            wat, "Blad Zonder order, blok D", actie, inst.opdrachtgever, G_OPDRACHTGEVER,
        ))  # fmt: skip
    return uit


def _niet_gefactureerd(o: Overzicht) -> list[Bevinding]:
    inst, uit = o.inst, []
    for periode, v in _verschillen_per(o, O_NIETGEF, "periode"):
        dagen = _dagen(v)
        wat = (
            f"Op de order staat {nl_bedrag(-v.verschil.sum())} ex btw voor {dagen} in {_weken(v.week)}; er is geen "
            f"urenregel van {inst.bureau} die dit factureert."
        )
        actie = (
            f"Ga na of er op {dagen} is gewerkt: zo ja, factureer het; zo nee, vraag {inst.opdrachtgever} de order aan "
            "te passen."
        )
        uit.append(_maak(
            S_NIETGEF, o.medewerker, periode, f"Wel op order, niet gefactureerd, {periode}", abs(v.verschil.sum()),
            "ex", wat, "Blad Verschillen", actie, _wie(inst, set(v.toewijzing)), G_NAVRAGEN,
        ))  # fmt: skip
    return uit


def _meer_en_minder(v: pd.DataFrame) -> str:
    """Per saldo meer of minder gefactureerd dan op de orders staat; met beide kanten als ze allebei voorkomen."""
    per_saldo = v.verschil.sum()
    tekst = f"{nl_bedrag(abs(per_saldo))} ex btw {'meer' if per_saldo > 0 else 'minder'} gefactureerd dan op de orders"
    meer, minder = v.verschil[v.verschil > 0].sum(), -v.verschil[v.verschil < 0].sum()
    if meer > 0.005 and minder > 0.005:
        tekst += f" ({nl_bedrag(meer)} meer en {nl_bedrag(minder)} minder)"
    return tekst


def _uren_anders(o: Overzicht) -> list[Bevinding]:
    inst, uit = o.inst, []
    for deel, v in _verschillen_per(o, O_UREN, None):
        wie = _wie(inst, set(v.toewijzing))
        wat = (
            f"Op {_aantal_dagen(v)} in {_perioden(v.periode)} staan op de order een ander aantal uren of een ander "
            f"tarief dan {inst.bureau} factureerde: per saldo {_meer_en_minder(v)}."
        )
        uit.append(_maak(
            S_UREN, o.medewerker, deel, "Uren of tarief anders dan op de order", abs(v.verschil.sum()), "ex", wat,
            "Blad Verschillen", "Ga na welk tarief en welk aantal uren gelden voor deze dagen (zie blad Verschillen).",
            wie, _groep(inst, wie),
        ))  # fmt: skip
    return uit


def _onkosten(o: Overzicht) -> list[Bevinding]:
    inst, uit = o.inst, []
    for deel, v in _verschillen_per(o, O_DAGV, None):
        wie = _wie(inst, set(v.toewijzing))
        wat = (
            f"{inst.bureau} factureerde op {_aantal_dagen(v)} een vast bedrag per gewerkte dag dat op geen order "
            f"staat: samen {nl_bedrag(v.verschil.sum())} ex btw."
        )
        uit.append(_maak(
            S_DAGV, o.medewerker, deel, "Dagvergoeding niet op de order", abs(v.verschil.sum()), "ex", wat,
            "Blad Verschillen", f"Ga na of {inst.opdrachtgever} dit vaste bedrag per dag heeft afgesproken.", wie,
            _groep(inst, wie),
        ))  # fmt: skip
    for deel, v in _verschillen_per(o, O_LOS, None):
        wie = _wie(inst, set(v.toewijzing))
        posten = _posten(len(v), "losse onkostenpost", "losse onkostenposten")
        wat = (
            f"{inst.bureau} factureerde {posten} die op geen order staan: samen "
            f"{nl_bedrag(v.verschil.sum())} ex btw ({_weken(v.week)})."
        )
        uit.append(_maak(
            S_LOS, o.medewerker, deel, "Losse onkostenpost niet op de order", abs(v.verschil.sum()), "ex", wat,
            "Blad Verschillen", f"Ga na wat dit is en of het met {inst.opdrachtgever} is afgesproken.", wie,
            _groep(inst, wie),
        ))  # fmt: skip
    for deel, v in _verschillen_per(o, O_ONK, None):
        wie = _wie(inst, set(v.toewijzing))
        wat = (
            f"Op {_aantal_dagen(v)} staan onkosten op de order voor een ander bedrag dan {inst.bureau} "
            f"factureerde: {nl_bedrag(v.fact.sum())} gefactureerd en {nl_bedrag(v.order.sum())} op de order, "
            "beide ex btw."
        )
        uit.append(_maak(
            S_ONK, o.medewerker, deel, "Onkosten anders dan op de order", abs(v.verschil.sum()), "ex", wat,
            "Blad Verschillen", "Ga na welk bedrag aan onkosten klopt en wat daarvoor is afgesproken.", wie,
            _groep(inst, wie),
        ))  # fmt: skip
    return uit


def _dubbel(o: Overzicht) -> list[Bevinding]:
    """Extra posten uit het oordeel van de gebruiker (`saldo <naam>.csv`) voor kilometers die dubbel zijn: de kant van
    de facturen (dubbel gefactureerd) en de kant van de orders (dubbel op orders)."""
    inst, uit = o.inst, []
    for _, v in _verschillen_per(o, O_DUBBEL, None):
        for soort, deel in ((S_DUBBEL_FACT, v[v.verschil > 0]), (S_DUBBEL_ORDER, v[v.verschil < 0])):
            if deel.empty:
                continue
            wie, kant = _wie(inst, set(deel.toewijzing)), "gefactureerd" if soort == S_DUBBEL_FACT else "op orders"
            wat = (
                f"Het oordeel in saldo {o.medewerker}.csv heeft {_posten(len(deel))} van samen "
                f"{nl_bedrag(abs(deel.verschil.sum()))} ex btw voor kilometers die twee keer zijn {kant}."
                if soort == S_DUBBEL_FACT
                else f"Het oordeel in saldo {o.medewerker}.csv heeft {_posten(len(deel))} van samen "
                f"{nl_bedrag(abs(deel.verschil.sum()))} ex btw voor kilometers die op twee orders staan."
            )
            actie = (
                f"Ga na of {inst.bureau} dezelfde kilometers twee keer heeft gefactureerd, en crediteer ze zo nodig."
                if soort == S_DUBBEL_FACT
                else f"Meld {inst.opdrachtgever} dat dezelfde kilometers op twee orders lijken te staan, of ga na of "
                "dat klopt."
            )
            uit.append(_maak(
                soort, o.medewerker, "alle", f"{soort}: kilometers", abs(deel.verschil.sum()), "ex", wat,
                "Blad Verschillen", actie, wie, _groep(inst, wie),
            ))  # fmt: skip
    return uit


# ---------- uit de weken ----------


def _urenstaat(o: Overzicht) -> list[Bevinding]:
    """Weken waarin het bureau meer of minder factureerde dan de goedgekeurde urenstaat."""
    inst, uit = o.inst, []
    w = o.weken
    for r in w[(w.urenstaat_bedrag - w.fact).abs() > 0.005].itertuples():
        meer = r.fact > r.urenstaat_bedrag
        verschil = abs(r.fact - r.urenstaat_bedrag)
        wat = (
            f"Voor {r.week} staat {nl_bedrag(r.fact)} ex btw gefactureerd en {nl_bedrag(r.urenstaat_bedrag)} op de "
            f"goedgekeurde urenstaat: {nl_bedrag(verschil)} {'meer' if meer else 'minder'} gefactureerd."
        )
        if r.opmerking:
            wat += f" Toelichting uit urenstaat-afwijkend: {r.opmerking}"
        actie = (
            "Ga na of deze week dubbel is gefactureerd, en crediteer of meld het zo nodig."
            if meer
            else "Ga na of het ontbrekende bedrag nog kan worden gefactureerd."
        )
        soort = S_MEER_FACT if meer else S_MINDER_FACT
        uit.append(_maak(
            soort, o.medewerker, r.week, f"{soort}, {r.week}", verschil, "ex", wat, "Blad Per week", actie,
            inst.bureau, G_BOEKHOUDING,
        ))  # fmt: skip
    return uit


# ---------- uit de orders ----------


def _orders(o: Overzicht) -> list[Bevinding]:
    """Per order: niet betaald (na de betaaltermijn), deels betaald, en een creditorder die wel of niet is verrekend."""
    if o.orders.empty:
        return []
    inst, bank_tot, uit = (
        o.inst,
        o.aansluiting.bank_tot,
        {s: [] for s in (S_NIET_BETAALD, S_DEELS, S_CREDIT_NIET, S_CREDIT)},
    )
    for r in o.orders.itertuples():
        stuk = f"{r.order} van {_datum(r.factuurdatum)} ({nl_bedrag(abs(r.incl))} incl. btw)"
        if r.soort == ORDER_NIET:
            vervalt = r.factuurdatum + pd.Timedelta(days=inst.betaaltermijn)
            wat = (
                f"Order {stuk}: er is niets van ontvangen. De betaaltermijn van {inst.betaaltermijn} dagen was "
                f"verstreken op {_datum(vervalt)} en de bankexport loopt tot {_datum(bank_tot)}."
            )
            if r.anderen:
                wat += f" Hiervan is {nl_bedrag(r.waarvan_medewerker)} ex btw van {o.medewerker}."
            uit[S_NIET_BETAALD].append(_maak(
                S_NIET_BETAALD, o.medewerker, r.order, f"Order {r.order} niet ontvangen", r.open, "incl", wat,
                "Blad Orders", f"Vraag {inst.opdrachtgever} om betaling van order {r.order}, of om te laten weten "
                "waarom die niet is gedaan.", inst.opdrachtgever, G_OPDRACHTGEVER,
            ))  # fmt: skip
        elif r.soort == ORDER_DEELS:
            wat = f"Van order {stuk} is {nl_bedrag(r.ontvangen)} ontvangen; {nl_bedrag(r.open)} is niet ontvangen."
            uit[S_DEELS].append(_maak(
                S_DEELS, o.medewerker, r.order, f"Order {r.order} deels ontvangen", r.open, "incl", wat, "Blad Orders",
                f"Vraag {inst.opdrachtgever} waarom {nl_bedrag(r.open)} niet is betaald op order {r.order}.",
                inst.opdrachtgever, G_OPDRACHTGEVER,
            ))  # fmt: skip
        elif r.soort == ORDER_CREDIT_NIET:
            wat = f"Creditorder {stuk} is in de bankexport niet terug te vinden als verrekening."
            uit[S_CREDIT_NIET].append(_maak(
                S_CREDIT_NIET, o.medewerker, r.order, f"Creditorder {r.order} niet verrekend gevonden", abs(r.incl),
                "incl", wat, "Blad Orders", f"Vraag {inst.opdrachtgever} of creditorder {r.order} al is verrekend, en "
                "met welke betaling.", inst.opdrachtgever, G_OPDRACHTGEVER,
            ))  # fmt: skip
        elif r.incl < 0 and r.soort in (ORDER_VERREKEND, ORDER_BOEKHOUDING):
            wat = f"Creditorder {stuk} is verrekend met een betaling van {inst.opdrachtgever}."
            uit[S_CREDIT].append(_maak(
                S_CREDIT, o.medewerker, r.order, f"Creditorder {r.order} verrekend", abs(r.incl), "incl", wat,
                "Blad Orders", f"Ga na of {inst.bureau} de creditorder {r.order} heeft geboekt in de boekhouding.",
                inst.bureau, G_BOEKHOUDING,
            ))  # fmt: skip
    return [b for lijst in uit.values() for b in lijst]


def _vervallen(o: Overzicht) -> list[Bevinding]:
    inst, uit = o.inst, []
    for r in o.vervallen_orders().itertuples():
        door = r.vervangen_door or ""
        status = f"vervangen door {door}" if door else "vervallen"
        wat = f"Order {r.order} staat in vervallen-orders.csv als {status}; hij telt niet mee in dit overzicht."
        wat += f" Toelichting: {r.toelichting}" if r.toelichting else ""
        actie = f"Vraag {inst.opdrachtgever} te bevestigen dat order {r.order} is {status}."
        uit.append(_maak(
            S_VERVALLEN, o.medewerker, r.order, f"Order {r.order} vervangen", r.waarvan_medewerker, "ex", wat,
            "Blad Orders", actie, inst.opdrachtgever, G_OPDRACHTGEVER,
        ))  # fmt: skip
    return uit


# ---------- uit de debiteurenkaart ----------


def _kaart(o: Overzicht) -> list[Bevinding]:
    """Facturen die betaald zijn maar nog open staan (per order), meer betaald dan gefactureerd (per factuur), en een
    kaart die niet aansluit op bank en orders."""
    k = o.kaart
    if k is None:
        return []
    inst, f, uit = o.inst, k.facturen, []
    betaald = f[(f.kaart >= 0.005) & (f.ontvangen >= 0.005) & (f.echt_open < AFRONDING)]
    for orders, g in betaald.groupby("orders", sort=True):
        nrs, een = _opsomming(g.factuur), len(g) == 1
        van = _opsomming(orders.split(", "), 3)
        wat = (
            f"{'Factuur' if een else 'Facturen'} {nrs} {'staat' if een else 'staan'} nog open op de debiteurenkaart "
            f"({nl_bedrag(g.kaart.sum())} incl. btw), terwijl {inst.opdrachtgever} het bedrag voor {van} heeft betaald."
        )
        actie = (
            f"Ga na of {nrs} in de boekhouding van {inst.bureau} {'kan' if een else 'kunnen'} worden afgeletterd tegen "
            f"de ontvangst voor {van}."
        )
        uit.append(_maak(
            S_AFLETTEREN, o.medewerker, orders, f"Betaalde facturen afletteren, {van}", g.kaart.sum(), "incl", wat,
            "Blad Debiteurenkaart, blok C", actie, inst.bureau, G_BOEKHOUDING,
        ))  # fmt: skip
    for r in f[(f.kaart < 0.005) & (f.echt_open < -KLEIN)].itertuples():
        meer = -r.echt_open
        wat = (
            f"{inst.opdrachtgever} betaalde {nl_bedrag(meer)} incl. btw meer dan {inst.bureau} factureerde voor "
            f"factuur {r.factuur} ({r.orders}); de factuur is afgeletterd."
        )
        actie = (
            f"Ga na waar dit bedrag vandaan komt: staat het op een order voor dagen die {inst.bureau} niet heeft "
            "gefactureerd (zie blad Verschillen), of is er te veel betaald?"
        )
        uit.append(_maak(
            S_MEER_BETAALD, o.medewerker, r.factuur, f"Meer betaald dan gefactureerd, factuur {r.factuur}", meer,
            "incl", wat, "Blad Debiteurenkaart, blok C", actie, NOG, G_NAVRAGEN,
        ))  # fmt: skip
    if not k.sluit:
        verschil = round(k.echt_open - k.controle, 2)
        wat = (
            f"Wat er volgens de debiteurenkaart voor {o.medewerker} echt openstaat ({nl_bedrag(k.echt_open)} incl. "
            f"btw) wijkt {nl_bedrag(abs(verschil))} af van wat bank en orders per factuur zeggen "
            f"({nl_bedrag(k.controle)})."
        )
        actie = (
            "Ga na of de ontvangsten en de open facturen van deze medewerker op de kaart compleet zijn en bij de "
            "juiste orders horen (blad Debiteurenkaart, blok B en C)."
        )
        uit.append(_maak(
            S_KAART, o.medewerker, "debiteurenkaart", "Debiteurenkaart sluit niet aan op bank en orders",
            abs(verschil), "incl", wat, "Blad Debiteurenkaart, blok B", actie, NOG, G_UITZOEKEN,
        ))  # fmt: skip
    return uit


# ---------- uit de bronnen ----------


def _bronnen(d: Dossier) -> list[Bevinding]:
    """Wat er aan bronnen ontbreekt: geen bank, geen debiteurenkaart."""
    inst, uit = d.inst, []
    if d.bank is None:
        wat = "Er is geen bankexport in het dossier. Er zijn dus geen ontvangsten en geen oordeel over de betaling van"
        wat += " de orders en de debiteurenkaart wordt niet gebruikt." if d.kaart is not None else " de orders."
        uit.append(_maak(
            S_BRON, ALGEMEEN, "bank", "Er is geen bankexport", None, "", wat, "Blad Stand",
            f"Lever een bankexport aan met de ontvangsten van {inst.opdrachtgever} (bank.csv).", NOG, G_AANLEVEREN,
        ))  # fmt: skip
    if d.kaart is None:
        wat = "Er is geen debiteurenkaart in het dossier. Wat er in de boekhouding nog openstaat, is dus niet te zien."
        uit.append(_maak(
            S_BRON, ALGEMEEN, "debiteurenkaart", "Er is geen debiteurenkaart", None, "", wat, "Blad Stand",
            "Lever de debiteurenkaart aan uit de boekhouding (debiteurenkaart.csv).", NOG, G_AANLEVEREN,
        ))  # fmt: skip
    return uit


def _onbekende_orders(overzichten: list[Overzicht], d: Dossier) -> list[Bevinding]:
    """Orders met regels voor een medewerker die in de uren niet voorkomt. Namen worden vergeleken zoals `aansluiten`
    dat doet (voornaam en laatste woord). Van een order met meer medewerkers noemt de tekst alleen de onbekende naam."""
    inst, uit = d.inst, []
    bekend = {naamsleutel(n) for n in d.uren.medewerker.unique() if str(n).strip()}
    kop = d.kop.sort_values("bestand", key=lambda s: s.str.len(), kind="stable").drop_duplicates("order")
    if d.vervallen is not None:
        kop = kop[~kop.order.isin(d.vervallen.order)]
    regels = d.regels[d.regels.bestand.isin(kop.bestand) & d.regels.medewerker.notna()]
    betaald = overzichten[0].aansluiting.betaald if overzichten and d.bank is not None else None
    for r in kop.sort_values("order").itertuples():
        g = regels[regels.order == r.order]
        g = g[g.medewerker.map(lambda n: bool(str(n).strip()) and naamsleutel(n) not in bekend)]
        if g.empty:
            continue
        namen = sorted(set(g.medewerker))
        wat = (
            f"Op order {r.order} van {_datum(r.factuurdatum)} ({nl_bedrag(r.incl)} incl. btw) staan regels voor "
            f"{_opsomming(namen)}, met datums van {_datum(g.datum.min())} t/m {_datum(g.datum.max())}. In de uren "
            f"staat geen regel voor {'deze naam' if len(namen) == 1 else 'deze namen'}."
        )
        if betaald is not None:
            wat += f" Er is {'een' if betaald.get(r.order) else 'geen'} betaling voor deze order gevonden."
        uit.append(_maak(
            S_ONBEKEND, ALGEMEEN, r.order, f"Order {r.order} voor een medewerker zonder uren", r.incl, "incl", wat,
            "Blad Orderregels", f"Ga na of deze medewerker bij {inst.bureau} hoort en of de uren nog moeten worden "
            "aangeleverd.", NOG, G_AANLEVEREN,
        ))  # fmt: skip
    return uit


def _buiten_uren(d: Dossier) -> list[Bevinding]:
    """Facturen op de debiteurenkaart waarvan het nummer bij geen enkele urenregel voorkomt: één bevinding voor alle."""
    if d.kaart is None:
        return []
    in_uren = set(d.uren.factuurnummer.dropna())
    kf = d.kaart[(d.kaart.soort == "factuur") & ~d.kaart.nummer.isin(in_uren)]
    if kf.empty:
        return []
    nrs = list(dict.fromkeys(kf.nummer))
    een = len(nrs) == 1
    wat = (
        f"Op de debiteurenkaart {'staat' if een else 'staan'} {len(nrs)} open {'factuur' if een else 'facturen'} die "
        f"bij geen enkele urenregel {'voorkomt' if een else 'voorkomen'}: {_opsomming(nrs, 5, 5)}, samen "
        f"{nl_bedrag(kf.bedrag.sum())} incl. btw."
    )
    return [
        _maak(
            S_BUITEN_UREN,
            ALGEMEEN,
            "debiteurenkaart",
            "Open facturen op de kaart die niet in de uren staan",
            kf.bedrag.sum(),
            "incl",
            wat,
            "Blad Debiteurenkaart, blok A",
            "Ga na bij welke medewerker of opdracht deze facturen horen.",
            NOG,
            G_UITZOEKEN,
        )
    ]


# De functies per soort, in de volgorde van de lijst: eerst de uren, dan de orders en de onkosten, dan de kaart.
PER_MEDEWERKER = (
    _zonder_order, _dagen_ontbreken, _toezeggingen, _orders, _niet_gefactureerd, _uren_anders, _onkosten, _dubbel,
    _urenstaat, _kaart, _vervallen,
)  # fmt: skip


def leid_af(overzichten: list[Overzicht], d: Dossier) -> list[Bevinding]:
    """De bevindingen uit de overzichten van alle medewerkers, in een vaste volgorde: eerst wat voor iedereen geldt,
    dan per medewerker (in de volgorde van `overzichten`). Twee keer afleiden uit dezelfde invoer geeft dezelfde
    sleutels in dezelfde volgorde, en geen sleutel komt twee keer voor."""
    namen = [o.medewerker for o in overzichten]
    dubbel = sorted({n for n in namen if namen.count(n) > 1})
    if dubbel:
        raise ValueError(f"{', '.join(dubbel)} komt twee keer voor in de overzichten")
    lijst = [*_bronnen(d), *_onbekende_orders(overzichten, d), *_buiten_uren(d)]
    for o in overzichten:
        lijst += [b for functie in PER_MEDEWERKER for b in functie(o)]
    dubbele = sorted(sleutel for sleutel, n in Counter(b.sleutel for b in lijst).items() if n > 1)
    if dubbele:
        raise ValueError(f"de sleutel {', '.join(dubbele)} komt twee keer voor (een fout in de tool)")
    return lijst


# ---------- opmerkingen, acties en csv ----------


def opmerkingen(bevindingen: list[Bevinding], d: Dossier) -> pd.DataFrame:
    """De opmerkingen voor het werkboek: alle rijen van `opmerkingen.csv` (bron `handmatig`) en elke bevinding waarvan
    de sleutel in geen enkele cel van kolom `bevinding` staat (bron `automatisch`; die kolom mag meer sleutels
    bevatten, gescheiden door een komma). Een handmatige opmerking vervangt zo de automatische.

    Per medewerker eerst de handmatige rijen, dan de automatische; de medewerkers in de volgorde waarin ze voor het
    eerst voorkomen. De automatische rijen krijgen als `nr` A1, A2, ... per medewerker en `belangrijk` is `ja` boven de
    1.000 en voor een order die niet is betaald."""
    handmatig = d.opmerkingen if d.opmerkingen is not None else pd.DataFrame(columns=inlezen.OPMERKINGEN)
    gedekt = {s.strip() for cel in handmatig.bevinding for s in str(cel).split(",") if s.strip()}
    bestaand = {b.sleutel for b in bevindingen}
    for r in handmatig.itertuples():  # een sleutel die bij geen bevinding hoort is waarschijnlijk een typfout
        for sleutel in (x.strip() for x in str(r.bevinding).split(",") if x.strip()):
            if sleutel not in bestaand:
                log.warning(
                    "opmerking %s (%s) verwijst naar bevinding %s, die er niet is",
                    r.nr,
                    r.medewerker or ALGEMEEN,
                    sleutel,
                )
    automatisch, nummer = [], {}
    for b in bevindingen:
        if b.sleutel in gedekt:
            continue
        nummer[b.medewerker] = nummer.get(b.medewerker, 0) + 1
        belangrijk = (b.bedrag is not None and abs(b.bedrag) > BOVEN) or b.soort == S_NIET_BETAALD
        automatisch.append(
            dict(medewerker=b.medewerker, nr=f"A{nummer[b.medewerker]}", belangrijk="ja" if belangrijk else "nee",
                 onderwerp=b.onderwerp, bedrag=b.bedrag, btw=b.btw, wat=b.wat, bewijs=b.bewijs, actie=b.actie,
                 wie=b.wie, bevinding=b.sleutel)
        )  # fmt: skip
    kolommen = [*inlezen.OPMERKINGEN, "bron"]
    delen = [
        x.assign(bron=naam)[kolommen].astype(object)
        for x, naam in (
            (handmatig, "handmatig"),
            (pd.DataFrame(automatisch, columns=inlezen.OPMERKINGEN), "automatisch"),
        )
        if not x.empty
    ]
    if not delen:
        return pd.DataFrame(columns=kolommen)
    alles = pd.concat(delen, ignore_index=True)
    groep = alles.medewerker.map(lambda m: m or ALGEMEEN)
    volgorde = {m: i for i, m in enumerate(dict.fromkeys(groep))}
    rang = (alles.bron == "automatisch").astype(int)
    sorteer = pd.DataFrame({"groep": groep.map(volgorde), "rang": rang, "plek": range(len(alles))})
    alles = alles.loc[sorteer.sort_values(["groep", "rang", "plek"]).index].reset_index(drop=True)
    alles["bedrag"] = pd.to_numeric(alles.bedrag)
    return alles


def _korte_namen(bevindingen: list[Bevinding], d: Dossier, korte_namen: dict[str, str] | None) -> dict[str, str]:
    """Volledige naam -> korte naam: wat de gebruiker meegeeft, anders de voornaam."""
    namen = [*dict.fromkeys([*d.uren.medewerker, *(b.medewerker for b in bevindingen if b.medewerker != ALGEMEEN)])]
    return {n: n.split()[0] for n in namen} | (korte_namen or {})


def _vormen(naam: str, kort: str) -> list[str]:
    """Hoe een medewerker in een tekst kan staan: de korte naam, de voornaam, de volledige naam, alles na de eerste
    voornaam (als dat meer dan één woord is) en het laatste woord alleen (als het minstens vier letters heeft: een kort
    woord als 'Lee' of 'Vos' komt te vaak als iets anders voor)."""
    woorden = naam.split()
    vormen = [kort, naam, *woorden[:1]]
    if len(woorden) > 2:
        vormen.append(" ".join(woorden[1:]))
    if len(woorden) > 1 and len(woorden[-1]) >= 4:
        vormen.append(woorden[-1])
    return [v for v in dict.fromkeys(vormen) if v]


def _voor_wie(tekst: str, korte: dict[str, str]) -> str:
    """De korte namen van de medewerkers die in de tekst staan (zie `_vormen`; hele woorden), of `Algemeen`."""
    gevonden = {}
    for naam, kort in korte.items():
        if any(re.search(rf"(?<!\w){re.escape(v)}(?!\w)", tekst) for v in _vormen(naam, kort)):
            gevonden[kort] = None
    return ", ".join(gevonden) or ALGEMEEN


def _todo_actie(titel: str, toelichting: str, korte: dict[str, str]) -> tuple[str, str, str]:
    """Een actie uit `to-do.md` voor de lijst: de titel zonder punt of dubbele punt aan het eind (een vraagteken
    blijft), voor wie het is en de toelichting met een hoofdletter."""
    schoon = titel.rstrip().rstrip(":.").rstrip()
    return schoon, _voor_wie(f"{titel} {toelichting}", korte), toelichting[:1].upper() + toelichting[1:]


def acties(
    bevindingen: list[Bevinding], d: Dossier, korte_namen: dict[str, str] | None = None
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """De actielijst: `[(groep, [(titel, voor_wie, toelichting), ...])]`.

    Is er een `to-do.md` (`d.todo`), dan is dat de lijst; `voor_wie` zijn dan de korte namen die in titel of toelichting
    staan, anders `Algemeen`. Anders volgen de acties uit de bevindingen, per groep in de vaste volgorde: titel is het
    onderwerp en de toelichting is wat er is gezien plus de actie. `korte_namen` is volledige naam -> korte naam; zonder
    woordenboek is dat de voornaam. Zijn er geen bevindingen, dan staat er één groep `Niets te doen`."""
    korte = _korte_namen(bevindingen, d, korte_namen)
    if d.todo:
        return [
            (groep, [_todo_actie(titel, toelichting, korte) for titel, toelichting in lijst]) for groep, lijst in d.todo
        ]
    if not bevindingen:
        return [("Niets te doen", [("Er zijn geen acties: alles sluit aan.", ALGEMEEN, "")])]
    return [
        (
            groep,
            [
                (b.onderwerp, korte.get(b.medewerker, b.medewerker), f"{b.wat} {b.actie}")
                for b in bevindingen
                if b.groep == groep
            ],
        )
        for groep in GROEPEN
        if any(b.groep == groep for b in bevindingen)
    ]


def schrijf_csv(bevindingen: list[Bevinding], pad: Path) -> None:
    """Schrijft `bevindingen.csv` in de vorm van de sjablonen: puntkomma, decimale komma. Tekst die met `= + - @`
    begint, krijgt een apostrof (zie `omzetten.schrijf_sjabloon`), zodat Excel er geen formule van maakt."""
    tabel = pd.DataFrame([[getattr(b, k) for k in KOLOMMEN] for b in bevindingen], columns=KOLOMMEN)
    omzetten.schrijf_sjabloon(tabel.astype({"bedrag": float}), pad)
