"""Het rekenmodel van het overzicht per medewerker: alle cijfers van de stand, de verschillen, de weken, de orders met
hun betaling, de facturen zonder order en de debiteurenkaart, zonder iets in een werkboek te schrijven.

`maak` neemt een `Aansluiting` (de uitkomst van `sluit_aan`) en het `Dossier` en geeft een `Overzicht`. Elk onderdeel
is een eigen functie hieronder; de schrijvers van de werkboeken lezen alleen de tabellen van het `Overzicht`."""

import re
from dataclasses import dataclass, field
from typing import NamedTuple

import pandas as pd

from . import aansluiten, inlezen
from .aansluiten import (
    ALLEEN,
    CORR,
    CV,
    CW,
    GEEN,
    GEEN_D,
    GEEN_P,
    KLOPT,
    KM,
    NOG,
    ONK,
    OV,
    OW,
    UREN,
    Aansluiting,
    samenvoegen,
    weeksleutels,
)
from .dossier import Dossier, in_naam
from .instellingen import Instellingen
from .opmaak import nl_bedrag, zin  # `overzicht.zin` blijft bestaan: `totaal.py` gebruikt hem

TOEWIJZINGEN = aansluiten.TOEWIJZINGEN

# ---------- de oorzaken van een verschil, in vaste volgorde ----------
O_TOEZEGGING = "Uren zonder order: wel op een toezegging van de opdrachtgever"
O_BLOK = "Uren zonder order: heel blok ontbreekt"
O_DAG = "Uren zonder order: dagen ontbreken op de order"
O_UREN = "Uren of tarief anders dan op de order"
O_DAGV = "Dagvergoeding niet op order"
O_LOS = "Losse onkostenpost niet op order"
O_ONK = "Onkosten anders dan op de order"
O_NIETGEF = "Wel op order, niet door het bureau gefactureerd"
O_DUBBEL = "Kilometers dubbel"
OORZAKEN = (O_TOEZEGGING, O_BLOK, O_DAG, O_UREN, O_DAGV, O_LOS, O_ONK, O_NIETGEF, O_DUBBEL)

# Het voorstel voor wie aan zet is; de kilometers dubbel zijn alleen een extra post uit het oordeel van de gebruiker.
VOORSTEL = {
    O_TOEZEGGING: OW, O_BLOK: OW, O_DAG: OW, O_UREN: NOG, O_DAGV: NOG, O_LOS: NOG, O_ONK: NOG, O_NIETGEF: CW,
}  # fmt: skip
# Een bedrag dat zo vaak als onkostenpost terugkomt, is een vast bedrag per dag; de rest is een losse post.
DAGVERGOEDING_VANAF = 5
NORMALE_UREN = "Normale Uren"  # de soort van gewone gewerkte uren in de export; andere soorten met uren zijn verlof
GOEDGEKEURD = "Goedgekeurd"
KILOMETERS = "Kilometers"

# ---------- de status van een week ----------
W_AANSLUIT = "Sluit aan"
W_ONKOSTEN = "Onkosten niet op order"
W_AFWIJKT = "Wijkt af van de order"
W_DEELS = "Deels geen order"
W_GEEN = "Geen order"
W_NIET_GEF = "Niet gefactureerd door het bureau"
W_NIET_GEWERKT = "Niet gewerkt"
W_GEEN_URENSTAAT = "Geen urenstaat"
WEEKSTATUSSEN = (
    W_AANSLUIT, W_ONKOSTEN, W_AFWIJKT, W_DEELS, W_GEEN, W_NIET_GEF, W_NIET_GEWERKT, W_GEEN_URENSTAAT
)  # fmt: skip

# ---------- de soorten regel van een toezegging ----------
T_ALLEEN = "Uren, alleen op de toezegging"
T_DUBBEL = "Uren, ook op een order (dubbel)"
T_LEEG = "Uren, zonder bedrag"
T_REIS = "Reiskosten"

# ---------- de soorten betaling van een order (kolom `soort` van `Overzicht.orders`) ----------
ORDER_BETAALD = "betaald"
ORDER_VERREKEND = "verrekend"  # een creditorder, verrekend met een betaling
ORDER_BOEKHOUDING = "boekhouding"  # niet op de bank, wel afgeletterd in de boekhouding
ORDER_DEELS = "deels"
ORDER_NIET = "niet"
ORDER_NOG_NIET = "nog_niet_vervallen"
ORDER_CREDIT_NIET = "credit_niet"  # een creditorder waarvan de verrekening niet is gevonden
ORDER_GEEN_BANK = "geen_bank"
BETAALD = (ORDER_BETAALD, ORDER_VERREKEND, ORDER_BOEKHOUDING)  # de soorten van een order die in orde is
# De soort in een paar woorden, voor een lijst van wat nog open staat (blok 4 van de Stand).
ORDER_KORT = {
    ORDER_DEELS: "deels ontvangen", ORDER_NIET: "niet ontvangen",
    ORDER_NOG_NIET: "nog niet vervallen bij einde bankexport", ORDER_CREDIT_NIET: "creditorder",
    ORDER_GEEN_BANK: "geen bank",
}  # fmt: skip

# ---------- de regels van blok B van de kaart (`Kaartblok.regel_sleutels`) ----------
R_OPEN = "open"
R_AF_ONTVANGEN = "af_ontvangen"
R_BIJ_CREDIT = "bij_credit"
R_AF_VERREKEND = "af_verrekend"
R_BIJ_NIET_GEFACTUREERD = "bij_niet_gefactureerd"

# ---------- de controles van `Overzicht.controles` ----------
C_OORZAKEN = "oorzaken"  # blok 2 van de Stand tegenover het verschil van blok 1
C_ZONDER_ORDER = "zonder_order"  # blok A van Zonder order tegenover de facturen
C_KAART = "kaart"  # de kaart tegenover bank en orders per factuur


def uitleg(inst: Instellingen) -> dict[str, str]:
    """Wat elke oorzaak betekent, in woorden voor de lezer van blok 2 van de Stand. Algemeen: geen bedragen of
    voorbeelden, en alleen wat de tool in de bronnen ziet of wat de lezer moet nagaan; de tool weet niet wat de
    opdrachtgever afspreekt of wanneer hij betaalt."""
    return {
        O_TOEZEGGING: f"Wel een toezegging van {inst.opdrachtgever} voor deze week of maand (een schriftelijk stuk dat "
        "de gebruiker heeft overgenomen), maar geen order. Er is geen betaling voor gevonden.",
        O_BLOK: zin(f"{inst.bureau} heeft gefactureerd; voor het hele blok is er geen order."),
        O_DAG: "Het blok heeft wel een order, maar deze dagen staan er niet op.",
        O_DAGV: "Een vast bedrag per gewerkte dag dat wel is gefactureerd en niet op een order staat. Ga na of dit "
        f"met {inst.opdrachtgever} is afgesproken.",
        O_LOS: "Losse onkostenposten die zijn gefactureerd en op geen enkele order staan, zoals een vergoeding of "
        "een post zonder omschrijving. Ga na wat het is.",
        O_NIETGEF: zin(
            f"{inst.opdrachtgever} heeft dit op een order gezet; er is geen urenregel van {inst.bureau} die "
            "het factureert, of het is weer gecrediteerd."
        ),
        O_UREN: "De dag staat op een order, maar met een ander aantal uren of een ander tarief dan gefactureerd is.",
        O_ONK: "De onkosten staan op een order, maar voor een ander bedrag dan gefactureerd is.",
        O_DUBBEL: "Extra post uit het oordeel van de gebruiker: dezelfde kilometers staan op twee orders en zijn twee "
        "keer gefactureerd.",
    }


def toewijzing_uitleg(inst: Instellingen) -> dict[str, str]:
    """Wat elke toewijzing betekent, voor blok 3 van de Stand: een waarneming en wat het voorstel van de tool is. Of
    de opdrachtgever het eens is, weet de tool niet."""
    return {
        OW: f"Gefactureerd en niet op een order gevonden. Voorstel: {inst.opdrachtgever} neemt het nog op.",
        OV: f"Staat vaker op een order dan er is gefactureerd. Voorstel: {inst.opdrachtgever} past de order aan.",
        CW: f"Staat op een order en is niet gefactureerd. Voorstel: {inst.bureau} factureert het nog.",
        CV: f"Is vaker gefactureerd dan er op een order staat. Voorstel: {inst.bureau} corrigeert de factuur.",
        NOG: f"Eerst intern of met {inst.opdrachtgever} uitzoeken; zie de toelichting per regel.",
    }


def _toelichting(oorzaak: str, inst: Instellingen, toezegging_naam: str) -> str:
    """De toelichting bij een verschilregel van deze oorzaak, als er geen oordeel van de gebruiker voor is."""
    return {
        O_TOEZEGGING: f"Staat op toezegging {toezegging_naam}, niet op een order. Er is geen betaling voor gevonden.",
        O_BLOK: "Geen order in de map voor dit blok.",
        O_DAG: "Deze dagen staan niet op de order van dit blok.",
        O_DAGV: f"Ga na of {inst.opdrachtgever} dit vaste bedrag per dag heeft afgesproken.",
        O_LOS: "Ga na wat dit is.",
        O_NIETGEF: "Staat op de order; er is geen urenregel die dit factureert.",
        O_UREN: "Uren of tarief verschillen tussen factuur en order; zie blad Per dag.",
        O_ONK: "Onkosten verschillen tussen factuur en order; zie blad Per dag.",
    }[oorzaak]


def _kort(oorzaak: str) -> str:
    """De oorzaak in een paar woorden, voor de regel 'waar het verschil uit bestaat' bij een periode."""
    return {
        O_TOEZEGGING: "uren op toezegging, niet op order", O_BLOK: "uren zonder order",
        O_DAG: "uren op dagen die op de order ontbreken", O_DAGV: "dagvergoeding",
        O_UREN: "uren of tarief anders dan op de order", O_ONK: "onkosten anders dan op de order",
        O_LOS: "losse onkostenpost", O_NIETGEF: "op order maar niet gefactureerd", O_DUBBEL: "kilometers dubbel",
    }[oorzaak]  # fmt: skip


@dataclass
class Kaartblok:
    """De debiteurenkaart naast de bank en de orders, voor één medewerker.

    `heel` zijn de bedragen van de hele kaart: `open` (alle open facturen), `ontvangsten` (de ontvangsten die nog niet
    aan een factuur zijn gekoppeld, negatief), `saldo` (samen) en `buiten_uren` (de open facturen die niet in de uren
    staan); `per_medewerker` zijn de open facturen per medewerker uit de uren.

    Blok B, wat er voor deze medewerker echt openstaat, bestaat uit `open` (de open facturen van deze medewerker),
    `af_ontvangen` (wat de opdrachtgever voor zijn orders betaalde en nog niet is afgeletterd), `bij_credit` (een
    creditorder die pas na het afletteren is verrekend: dat bedrag staat weer open), `af_verrekend` (het deel van
    de orders van de medewerker op een betaling dat niet is binnengekomen, omdat er een creditorder van een ander op
    die betaling staat) en `bij_niet_gefactureerd` (wat de
    opdrachtgever betaalde voor dagen die het bureau nooit factureerde). Alle vijf zijn bedragen zonder teken; de
    namen zeggen of ze erbij of eraf gaan. `echt_open` is de uitkomst, `regels` zijn de regels van het blok zoals ze
    op het blad staan (label, bedrag met teken, uitleg; de som is `echt_open`) en `controle` is hetzelfde bedrag uit de
    facturen: wat bank en orders per factuur zeggen. `eraf` is wat er van
    `echt_open` afgaat voor de netto vordering (label, bedrag zonder teken, uitleg) en `per_saldo` de uitkomst.

    `sluit` is waar als `echt_open` en `controle` minder dan 0,05 van elkaar verschillen. Is het onwaar, dan zegt de
    kaart iets anders dan bank en orders per factuur; het verschil is `echt_open - controle`. `maak` stopt daar niet
    op: het is een afgeleide controle die de gebruiker niet in zijn invoer kan herstellen.

    `aantallen` zijn de aantallen van blok A (`open`, `ontvangsten` en `buiten_uren`), `per_medewerker_aantal` het
    aantal open facturen per medewerker en `buiten_nummers` de nummers van de open facturen die niet in de uren staan.
    `volgorde` is de volgorde waarin de medewerkers voor het eerst op de kaart staan (een lege tekst staat voor de
    open facturen die niet in de uren staan): zo staan ze in blok A.
    `al_afgeletterd` is wat er op afgeletterde facturen binnenkwam: het gaat niet van de open facturen af.
    `regel_sleutels` geeft elke regel van `regels` een vaste sleutel (`R_...`), in dezelfde volgorde.

    `facturen` (blok C) heeft elke factuur van de medewerker naast wat ervoor is betaald; `ontvangsten` (blok D)
    elke ontvangst op de kaart naast de bankboeking waar hij bij hoort (`waarvan` is het deel van de medewerker)."""

    heel: dict[str, float]
    per_medewerker: pd.Series
    open: float
    af_ontvangen: float
    bij_credit: float
    bij_niet_gefactureerd: float
    echt_open: float
    controle: float
    eraf: list[tuple[str, float, str]]
    per_saldo: float
    facturen: pd.DataFrame
    ontvangsten: pd.DataFrame
    af_verrekend: float = 0.0
    regels: list[tuple[str, float, str]] = field(default_factory=list)
    sluit: bool = True
    al_afgeletterd: float = 0.0
    aantallen: dict[str, int] = field(default_factory=dict)
    per_medewerker_aantal: pd.Series = field(default_factory=lambda: pd.Series(dtype=int))
    buiten_nummers: list[str] = field(default_factory=list)
    volgorde: list[str] = field(default_factory=list)
    regel_sleutels: list[str] = field(default_factory=list)


class Controle(NamedTuple):
    """De uitkomst van een controle: sluit hij, en met welk verschil (met teken, zoals de controle het rekent)."""

    sluit: bool
    verschil: float


@dataclass
class Overzicht:
    """Alle cijfers van het overzicht van één medewerker.

    `per_dag` heeft alleen de dagen met een bedrag. `stand` heeft de vier bedragen van blok 1 van de Stand
    (`urenstaat`, `gefactureerd`, `op_orders`, `betaald`, ex btw). `verschillen` heeft één regel per week en oorzaak:
    `fact` en `order` zijn de bedragen aan beide kanten, `verschil` het verschil, `voorstel` de toewijzing die de
    tool voorstelt, `eigen` de keuze van de gebruiker uit een eerder werkboek en `toewijzing` wat geldt (de eigen keuze,
    anders het voorstel). `weken` heeft elke kalenderweek van de eerste tot de laatste werkdag, `perioden` één regel per
    blok, `orders` elke order van de medewerker met wat ervan is ontvangen, `facturen` elke factuur.
    `zonder_order` is per factuur wat zonder order is gefactureerd (blok A), `zonder_order_dag` hetzelfde per dag
    (blok B), `bank_los` de bankboekingen die niet precies bij een order passen (blok C; `None` zonder bank) en
    `toezegging` de toezegging regel voor regel (blok D; `None` zonder toezegging). `kaart` is de debiteurenkaart
    (`None` zonder kaart of zonder bank: de kaart wordt tegen de bank gelegd). `bank` heeft elke bankboeking met
    `betreft` en `opmerking` (blad Bank; `None` zonder bank)."""

    medewerker: str
    inst: Instellingen
    aansluiting: Aansluiting
    per_dag: pd.DataFrame
    stand: dict[str, float]
    verschillen: pd.DataFrame
    weken: pd.DataFrame
    perioden: pd.DataFrame
    orders: pd.DataFrame
    facturen: pd.DataFrame
    zonder_order: pd.DataFrame
    zonder_order_dag: pd.DataFrame
    bank_los: pd.DataFrame | None
    toezegging: pd.DataFrame | None
    kaart: Kaartblok | None = None
    bank: pd.DataFrame | None = None

    def per_oorzaak(self) -> pd.DataFrame:
        """Blok 2 van de Stand: per oorzaak het verschil (met teken) en het aantal regels. Alleen oorzaken die voor
        deze medewerker voorkomen, in de vaste volgorde."""
        v = self.verschillen
        g = v.groupby("oorzaak").agg(bedrag=("verschil", "sum"), regels=("oorzaak", "size"))
        g = g.reindex([o for o in OORZAKEN if o in g.index])
        return g.assign(bedrag=g.bedrag.round(2)).reset_index()

    def per_toewijzing(self) -> pd.DataFrame:
        """Blok 3 van de Stand: per toewijzing het bedrag (bruto, dus zonder teken), het aantal regels en het verschil
        met teken. Alle vijf de toewijzingen staan erin, ook als ze nul zijn. Het bruto bedrag telt niet op tot blok
        2; het verschil met teken wel."""
        v = self.verschillen
        rijen = [
            (t, round(v.bedrag[v.toewijzing == t].sum(), 2), int((v.toewijzing == t).sum()),
             round(v.verschil[v.toewijzing == t].sum(), 2))
            for t in TOEWIJZINGEN
        ]  # fmt: skip
        return pd.DataFrame(rijen, columns=["toewijzing", "bedrag", "regels", "verschil"])

    def stappen(self) -> dict[str, float]:
        """Blok 1 van de Stand: bij elke regel het verschil met de regel erboven (met teken)."""
        s = self.stand
        return dict(
            urenstaat_gefactureerd=round(s["urenstaat"] - s["gefactureerd"], 2),
            gefactureerd_orders=round(s["gefactureerd"] - s["op_orders"], 2),
            orders_betaald=round(s["op_orders"] - s["betaald"], 2),
        )

    def per_saldo(self) -> dict[str, float]:
        """Blok 3 van de Stand, onder de toewijzingen: wat per saldo nog op te nemen (`opnemen`) en te factureren
        (`factureren`) is, en wat nog uit te zoeken is (`uitzoeken`)."""
        w = self.per_toewijzing().set_index("toewijzing").bedrag
        return dict(
            opnemen=round(w[OW] - w[OV], 2), factureren=round(w[CW] - w[CV], 2), uitzoeken=round(float(w[NOG]), 2)
        )

    def bank_totalen(self) -> dict[str, float] | None:
        """Blok 4 van de Stand: het bedrag van de orders incl. btw, wat ervan is ontvangen en wat nog open staat.
        `None` zonder bank."""
        if self.bank is None:
            return None
        incl, ontvangen = round(float(self.orders.incl.sum()), 2), round(float(self.orders.ontvangen.sum()), 2)
        return dict(incl=incl, ontvangen=ontvangen, open=round(incl - ontvangen, 2))

    def controles(self) -> dict[str, Controle]:
        """De controles die het werkboek als tekst toont: `C_OORZAKEN` (de oorzaken van blok 2 tellen op tot het
        verschil van blok 1), `C_ZONDER_ORDER` (blok A van Zonder order sluit op de facturen) en, met een kaart,
        `C_KAART` (de kaart sluit op bank en orders per factuur)."""
        s = self.stand
        oorzaken = round(float(self.per_oorzaak().bedrag.sum()) - float(s["gefactureerd"] - s["op_orders"]), 2)
        facturen = round(float(self.zonder_order.zonder_order.sum() - self.facturen.zonder_order.sum()), 2)
        uit = {
            C_OORZAKEN: Controle(oorzaken == 0, oorzaken),
            C_ZONDER_ORDER: Controle(abs(facturen) < 0.005, facturen),
        }
        if self.kaart is not None:
            uit[C_KAART] = Controle(self.kaart.sluit, round(self.kaart.echt_open - self.kaart.controle, 2))
        return uit

    def toezegging_bedragen(self) -> dict | None:
        """Wat van de facturen zonder order op een toezegging staat (`op_toezegging`) en wat niet (`rest`, met daarin
        `uren_rest` aan uren zonder enig stuk en `onkosten`), en de nummers van de toezeggingen. `None` zonder
        toezegging."""
        if self.toezegging is None:
            return None
        z = self.zonder_order
        voor = round(float(z.op_toezegging.sum()), 2)
        return dict(
            nummers=", ".join(dict.fromkeys(self.toezegging.toezegging)), op_toezegging=voor,
            rest=round(float(z.zonder_order.sum()) - voor, 2), uren_rest=round(float(z.bedrag_uren.sum()) - voor, 2),
            onkosten=round(float(z.onkosten.sum()), 2),
        )  # fmt: skip

    def bank_telling(self) -> dict[str, int] | None:
        """Hoeveel bankboekingen er zijn (`totaal`), hoeveel bij een order horen (`bij_order`) en hoeveel niet
        (`zonder_order`), en hoeveel er in blok C van Zonder order staan (`getoond`). `None` zonder bank."""
        if self.bank is None:
            return None
        bij = int(self.bank.orders.map(len).gt(0).sum())
        return dict(totaal=len(self.bank), bij_order=bij, zonder_order=len(self.bank) - bij, getoond=len(self.bank_los))

    def vervallen_orders(self) -> pd.DataFrame:
        """De vervallen orders waar deze medewerker op stond: ze tellen nergens mee. Met kolom `waarvan_medewerker`,
        het bedrag ex btw van de regels van deze medewerker."""
        a = self.aansluiting
        r = a.order_regels
        v = a.vervallen[a.vervallen.order.isin(r.order[r.medewerker == self.medewerker])].copy()
        eigen = r[r.medewerker == self.medewerker].groupby("order").bedrag.sum()
        v["waarvan_medewerker"] = v.order.map(eigen).fillna(0.0).round(2)
        return v.reset_index(drop=True)


def _lijst(tekst: pd.Series) -> str:
    """Alle losse namen uit een kolom waarin namen met ', ' zijn samengevoegd, zonder dubbelen."""
    return ", ".join(sorted({x for t in tekst for x in str(t).split(", ") if x}))


def _dagoordeel(r, inst: Instellingen) -> str:
    """De status van een dag in woorden voor de lezer van blad Per dag: korter dan de status uit `sluit_aan`."""
    if r.status == ONK and r.order_onkosten > r.fact_onkosten:
        return "Onkosten op order, niet gefactureerd"
    return {
        KLOPT: "Sluit aan", KM: "Sluit aan", ONK: "Onkosten niet op order", UREN: "Uren wijken af",
        ALLEEN: f"Niet gefactureerd door {inst.bureau}", CORR: "Correctie of credit bij een andere order",
        GEEN_P: "Geen order (heel blok ontbreekt)", GEEN_D: "Geen order (dag ontbreekt op de order)",
    }[r.status]  # fmt: skip


def _per_dag(a: Aansluiting) -> pd.DataFrame:
    """De dagen waar een bedrag op staat (kilometers zonder bedrag vallen weg), en de controle dat ze sluiten op de
    bronnen. Elke dag krijgt ook een `oordeel`: de status in woorden."""
    d = a.per_dag
    d = d[(d[["fact_bedrag_uren", "fact_onkosten", "order_bedrag_uren", "order_onkosten"]].abs() > 0.005).any(axis=1)]
    d = d.reset_index(drop=True)
    if d.empty:
        raise ValueError(f"{a.medewerker} heeft geen enkele dag met een bedrag")
    u = a.uren
    fout = []
    if abs(round((d.fact_bedrag_uren + d.fact_onkosten).sum(), 2) - round(u.netto.sum(), 2)) >= 0.01:
        fout.append("de urenregels")
    r = a.order_regels
    mijn = r[r.telt_mee]
    if abs((d.order_bedrag_uren + d.order_onkosten).sum() - mijn.bedrag.fillna(0).sum()) >= 0.01:
        fout.append("de orderregels")
    if fout:
        raise ValueError(f"per dag sluit niet aan op de bronnen: {' en '.join(fout)}")
    return d.assign(oordeel=[_dagoordeel(r, a.inst) for r in d.itertuples()])


def _verschilregels(pdag: pd.DataFrame, eff: pd.DataFrame, in_toezegging: dict[str, str]) -> pd.DataFrame:
    """Elk verschil per dag met zijn oorzaak. Uren worden per dag over alle orders samen vergeleken: een correctie- of
    creditorder hoort bij de order die hij corrigeert. Onkosten worden per dag en order vergeleken."""
    onk = eff[eff.post == "onkosten"]
    vaak = onk.netto.round(2).value_counts()
    rijen = []  # week, periode, oorzaak, datum, fact, order_bedrag, facturen, orders
    for dag, g in pdag.groupby("datum"):
        if round(g.fact_bedrag_uren.sum() - g.order_bedrag_uren.sum(), 2):
            r = g.iloc[0]
            orders = samenvoegen(g.order[g.order != GEEN])
            oorzaak = (
                O_NIETGEF if not g.urenstaten.ne("").any()
                else O_UREN if orders
                else O_BLOK if (g.status == GEEN_P).all()
                else O_DAG
            )  # fmt: skip
            if oorzaak in (O_BLOK, O_DAG) and r.week in in_toezegging:
                oorzaak = O_TOEZEGGING
            rijen.append(
                (r.week, r.periode, oorzaak, dag, g.fact_bedrag_uren.sum(), g.order_bedrag_uren.sum(),
                 _lijst(g.facturen), orders or GEEN)
            )  # fmt: skip
    for r in pdag.itertuples():
        if not round(r.fact_onkosten - r.order_onkosten, 2):
            continue
        if r.order_onkosten:  # wel op de order, ander bedrag: niet per regel uit te splitsen
            rijen.append((r.week, r.periode, O_ONK, r.datum, r.fact_onkosten, r.order_onkosten, r.facturen, r.order))
            continue
        for o in onk[(onk.datum == r.datum) & (onk.order == r.order)].itertuples():
            b = round(o.netto, 2)
            rijen.append(
                (r.week, r.periode, O_DAGV if vaak[b] >= DAGVERGOEDING_VANAF else O_LOS, r.datum, b, 0.0,
                 o.factuurnummer, r.order)
            )  # fmt: skip
    regels = pd.DataFrame(
        rijen, columns=["week", "periode", "oorzaak", "datum", "fact", "order_bedrag", "facturen", "orders"]
    )
    # Onkosten staan in de uren vaak op de eerste dag van de urenstaat en op de order op de dag zelf. Staan in één week
    # onkosten aan beide kanten op verschillende dagen, dan is het één post: samen vergelijken, en alleen het saldo
    # van de week telt.
    for w in regels.week[regels.oorzaak == O_ONK].unique():
        regels.loc[(regels.week == w) & (regels.oorzaak == O_LOS), "oorzaak"] = O_ONK
    return regels


def saldo_bestand(medewerker: str) -> str:
    """De naam van het bestand met het eigen oordeel van een medewerker, zoals een melding hem noemt."""
    return f"saldo {in_naam(medewerker)}.csv"


def _begin_van_week(week: str, medewerker: str) -> pd.Timestamp:
    """De maandag van de week in een weeksleutel als '2025 wk 14' (of '2025 wk 14-15': de eerste week telt). De
    periode van een extra post volgt uit die datum, zodat blokken, week 53 en maanden overal hetzelfde gaan."""
    m = re.fullmatch(r"(\d{4}) wk (\d{1,2})(?:-\d{1,2})?", week.strip())
    try:
        if m:
            return pd.Timestamp.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:  # een weeknummer dat in dat jaar niet bestaat
        pass
    raise ValueError(
        f"{saldo_bestand(medewerker)}: '{week}' is geen week zoals '2025 wk 14' of een week die niet bestaat"
    )


def _verschillen(
    pdag: pd.DataFrame,
    eff: pd.DataFrame,
    inst: Instellingen,
    in_toezegging: dict[str, str],
    toezegging_naam: str,
    maanden_toezegging: set[str],
    saldo: pd.DataFrame | None,
    eigen: dict[tuple[str, str], str],
    medewerker: str,
) -> pd.DataFrame:
    """Het blad Verschillen: één regel per week en oorzaak, met voorstel, toelichting en de keuze van de gebruiker.

    Een oordeel uit `saldo <naam>.csv` gaat voor het voorstel: zonder bedragen is het een andere toewijzing (en
    toelichting) voor de verschillen van die week, met bedragen is het een extra post. Een eigen keuze uit een eerder
    werkboek gaat daar weer voor."""
    regels = _verschilregels(pdag, eff, in_toezegging)
    rijen = []  # week, periode, oorzaak, dagen, fact, order, voorstel, toelichting, facturen, orders
    for (w, p, oorzaak), g in regels.groupby(["week", "periode", "oorzaak"]):
        if not round(g.fact.sum() - g.order_bedrag.sum(), 2):
            continue  # binnen de week tegen elkaar weggevallen (uren op een andere dag geboekt)
        toel = _toelichting(oorzaak, inst, toezegging_naam)
        if oorzaak == O_DAGV:
            toel += (
                " " + ", ".join(f"{n} x {nl_bedrag(b)}" for b, n in g.fact.value_counts().sort_index().items()) + "."
            )
            if set(g.datum.dt.strftime("%Y-%m")) & maanden_toezegging:
                toel += (
                    f" Op toezegging {toezegging_naam.split(' van ')[0]} staan over deze maand ook reiskosten "
                    "(blad Zonder order, blok D)."
                )
        dagen = ", ".join(sorted(set(g.datum.dt.strftime("%d-%m")), key=lambda s: s[3:] + s[:2]))
        rijen.append(
            [w, p, oorzaak, dagen, round(g.fact.sum(), 2), round(g.order_bedrag.sum(), 2), VOORSTEL[oorzaak], toel,
             _lijst(g.facturen), samenvoegen(g.orders[g.orders != GEEN])]
        )  # fmt: skip
    if saldo is not None:
        bestand = saldo_bestand(medewerker)
        for x in saldo.fillna({"toewijzing": "", "toelichting": ""}).itertuples():
            regel = x.Index + 2  # de kopregel is regel 1
            if x.toewijzing not in TOEWIJZINGEN:
                raise ValueError(
                    f"{bestand}: regel {regel}, week {x.week}: toewijzing '{x.toewijzing}' is geen van: "
                    f"{', '.join(TOEWIJZINGEN)}"
                )
            if pd.notna(x.gefactureerd) or pd.notna(x.order):  # een extra post: bedragen zonder regel in de uren
                begin = _begin_van_week(x.week, medewerker)
                rijen.append(
                    [x.week, inst.periode(begin), O_DUBBEL, "",
                     float(x.gefactureerd if pd.notna(x.gefactureerd) else 0),
                     float(x.order if pd.notna(x.order) else 0), x.toewijzing, x.toelichting, "", ""]
                )  # fmt: skip
                continue
            soort = str(x.soort).strip().capitalize()
            if soort not in ("Uren", "Onkosten"):
                raise ValueError(f"{bestand}: regel {regel}, kolom soort: '{x.soort}' is Uren of Onkosten")
            soorten = (O_TOEZEGGING, O_BLOK, O_DAG, O_UREN, O_NIETGEF) if soort == "Uren" else (O_DAGV, O_LOS, O_ONK)
            gevonden = [r for r in rijen if r[0] == x.week and r[2] in soorten]
            if not gevonden:
                raise ValueError(f"{bestand}: regel {regel}: er is in {x.week} geen verschil van de soort {soort}")
            for r in gevonden:
                r[6], r[7] = x.toewijzing, x.toelichting
    rijen.sort(key=lambda r: (r[0][:10], OORZAKEN.index(r[2]), r[0]))
    kolommen = ["week", "periode", "oorzaak", "dagen", "fact", "order", "voorstel", "toelichting", "facturen", "orders"]
    v = pd.DataFrame(rijen, columns=kolommen)
    v["verschil"] = (v.fact - v.order).round(2)
    v["bedrag"] = v.verschil.abs()
    v["eigen"] = [eigen.get((r.week, r.oorzaak), "") for r in v.itertuples()]
    onbekend = v.eigen[~v.eigen.isin(("", *TOEWIJZINGEN))]
    if len(onbekend):
        raise ValueError(f"eigen keuze '{onbekend.iloc[0]}' is geen van: {', '.join(TOEWIJZINGEN)}")
    v["toewijzing"] = v.eigen.where(v.eigen != "", v.voorstel)
    return v[
        ["week", "periode", "oorzaak", "dagen", "fact", "order", "toewijzing", "toelichting", "facturen", "orders",
         "verschil", "bedrag", "voorstel", "eigen"]
    ]  # fmt: skip


def _geldende_urenstaat(u: pd.DataFrame) -> pd.Series:
    """Het bedrag van de goedgekeurde urenstaten per week. Factuurregels staan soms dubbel in de export: een regel die
    in alles gelijk is, telt één keer."""
    uniek = ["urenstaat", "datum", "uren", "soort", "factuurnummer", "bedrag", "factuurbedrag"]
    goed = u[u.status == GOEDGEKEURD].drop_duplicates(uniek)
    return goed.groupby("week").factuurbedrag.sum()


def _weken(
    u: pd.DataFrame,
    pdag: pd.DataFrame,
    in_toezegging: dict[str, str],
    afwijkend: pd.DataFrame | None,
    inst: Instellingen,
) -> pd.DataFrame:
    """Elke kalenderweek van de eerste tot de laatste dag met een bedrag: urenstaat, factuur en order naast elkaar,
    met een status.

    De goedgekeurde urenstaat van een week is wat gefactureerd is, tenzij `urenstaat-afwijkend` er een eigen bedrag
    voor heeft (bijvoorbeeld een dag op twee goedgekeurde urenstaten). Een week waar beide verschillen en die niet in
    dat bestand staat, is een fout in de invoer."""
    geldend = _geldende_urenstaat(u)
    eigen = (
        afwijkend.drop_duplicates("week", keep="last").set_index("week")
        if afwijkend is not None
        else pd.DataFrame(columns=["bedrag", "toelichting"])
    )
    rijen, onverklaard = [], []
    ma = pdag.datum.min() - pd.Timedelta(days=pdag.datum.min().weekday())
    while ma <= pdag.datum.max():
        w = inst.week(ma)
        g, alles = pdag[pdag.week == w], u[u.week == w]
        ew, goed = alles[alles.telt_mee], alles[alles.status == GOEDGEKEURD]
        dagen = g.datum if len(g) else goed.datum if len(goed) else pd.Series([ma, ma + pd.Timedelta(days=4)])
        fact_dag, order_dag = g.fact_bedrag_uren + g.fact_onkosten, g.order_bedrag_uren + g.order_onkosten
        s, p = fact_dag.sum(), order_dag.sum()
        zonder = fact_dag[g.order == GEEN].sum()
        verlof = samenvoegen(goed.soort[(goed.uren > 0) & (goed.soort != NORMALE_UREN)])
        if not len(g):
            status, opm = (
                (W_NIET_GEWERKT, verlof)
                if len(goed)
                else (W_GEEN_URENSTAAT, "Geen urenstaat en geen order in deze week")
            )
        else:
            uren_gelijk = abs((g.fact_bedrag_uren - g.order_bedrag_uren).sum()) < 0.005
            status = (
                W_NIET_GEF if abs(s) < 0.005
                else W_GEEN if abs(zonder - s) < 0.005
                else W_DEELS if zonder > 0.005
                else W_AANSLUIT if abs(s - p) < 0.005
                else W_ONKOSTEN if uren_gelijk and g.fact_onkosten.sum() > g.order_onkosten.sum()
                else W_AFWIJKT
            )  # fmt: skip
            opm = (
                "Geen urenstaat in de uren, wel op een order" if abs(s) < 0.005 else f"Ook: {verlof}" if verlof else ""
            )
        if w in in_toezegging:  # staan de uren ook op een order, dan staat de week dubbel bij de opdrachtgever
            dubbel = g.order_bedrag_uren.sum() > 0.005
            nr = in_toezegging[w]
            extra = f"Ook op toezegging {nr}: dubbel" if dubbel else f"Op toezegging {nr}, niet op een order"
            opm = "; ".join(x for x in (opm, extra) if x)
        urenstaat_bedrag = round(float(eigen.bedrag.get(w, geldend.get(w, 0))), 2)
        if w in eigen.index:
            opm = eigen.toelichting[w]
        elif abs(urenstaat_bedrag - s) > 0.005:
            onverklaard.append(w)
        rijen.append(
            [w, g.periode.iloc[0] if len(g) else inst.periode(ma), dagen.min(), dagen.max(),
             ew.uren[ew.post == "uren"].sum(), samenvoegen(goed.urenstaat), urenstaat_bedrag,
             samenvoegen(ew.factuurnummer), samenvoegen(ew.factuurdatum.dt.strftime("%d-%m-%Y")),
             samenvoegen(g.order[g.order != GEEN]), status, opm, round(s, 2), round(p, 2)]
        )  # fmt: skip
        ma += pd.Timedelta(days=7)
    if onverklaard:
        raise ValueError(
            "de goedgekeurde urenstaat wijkt af van wat gefactureerd is in week "
            f"{', '.join(onverklaard)}, en die week staat niet in urenstaat-afwijkend: zet de week en het goedgekeurde "
            "bedrag in dat bestand, of controleer de uren"
        )
    kolommen = [
        "week", "periode", "van", "tm", "uren", "urenstaten", "urenstaat_bedrag", "facturen", "factuurdatums",
        "orders", "status", "opmerking", "fact", "order",
    ]  # fmt: skip
    return pd.DataFrame(rijen, columns=kolommen)


def _perioden(
    weken: pd.DataFrame,
    verschillen: pd.DataFrame,
    pdag: pd.DataFrame,
    oordelen: dict[str, str],
    soorten: dict[str, str],
    met_bank: bool,
) -> pd.DataFrame:
    """Eén regel per blok van de opdrachtgever: de weken opgeteld, de orders van dat blok, hun betaling en een status.
    Het verschil van een blok is dat van zijn verschilregels, zonder de extra posten voor dubbele kilometers (die
    staan niet in de uren)."""
    rijen = []
    for p in dict.fromkeys(weken.periode):
        wk = weken[weken.periode == p]
        nrs = sorted(set(pdag.order[(pdag.periode == p) & (pdag.order != GEEN)]))
        delen = {}
        for v in verschillen[(verschillen.periode == p) & (verschillen.oorzaak != O_DUBBEL)].itertuples():
            delen[v.oorzaak] = delen.get(v.oorzaak, 0) + v.verschil
        verschil = sum(delen.values())
        niet = [f"{nr}: {oordelen[nr].lower()}" for nr in nrs if soorten[nr] not in BETAALD]
        betaling = "" if not met_bank else "; ".join(niet) or ("Betaald" if nrs else "")
        status = "Sluit aan" if abs(verschil) < 0.005 else "Geen order" if not nrs else "Verschil"
        rijen.append(
            dict(
                periode=p, van=wk.van.min(), tm=wk.tm.max(), uren=wk.uren.sum(),
                urenstaat=round(wk.urenstaat_bedrag.sum(), 2), fact=round(wk.fact.sum(), 2),
                verschil_urenstaat=round(wk.urenstaat_bedrag.sum() - wk.fact.sum(), 2),
                order=round(wk.order.sum(), 2), verschil=round(wk.fact.sum() - wk.order.sum(), 2),
                orders=", ".join(nrs), betaling=betaling, status=status,
                oorzaken="; ".join(f"{_kort(o)} {nl_bedrag(delen[o])}" for o in OORZAKEN if o in delen),
            )
        )  # fmt: skip
    return pd.DataFrame(rijen)


def _betaling(
    k, a: Aansluiting, facturen: set[str], open_op_kaart: set[str] | None, inst: Instellingen
) -> tuple[list[float], pd.Timestamp | None, str, str]:
    """Wat elke rekening van een order ontving (nooit meer dan haar aandeel van deze order), de datum van de laatste
    ontvangst, het oordeel in woorden en de soort betaling (`ORDER_...`)."""
    b = a.betaald.get(k.order, {})
    aandelen = [round(k.incl * r.aandeel, 2) for r in a.rekeningen]
    # De bankexport is niet altijd compleet. Staat er niets op de bank, maar zijn alle facturen over de dagen van deze
    # order in de boekhouding afgeletterd (ze staan niet meer als open op de debiteurenkaart), dan is de order betaald.
    if not b and open_op_kaart is not None and facturen and not facturen & open_op_kaart:
        tekst = f"{'Verrekend' if k.incl < 0 else 'Betaald'} volgens de boekhouding: {', '.join(sorted(facturen))} "
        tekst += "afgeletterd. Niet in de bankexport"
        return [*aandelen[:-1], round(k.incl - sum(aandelen[:-1]), 2)], None, tekst, ORDER_BOEKHOUDING
    # sluit de betaling op de cent (ook met een verrekende creditorder erin), dan telt het hele aandeel van deze order
    ontvangen = [
        (b[r.naam].deel if b[r.naam].sluit else min(b[r.naam].bedrag, b[r.naam].deel)) if r.naam in b else 0.0
        for r in a.rekeningen
    ]
    rest = round(k.incl - sum(ontvangen), 2)
    if abs(rest) < 0.015:
        soort = ORDER_VERREKEND if k.incl < 0 else ORDER_BETAALD
        tekst = "Verrekend met een betaling" if k.incl < 0 else "Betaald"
        if any(x.sluit and x.deel - x.bedrag > 0.015 for x in b.values()):  # minder geld dan de order: een creditorder
            incl = a.kop.set_index("order").incl
            cr = sorted({x for lijst in a.bankregels.orders if k.order in lijst for x in lijst if incl[x] < 0})
            tekst += f"; waarvan {nl_bedrag(-incl[cr].sum())} verrekend met creditorder {', '.join(cr)}"
    elif k.incl < 0:
        soort, tekst = ORDER_CREDIT_NIET, "Creditorder: verrekening niet gevonden in de bankexport"
    elif b:
        soort, tekst = ORDER_DEELS, f"Deels ontvangen: {nl_bedrag(rest)} ingehouden"
    else:
        vervalt = k.factuurdatum + pd.Timedelta(days=inst.betaaltermijn)
        soort, tekst = (
            (ORDER_NIET, "Niet ontvangen") if vervalt <= a.bank_tot
            else (ORDER_NOG_NIET, f"Nog niet vervallen bij einde bankexport ({a.bank_tot:%d-%m-%Y})")
        )  # fmt: skip
    return ontvangen, max(x.datum for x in b.values()) if b else None, tekst, soort


def _orders(
    a: Aansluiting, pdag: pd.DataFrame, eff: pd.DataFrame, open_op_kaart: set[str] | None
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Elke order waar de medewerker op staat, met wat ervan is ontvangen, een oordeel in woorden en de `soort`
    betaling (`ORDER_...`). Geeft de tabel en per order het oordeel. Zonder bank is er niets ontvangen en is er geen
    oordeel over de betaling."""
    regels = a.order_regels
    mijn = regels[regels.telt_mee]
    kop = a.kop[a.kop.order.isin(mijn.order)].sort_values(["factuurdatum", "order"])
    bereik = mijn.groupby("order").datum.agg(["min", "max"])
    order_dag = (pdag.order_bedrag_uren + pdag.order_onkosten).groupby(pdag.order).sum()
    fact_dag = (pdag.fact_bedrag_uren + pdag.fact_onkosten).groupby(pdag.order).sum()
    # De dagen van een order volgens `per_dag`: daar staat een verzamelorder al op één datum.
    dagen_van = a.per_dag[a.per_dag.entiteit != ""].groupby("order").datum.agg(set)
    rijen, oordelen = [], {}
    for k in kop.itertuples():
        if a.bankregels is None:
            ontvangen, betaald_op, tekst = [0.0] * len(a.rekeningen), None, "Geen bank in het dossier"
            soort = ORDER_GEEN_BANK
        else:
            facturen = set(eff.factuurnummer[eff.datum.isin(dagen_van.get(k.order, set()))].dropna())
            ontvangen, betaald_op, tekst, soort = _betaling(k, a, facturen, open_op_kaart, a.inst)
        oordelen[k.order] = tekst
        eigen = float(order_dag.get(k.order, 0.0))
        totaal = sum(ontvangen)
        rij = dict(
            order=k.order, entiteit=k.entiteit, factuurdatum=k.factuurdatum, van=bereik["min"][k.order],
            tm=bereik["max"][k.order], excl=k.excl, waarvan_medewerker=round(eigen, 2),
            anderen=round(k.excl - eigen, 2), incl=k.incl,
        )  # fmt: skip
        for r, bedrag in zip(a.rekeningen, ontvangen, strict=True):
            rij[f"ontvangen {r.naam}"] = bedrag
        rij |= dict(
            ontvangen=round(totaal, 2), open=round(k.incl - totaal, 2), betaald_op=betaald_op, oordeel=tekst,
            soort=soort,
            betaald_ex=0.0 if k.incl == 0 else round(eigen * totaal / k.incl, 2),
            gefactureerd=round(float(fact_dag.get(k.order, 0.0)), 2),
            verschil=round(float(fact_dag.get(k.order, 0.0)) - eigen, 2),
            bank_omschrijving="" if a.bankregels is None else samenvoegen(
                a.bankregels.omschrijving[a.bankregels.orders.map(lambda lijst, nr=k.order: nr in lijst)]
            ),
        )  # fmt: skip
        rijen.append(rij)
    kolommen = [
        "order", "entiteit", "factuurdatum", "van", "tm", "excl", "waarvan_medewerker", "anderen", "incl",
        *[f"ontvangen {r.naam}" for r in a.rekeningen],
        "ontvangen", "open", "betaald_op", "oordeel", "soort", "betaald_ex", "gefactureerd", "verschil",
        "bank_omschrijving",
    ]  # fmt: skip
    return pd.DataFrame(rijen, columns=kolommen), oordelen  # een medewerker zonder orders: lege tabel met kolommen


def _facturen(u: pd.DataFrame) -> pd.DataFrame:
    """Elke factuur van de medewerker: omzet, credit, netto en wat daarvan zonder order is gefactureerd."""
    heeft = u.factuurnummer.notna() & u.factuurnummer.ne("")
    rijen = []
    for nr, g in sorted(u[heeft].groupby("factuurnummer"), key=lambda t: (t[1].factuurdatum.iloc[0], t[0])):
        netto = g.netto.sum()
        wacht = (g.factuurdatum.iloc[0] - g.datum.max()).days
        opm = (["volledig gecrediteerd"] if abs(netto) < 0.005 else []) + (
            ["meer dan drie maanden na de werkweek gefactureerd"] if wacht > 92 else []
        )
        rijen.append(
            dict(
                factuur=nr, factuurdatum=g.factuurdatum.iloc[0], weken=samenvoegen(g.week),
                urenstaten=samenvoegen(g.urenstaat), omzet=round(g.bedrag.sum(), 2),
                credit=round(g.creditbedrag.sum(), 2), creditfacturen=samenvoegen(g.creditnummer),
                netto=round(netto, 2), zonder_order=round(g.netto[g.order == GEEN].sum(), 2),
                dagen_na_werkdag=wacht, opmerking="; ".join(opm),
            )
        )  # fmt: skip
    kolommen = [
        "factuur", "factuurdatum", "weken", "urenstaten", "omzet", "credit", "creditfacturen", "netto", "zonder_order",
        "dagen_na_werkdag", "opmerking",
    ]  # fmt: skip
    return pd.DataFrame(rijen, columns=kolommen)


def _controleer_status(a: Aansluiting) -> None:
    """De geldende urenstaat heeft de status `Goedgekeurd`, letterlijk zo. Heeft de medewerker geen enkele regel met
    die status, dan is het bijna zeker een andere schrijfwijze in de export, en dat is iets anders dan een week die
    afwijkt."""
    statussen = a.uren.status.dropna().astype(str)
    if (statussen == GOEDGEKEURD).any():
        return
    aanwezig = ", ".join(f"'{x}'" for x in sorted(set(statussen))) or "geen"
    raise ValueError(
        f"{a.medewerker}: geen enkele urenregel heeft de status {GOEDGEKEURD}. In de uren staat: {aanwezig}. De tool "
        f"rekent alleen met urenstaten met de status {GOEDGEKEURD}; zet die status in uren.csv (bij het omzetten van "
        "een export met een andere schrijfwijze: in omzetten.py)"
    )


def _controleer_factuurnummers(a: Aansluiting) -> None:
    """Elke urenregel met een bedrag heeft een factuurnummer. Zonder nummer is de regel niet aan een factuur te hangen
    en zou de omzet stil uit de facturen vallen."""
    u = a.uren
    zonder = u[(u.bedrag.abs() > 0.005) | (u.netto.abs() > 0.005)]
    zonder = zonder[zonder.factuurnummer.isna() | zonder.factuurnummer.eq("")]
    if zonder.empty:
        return
    rijen = [str(int(r)) for r in zonder.rij]
    genoemd = ", ".join(rijen[:5]) + (f" en {len(rijen) - 5} andere" if len(rijen) > 5 else "")
    raise ValueError(
        f"{a.medewerker}: {len(rijen)} urenregel{'s' if len(rijen) > 1 else ''} met een bedrag maar zonder "
        f"factuurnummer (rij {genoemd}). Vul het factuurnummer in, of zet het bedrag op 0 als de regel niet is "
        "gefactureerd"
    )


def _notitie(r, notities: pd.DataFrame | None, voornaam: str) -> tuple[str, str]:
    """Betreft deze bankboeking deze medewerker, en de opmerking erbij. Een eigen opmerking uit `bank-notities`
    gaat voor: `betreft` noemt daar de voornaam of voornamen om wie het gaat. Hoort de boeking bij een order, dan
    beslist de order."""
    x = None
    if notities is not None:
        x = next((n for n in notities.itertuples() if n.tekst in r.omschrijving), None)
    if x is None:
        return r.betreft, r.opmerking
    wie = str(x.betreft)
    if r.orders:
        betreft = r.betreft
    elif voornaam in [w.strip() for w in wie.split(",")]:
        betreft = "ja"
    elif wie.startswith(("onbekend", "waarschijnlijk")):
        betreft = wie
    else:
        betreft = f"nee ({wie})"
    return betreft, x.opmerking


def _bank_los(a: Aansluiting, notities: pd.DataFrame | None) -> pd.DataFrame | None:
    """Blok C: de bankboekingen die niet precies bij een order passen en misschien wel de medewerker betreffen.
    `None` zonder bank. Boekingen waarvan de opmerking zegt dat ze een ander betreffen, vallen weg."""
    if a.bankregels is None:
        return None
    voornaam = a.medewerker.split()[0]
    rijen = []
    for r in a.bankregels.itertuples():
        if r.orders and abs(r.rest) < 0.015:
            continue
        betreft, opmerking = _notitie(r, notities, voornaam)
        if betreft.startswith("nee"):
            continue
        rijen.append(
            dict(
                datum=r.datum, rekening=r.rekening, omschrijving=r.omschrijving, bedrag=r.bedrag,
                orders=", ".join(r.orders), betreft=betreft, opmerking=opmerking,
            )
        )  # fmt: skip
    return pd.DataFrame(
        rijen, columns=["datum", "rekening", "omschrijving", "bedrag", "orders", "betreft", "opmerking"]
    )


def _bank(a: Aansluiting, notities: pd.DataFrame | None) -> pd.DataFrame | None:
    """Het blad Bank: elke bankboeking met de order waar hij bij hoort, en of hij de medewerker betreft, met de eigen
    opmerking uit `bank-notities`. `None` zonder bank."""
    if a.bankregels is None:
        return None
    voornaam = a.medewerker.split()[0]
    uit = [_notitie(r, notities, voornaam) for r in a.bankregels.itertuples()]
    return a.bankregels.assign(betreft=[x[0] for x in uit], opmerking=[x[1] for x in uit])


def _order_bij(w: str, pdag: pd.DataFrame) -> str:
    """Welke order er wel is in de buurt van een week zonder order: in dezelfde week, anders in hetzelfde blok."""
    wel = pdag.order != GEEN
    in_week = sorted(set(pdag.order[(pdag.week == w) & wel]))
    in_blok = sorted(set(pdag.order[(pdag.periode == pdag.periode[pdag.week == w].iloc[0]) & wel]))
    if in_week:
        return f"Geen. {', '.join(in_week)} bevat de rest van deze week, niet deze dag"
    if in_blok:
        return f"Geen. {', '.join(in_blok)} is van hetzelfde blok maar bevat deze week niet"
    return "Geen order voor dit blok"


def _zonder_order(
    a: Aansluiting,
    u: pd.DataFrame,
    eff: pd.DataFrame,
    pdag: pd.DataFrame,
    in_toezegging: dict[str, str],
    stukken: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Blok A (per factuur) en blok B (per dag): wat het bureau heeft gefactureerd zonder dat het op een order staat.

    Per dag staan de uren, de onkosten (met de soort: een vast bedrag per dag of een losse post) en wat de
    medewerker die dag zelf aan kilometers bij de opdrachtgever boekte. Uren in een week die op een toezegging staat,
    staan er ook apart bij: `op_toezegging`. Per factuur komt daar het andere schriftelijke stuk van de opdrachtgever
    uit `zonder-order` bij. Ontvangen is altijd nul: een betaling hoort bij een order en deze dagen hebben er geen."""
    z = eff[eff.order == GEEN]
    vaak = eff[eff.post == "onkosten"].netto.round(2).value_counts()
    r = a.order_regels
    km = r[r.telt_mee & (r.eenheid == KILOMETERS)].groupby("datum").aantal.sum()
    toel = (
        stukken.groupby("week")[["stuk", "toelichting"]].agg(lambda s: " ".join(x for x in s if x))
        if stukken is not None
        else pd.DataFrame(columns=["stuk", "toelichting"])
    )
    facturen = sorted(z.groupby("factuurnummer"), key=lambda t: (t[1].datum.min(), t[0]))
    dag_rijen = []
    for nr, f in facturen:
        for dag, g in f.groupby("datum"):
            uren, onk = g[g.post == "uren"], g[g.post == "onkosten"]
            dag_rijen.append(
                dict(
                    factuur=nr, datum=dag, week=g.week.iloc[0], urenstaten=samenvoegen(g.urenstaat),
                    uren=uren.uren.sum(), bedrag_uren=round(uren.netto.sum(), 2), onkosten=round(onk.netto.sum(), 2),
                    uurtarief=uren.tarief.iloc[0] if len(uren) else None,
                    soort_onkosten=", ".join(
                        sorted({"dagvergoeding" if vaak[round(b, 2)] >= DAGVERGOEDING_VANAF else "losse post"
                                for b in onk.netto})
                    ),
                    km_bij_opdrachtgever=km.get(dag), rij=", ".join(str(int(x)) for x in sorted(g.rij)),
                    op_toezegging=round(uren.netto.sum(), 2) if g.week.iloc[0] in in_toezegging else 0.0,
                )
            )  # fmt: skip
    kolommen_b = [
        "factuur", "datum", "week", "urenstaten", "uren", "bedrag_uren", "onkosten", "totaal", "uurtarief",
        "soort_onkosten", "km_bij_opdrachtgever", "rij", "op_toezegging",
    ]  # fmt: skip
    b = pd.DataFrame(dag_rijen, columns=[k for k in kolommen_b if k != "totaal"])
    b.insert(7, "totaal", (b.bedrag_uren + b.onkosten).round(2))
    rijen = []
    for nr, f in facturen:
        wkn = sorted(set(f.week))
        kms = [f"{km[d]:g} km op {d:%d-%m}" for d in km.index if a.inst.week(d) in wkn]
        dagen_f = b[b.factuur == nr]
        rijen.append(
            dict(
                factuur=nr, factuurdatum=f.factuurdatum.iloc[0], weken=", ".join(wkn),
                dagen=", ".join(d.strftime("%d-%m") for d in sorted(set(f.datum))), urenstaten=samenvoegen(f.urenstaat),
                uren=dagen_f.uren.sum(), bedrag_uren=round(dagen_f.bedrag_uren.sum(), 2),
                onkosten=round(dagen_f.onkosten.sum(), 2), zonder_order=round(dagen_f.totaal.sum(), 2),
                factuur_totaal=round(u.netto[u.factuurnummer == nr].sum(), 2),
                order_bij=" ".join(dict.fromkeys(_order_bij(w, pdag) for w in wkn)), ontvangen=0.0,
                ander_stuk=" ".join(dict.fromkeys(toel.stuk.get(w, "") for w in wkn)).strip(),
                km_bij_opdrachtgever=", ".join(kms) or "Geen",
                toelichting=" ".join(dict.fromkeys(toel.toelichting.get(w, "") for w in wkn)).strip(),
                op_toezegging=round(dagen_f.op_toezegging.sum(), 2),
            )
        )  # fmt: skip
    kolommen_a = [
        "factuur", "factuurdatum", "weken", "dagen", "urenstaten", "uren", "bedrag_uren", "onkosten", "zonder_order",
        "factuur_totaal", "order_bij", "ontvangen", "ander_stuk", "km_bij_opdrachtgever", "toelichting",
        "op_toezegging",
    ]  # fmt: skip
    A = pd.DataFrame(rijen, columns=kolommen_a)
    A["niet_op_toezegging"] = (A.zonder_order - A.op_toezegging).round(2)
    return A, b


def _toezegging_blok(
    a: Aansluiting,
    t: pd.DataFrame,
    eff: pd.DataFrame,
    pdag: pd.DataFrame,
    oordelen: dict[str, str],
) -> pd.DataFrame | None:
    """Blok D: de toezegging regel voor regel, naast de orders en de facturen over dezelfde week of maand.

    Een weekregel krijgt een soort: alleen op de toezegging, ook op een order (dubbel: de week staat dan twee keer bij
    de opdrachtgever), of zonder bedrag (geen enkel stuk voor die week). Een reiskostenregel krijgt de kilometers per
    dag en wordt naast de dagvergoedingen en de kilometers op orders over dezelfde maand gelegd. `None` zonder
    toezegging."""
    if t.empty:
        return None
    onk = eff[eff.post == "onkosten"]
    vaak = onk.netto.round(2).value_counts()
    r = a.order_regels
    km_alle = r[r.telt_mee & (r.eenheid == KILOMETERS)]
    rijen = []
    for x in t.itertuples():
        orders, op_order = [], None
        if x.week:
            g = pdag[pdag.week == x.week]
            op_order = round(g.order_bedrag_uren.sum(), 2)
            orders = sorted(set(g.order[(g.order_bedrag_uren.abs() > 0.005) & (g.order != GEEN)]))
            fact_uren, fact_onk = round(g.fact_bedrag_uren.sum(), 2), round(g.fact_onkosten.sum(), 2)
            facturen = _lijst(g.facturen) or "geen factuur"
            gefactureerd = f"{facturen}: uren {nl_bedrag(fact_uren)}, onkosten {nl_bedrag(fact_onk)}"
            if pd.isna(x.bedrag):
                soort = T_LEEG
                tekst = (
                    "De week staat op de toezegging zonder uren en zonder bedrag. "
                    + zin(f"{a.inst.bureau} factureerde {nl_bedrag(fact_uren)}. ")
                    + "Voor deze week is er geen order en geen bedrag op de toezegging."
                )
            elif op_order > 0.005:
                soort = T_DUBBEL
                eerste = "; ".join(oordelen[nr].split(";")[0].lower() for nr in orders)
                tekst = (
                    f"DUBBEL: deze week staat ook op order {', '.join(orders)} ({eerste}). "
                    "Ga na of dit niet twee keer wordt opgenomen."
                )
            else:
                soort = T_ALLEEN
                tekst = "Alleen op deze toezegging, niet op een order."
            if pd.notna(x.bedrag) and abs(x.bedrag - fact_uren) > 0.005:
                tekst += f" Let op: {a.inst.bureau} factureerde {nl_bedrag(fact_uren)} aan uren, de toezegging noemt "
                tekst += f"{nl_bedrag(x.bedrag)}."
        else:
            soort = T_REIS
            m = re.search(r"(\d+)\s+dagen", x.omschrijving)
            dagen = int(m.group(1)) if m else None
            tekst = ""
            if dagen and pd.notna(x.aantal) and pd.notna(x.bedrag):
                tekst = f"{x.aantal / dagen:g} km per dag = {nl_bedrag(x.bedrag / dagen)} per dag. "
            if x.maand:
                in_maand = lambda s, m=x.maand: s.dt.strftime("%Y-%m") == m  # noqa: E731
                o, k = onk[in_maand(onk.datum)], km_alle[in_maand(km_alle.datum)]
                dv = o[o.netto.round(2).map(vaak) >= DAGVERGOEDING_VANAF]
                gefactureerd = (
                    f"{len(dv)} x dagvergoeding, samen {nl_bedrag(dv.netto.sum())}" if len(dv)
                    else "geen dagvergoeding gefactureerd"
                )  # fmt: skip
                if len(o) > len(dv):
                    los = nl_bedrag(o.netto.sum() - dv.netto.sum())
                    gefactureerd += f" (daarnaast een losse post van {los}, geen reiskosten)"
                werkdagen = eff.datum[(eff.post == "uren") & in_maand(eff.datum)].nunique()
                tekst += f"Gewerkte dagen volgens de uren: {werkdagen}. " + (
                    f"Op orders staan over deze maand ook kilometers: {k.aantal.sum():g} km voor "
                    f"{nl_bedrag(k.bedrag.sum())} ({samenvoegen(k.order)}). Mogelijk dubbel."
                    if len(k)
                    else "Op orders staan over deze maand geen kilometers."
                )
            else:
                gefactureerd, tekst = "", tekst + "Geen maand genoemd."
        rijen.append(
            dict(
                toezegging=x.nr, datum=x.datum, week_of_maand=x.week or x.maand, soort=soort,
                omschrijving=x.omschrijving, eenheid=x.eenheid, aantal=x.aantal, prijs=x.prijs, bedrag=x.bedrag,
                op_order=", ".join(orders), uren_op_order=op_order, gefactureerd=gefactureerd, betekenis=tekst,
            )
        )  # fmt: skip
    return pd.DataFrame(rijen)


def _toezegging_van(d: Dossier, medewerker: str) -> pd.DataFrame:
    """De regels van de toezegging van deze medewerker (leeg als er geen zijn)."""
    if d.toezegging is None:
        return pd.DataFrame(columns=inlezen.TOEZEGGING)
    return d.toezegging[d.toezegging.medewerker == medewerker].reset_index(drop=True)


# ---------- de debiteurenkaart ----------

KLEIN = 5.0  # een verschil op een afgeletterde factuur onder dit bedrag (incl. btw) is afronding of btw, geen post
AFRONDING = 0.015
CONTROLE_VERSCHIL = 0.05  # zo veel mag "echt open" afwijken van wat bank en orders per factuur zeggen


class _Verdeling(NamedTuple):
    """Wat de opdrachtgever voor de orders betaalde, verdeeld over de facturen van het bureau."""

    ontvangen: pd.Series  # per factuur
    orders: pd.Series  # per factuur: de orders waar de betaling bij hoort, samengevoegd
    dagen: pd.DataFrame  # per dag en factuur het bedrag: `datum`, `factuurnummer`, `n`
    weken: pd.DataFrame  # hetzelfde per week: `wk`, `factuurnummer`, `n`
    zonder_factuur: float  # betaald voor weken waarin het bureau helemaal niets factureerde


def _cent(x: float) -> float:
    """Een bedrag als gewoon getal op de cent (pandas geeft numpy-getallen terug)."""
    return round(float(x), 2)


def _heeft_factuur(u: pd.DataFrame) -> pd.Series:
    """Welke urenregels op een factuur staan (het factuurnummer is niet leeg)."""
    return u.factuurnummer.notna() & u.factuurnummer.ne("")


def _incl_van(a: Aansluiting) -> pd.Series:
    """Het bedrag incl. btw van elke order, ook van de vervallen."""
    return pd.Series(
        {
            **dict(zip(a.vervallen.order, a.vervallen.incl, strict=True)),
            **dict(zip(a.kop.order, a.kop.incl, strict=True)),
        }
    )


def _btw_op_onkosten(a: Aansluiting, incl_van: pd.Series) -> dict[str, float]:
    """Per order met onkosten de factor die onkosten ex btw tot incl. btw maakt, afgeleid uit het ordertotaal: de
    btw op onkosten hoeft niet gelijk te zijn aan die op uren."""
    r = a.order_regels
    uren = r[r.post == "uren"].groupby("order").bedrag.sum()
    onkosten = r[r.post == "onkosten"].groupby("order").bedrag.sum()
    return {
        nr: round((incl_van[nr] - (1 + a.inst.btw) * uren.get(nr, 0.0)) / som, 2)
        for nr, som in onkosten.items()
        if abs(som) > 0.005
    }


def _orderbedragen(
    a: Aansluiting, pdag: pd.DataFrame, orders: pd.DataFrame, incl_van: pd.Series
) -> tuple[pd.DataFrame, pd.Series]:
    """Per dag en order van de medewerker wat de order incl. btw is (`order_incl`) en wat daarvan is ontvangen
    (`ontv`), en per order het deel van het ordertotaal dat van de medewerker is."""
    factor = pdag.order.map(_btw_op_onkosten(a, incl_van)).fillna(1.0)
    ok = pdag.assign(order_incl=pdag.order_bedrag_uren * (1 + a.inst.btw) + pdag.order_onkosten * factor)
    if orders.empty:
        return ok.assign(ontv=0.0), pd.Series(dtype=float)
    per_order = orders.set_index("order")
    incl = per_order.incl.where(per_order.incl != 0)
    deel = (per_order.ontvangen / incl).fillna(0.0)  # het deel van een order dat is ontvangen
    ok["ontv"] = ok.order_incl * ok.order.map(deel).fillna(0.0)
    return ok, ok.groupby("order").order_incl.sum() / incl


def _met_aandeel(df: pd.DataFrame, per: list[str], open_op_kaart: set[str]) -> pd.DataFrame:
    """Het aandeel van elke regel in het bedrag van zijn dag of week (`per`), naar het gefactureerde bedrag.

    Staan er in één groep facturen die al zijn afgeletterd (niet meer op de kaart) naast facturen die nog op de kaart
    staan, dan hoort de ontvangst bij de afgeletterde: de kaart laat zien dat daar geen betaling op staat die nog moet
    worden afgeletterd. Staan ze er allemaal of geen enkele op, dan blijft het naar bedrag."""
    groep = [df[k] for k in per]
    afgeletterd = df.n.where(~df.factuurnummer.isin(open_op_kaart), 0.0)
    gewicht = df.n.where(afgeletterd.abs().groupby(groep).transform("sum") < 0.005, afgeletterd)
    som = gewicht.groupby(groep).transform("sum")
    return df.assign(aandeel=(gewicht / som.where(som.abs() > 0.005)).fillna(0.0))


def _verdeel_ontvangsten(
    inst: Instellingen, eff: pd.DataFrame, ok: pd.DataFrame, open_op_kaart: set[str]
) -> _Verdeling:
    """Verdeelt wat voor een order is ontvangen over de facturen van de dagen van die order. Staan er op één dag twee
    facturen, dan naar het gefactureerde bedrag, met voorrang voor de facturen die niet meer op de kaart staan (zie
    `_met_aandeel`). Een correctie- of creditorder heeft zelf geen factuur: wat erop is
    ontvangen hoort bij de facturen van die dag, en anders bij die van dezelfde week (onkosten staan in de uren soms
    op een andere dag dan op de order)."""
    fe = eff[_heeft_factuur(eff)]
    lijn = fe.groupby(["datum", "order", "factuurnummer"]).netto.sum().rename("n").reset_index()
    lijn = _met_aandeel(lijn, ["datum", "order"], open_op_kaart).merge(
        ok[["datum", "order", "ontv"]], on=["datum", "order"]
    )
    los = ok[ok.facturen == ""].groupby("datum").agg(ontv=("ontv", "sum"), order=("order", ", ".join)).reset_index()
    dagen = fe.groupby(["datum", "factuurnummer"]).netto.sum().rename("n").reset_index()
    per_dag = _met_aandeel(dagen, ["datum"], open_op_kaart).merge(los, on="datum")
    los_w = los[~los.datum.isin(dagen.datum)].assign(wk=lambda x: weeksleutels(x.datum, inst))
    los_w = los_w.groupby("wk").agg(ontv=("ontv", "sum"), order=("order", ", ".join)).reset_index()
    weken = fe.assign(wk=weeksleutels(fe.datum, inst)).groupby(["wk", "factuurnummer"]).netto.sum().rename("n")
    weken = weken.reset_index()
    per_week = _met_aandeel(weken, ["wk"], open_op_kaart).merge(los_w, on="wk")
    alles = pd.concat([x for x in (lijn, per_dag, per_week) if len(x)], ignore_index=True)
    orders = alles.groupby("factuurnummer").order.agg(
        lambda x: ", ".join(sorted({nr for t in x for nr in t.split(", ")} - {GEEN}))
    )
    ontvangen = (alles.ontv * alles.aandeel).groupby(alles.factuurnummer).sum().round(2)
    return _Verdeling(ontvangen, orders, dagen, weken, _cent(los_w.ontv[~los_w.wk.isin(weken.wk)].sum()))


def _oordeel_factuur(
    x, a: Aansluiting, incl_van: pd.Series, oordelen: dict[str, str], in_toezegging: dict[str, str]
) -> str:
    """Het oordeel over een factuur van de kaart in woorden. Een factuur die niet meer op de kaart staat is afgeletterd
    (betaald volgens de boekhouding); dan telt of dat klopt met de bank. Een factuur die er nog op staat is open,
    of al betaald en nog af te letteren."""
    inst = a.inst
    nrs = [nr for nr in x.orders.split(", ") if nr]
    if x.kaart < 0.005:
        credit = [nr for nr in nrs if incl_van[nr] < 0]
        if x.echt_open < -KLEIN:
            return (
                f"Afgeletterd; {inst.opdrachtgever} betaalde {nl_bedrag(-x.echt_open)} meer dan {inst.bureau} "
                "factureerde; zie blad Verschillen"
            )
        if x.echt_open >= KLEIN and credit:
            return (
                f"Afgeletterd; creditorder {', '.join(credit)} is daarna verrekend, daardoor telt "
                f"{nl_bedrag(x.echt_open)} weer als open"
            )
        if x.echt_open < AFRONDING:
            return "Afgeletterd"
        if x.echt_open < KLEIN:
            return "Afgeletterd; klein verschil"
        if nrs and all(nr in a.betaald for nr in nrs):
            return (
                f"Afgeletterd, terwijl {inst.opdrachtgever} {nl_bedrag(x.echt_open)} minder betaalde: dat deel staat "
                f"niet op {', '.join(nrs)}; zie blad Verschillen"
            )
        return (
            f"Afgeletterd in de boekhouding, maar de bankexport toont geen ontvangst voor {x.orders or 'deze factuur'}"
        )
    if x.ontvangen < 0.005:
        toezeggingen = sorted({in_toezegging[w] for w in x.weken.split(", ") if w in in_toezegging})
        if nrs:
            reden = "; ".join(f"{nr} {oordelen[nr].lower()}" for nr in nrs)
        elif toezeggingen:
            reden = f"geen order, wel op toezegging {', '.join(toezeggingen)}, niets ontvangen"
        else:
            reden = "geen order, niets ontvangen"
        return f"Open: {reden}"
    if x.echt_open < AFRONDING:
        return f"Betaald door {inst.opdrachtgever}; staat nog open op de kaart"
    return f"Deels betaald: {nl_bedrag(x.ontvangen)} ontvangen, {nl_bedrag(x.echt_open)} staat nog open"


def _kaart_facturen(
    a: Aansluiting,
    u: pd.DataFrame,
    verdeling: _Verdeling,
    open_op_kaart: pd.Series,
    incl_van: pd.Series,
    oordelen: dict[str, str],
    in_toezegging: dict[str, str],
) -> pd.DataFrame:
    """Blok C: elke factuur van de medewerker naast wat de opdrachtgever ervoor betaalde (incl. btw) en wat ervan op
    de kaart openstaat."""
    f = u[_heeft_factuur(u)]
    fk = f.groupby("factuurnummer").agg(
        factuurdatum=("factuurdatum", "first"), netto=("netto", "sum"), weken=("week", samenvoegen)
    )
    fk = fk[fk.netto.abs() > 0.005].sort_values("factuurdatum", kind="stable")
    fk["incl"] = (fk.netto * (1 + a.inst.btw)).round(2)
    fk["ontvangen"] = verdeling.ontvangen.reindex(fk.index).fillna(0.0)
    fk["orders"] = verdeling.orders.reindex(fk.index).fillna("")
    fk["echt_open"] = (fk.incl - fk.ontvangen).round(2)
    fk["kaart"] = open_op_kaart.reindex(fk.index).fillna(0.0)
    fk = fk.reset_index(names="factuur")
    fk["oordeel"] = [_oordeel_factuur(x, a, incl_van, oordelen, in_toezegging) for x in fk.itertuples()]
    return fk[["factuur", "factuurdatum", "weken", "incl", "orders", "ontvangen", "echt_open", "kaart", "oordeel"]]


def _sleutel_bank(datum: pd.Series, bedrag: pd.Series) -> pd.Series:
    """Datum en bedrag als tekst: een ontvangst op de kaart heeft geen verwijzing naar de bank, die twee wel."""
    return datum.dt.strftime("%Y%m%d") + "|" + bedrag.round(2).map("{:.2f}".format)


def _op_bank(r, bank_op: pd.DataFrame, gebruikt: set[int]) -> tuple[pd.Series | None, bool]:
    """De bankboeking van een ontvangst op de kaart en of die precies is: dezelfde datum en hetzelfde bedrag.

    Elke boeking hoort bij één ontvangst: staan er twee even grote boekingen op één dag, dan krijgt de eerste
    ontvangst de eerste boeking en de tweede de tweede (`gebruikt` zijn de rijen die al een ontvangst hebben; staat op
    de kaart een rekening, dan gaat een boeking van die rekening voor). Zijn alle boekingen met dit bedrag al
    gekoppeld, dan staat de ontvangst niet in de bankexport.

    Beperking: bij even grote boekingen is de koppeling een volgorde, geen herkenning. Staat er maar één ontvangst op
    de kaart (de andere zijn afgeletterd) en zijn er meer gelijke boekingen, dan hoort hij bij de eerste, ook als de
    open factuur van een andere medewerker is; het kaartblok van die medewerker sluit dan niet. De ontvangst wordt
    nooit twee keer geteld.

    Staat op de kaart alleen het restant van een betaling (de rest is afgeletterd), dan is het de enige grotere
    boeking van die dag op die rekening; zonder rekening op de kaart telt de enige grotere boeking van die dag."""
    gelijk = bank_op[bank_op.sleutel == r.sleutel]
    if len(gelijk):
        vrij = gelijk[~gelijk.index.isin(gebruikt)]
        van_rekening = vrij[vrij.rekening == r.naam] if r.naam else vrij
        vrij = van_rekening if len(van_rekening) else vrij
        if not len(vrij):
            return None, False
        gebruikt.add(vrij.index[0])
        return vrij.iloc[0], True
    grotere = bank_op[(bank_op.datum == r.datum) & (bank_op.bedrag > -r.bedrag)]
    if r.naam:
        grotere = grotere[grotere.rekening == r.naam]
    return (grotere.iloc[0] if len(grotere) == 1 else None), False


def _mijn_deel(a: Aansluiting, b: pd.Series, aandeel: pd.Series, incl_van: pd.Series) -> tuple[float, float]:
    """Het deel van een bankboeking dat bij de orders van de medewerker hoort, en wat daarvan is ingehouden.

    Geeft `(ontvangen, ingehouden)`. Staat er een creditorder van een ander op de boeking en kwam er minder geld binnen
    dan de orders van de medewerker op die boeking: dat verschil is ingehouden (de order telt als betaald, maar dat
    deel is niet ontvangen) en niet in `ontvangen` meegeteld.

    Elke order op de boeking heeft een betaling voor deze rekening in `a.betaald`; `_controleer_koppeling` heeft dat
    in `maak` al gecontroleerd."""

    def deel_van(nr: str) -> float:  # het aandeel van die order in deze boeking, negatief bij een creditorder
        return a.betaald[nr][b.rekening].deel

    sluit = all(a.betaald[nr][b.rekening].sluit for nr in b.orders)
    # sluit de boeking op de cent, dan krijgt elk stuk precies zijn aandeel; anders nooit meer dan de boeking zelf
    geteld = sum((deel_van(nr) if sluit else min(b.bedrag, deel_van(nr))) * aandeel.get(nr, 0.0) for nr in b.orders)
    vreemd = any(incl_van[nr] < 0 and aandeel.get(nr, 0.0) < 0.999 for nr in b.orders)
    ingehouden = (
        max(0.0, sum(deel_van(nr) * aandeel.get(nr, 0.0) for nr in b.orders if incl_van[nr] > 0) - b.bedrag)
        if sluit and vreemd
        else 0.0
    )
    return _cent(geteld - ingehouden), _cent(ingehouden)


def _opmerking_ontvangst(r, b: pd.Series | None, exact: bool, notities: pd.DataFrame | None, voornaam: str) -> str:
    """De opmerking bij een ontvangst op de kaart: de notitie bij de bankboeking, of wat de tool ziet als er geen
    boeking met dat bedrag is."""
    if b is None:
        return "Staat niet in de bankexport"
    if exact:
        return _notitie(b, notities, voornaam)[1]
    # het restant: een notitie die bij de omschrijving op de kaart past, gaat voor
    eigen = _notitie(b.where(b.index != "omschrijving", r.omschrijving), notities, voornaam)[1]
    return eigen or f"Lijkt een restant van de bankboeking van {nl_bedrag(b.bedrag)} op deze dag"


class _Ontvangsten(NamedTuple):
    """De ontvangsten op de kaart naast de bankexport."""

    blok_d: pd.DataFrame
    ontvangen: float  # het deel van de medewerker in de boekingen die precies op de bank staan
    ingehouden: float  # wat daarvan is ingehouden voor een creditorder van een ander
    credits: list[str]  # die creditorders
    exact: set[str]  # orders van boekingen met precies dit bedrag op de bank
    op_kaart: set[str]  # die orders, plus die van de boeking waarvan alleen een restant op de kaart staat


def _kaart_ontvangsten(
    a: Aansluiting, d: Dossier, aandeel: pd.Series, incl_van: pd.Series, voornaam: str
) -> _Ontvangsten:
    """Koppelt elke ontvangst op de kaart aan een bankboeking en rekent uit wat daarvan bij de medewerker hoort (blok
    D). Een ontvangst heeft dezelfde datum en hetzelfde bedrag als zijn boeking; staat op de kaart alleen het restant
    van een betaling, dan is het de enige grotere boeking van die dag op die rekening."""
    inst, ko = a.inst, d.kaart[d.kaart.soort == "ontvangst"]
    bank_op = a.bankregels.assign(sleutel=_sleutel_bank(a.bankregels.datum, a.bankregels.bedrag))
    gebruikt: set[int] = set()
    naam = ko.rekening.map(lambda iban: inst.rekening(iban).naam if iban else "")
    ko = ko.assign(sleutel=_sleutel_bank(ko.datum, -ko.bedrag), naam=naam).sort_values("datum", kind="stable")
    rijen, ontvangen, ingehouden, credits, exact, restant = [], 0.0, 0.0, set(), set(), set()
    for r in ko.itertuples():
        b, precies = _op_bank(r, bank_op, gebruikt)
        deel, inhouding = _mijn_deel(a, b, aandeel, incl_van) if precies else (0.0, 0.0)
        ontvangen, ingehouden = ontvangen + deel, ingehouden + inhouding
        if inhouding:
            credits |= {nr for nr in b.orders if incl_van[nr] < 0}
        if b is not None:
            (exact if precies else restant).update(b.orders)
        rijen.append(
            dict(
                datum=r.datum, rekening=r.naam, omschrijving=r.omschrijving, bedrag=-r.bedrag,
                orders=", ".join(b.orders) if b is not None else "", waarvan=deel,
                opmerking=_opmerking_ontvangst(r, b, precies, d.bank_notities, voornaam),
            )
        )  # fmt: skip
    kolommen = ["datum", "rekening", "omschrijving", "bedrag", "orders", "waarvan", "opmerking"]
    return _Ontvangsten(
        pd.DataFrame(rijen, columns=kolommen), _cent(ontvangen), _cent(ingehouden), sorted(credits), exact,
        exact | restant,
    )  # fmt: skip


def _is_afgeletterd(dag: pd.Timestamp, inst: Instellingen, verdeling: _Verdeling, open_nummers: pd.Series) -> bool:
    """Is de factuur over deze dag afgeletterd, dus niet meer open op de kaart? Heeft de dag een factuur, dan telt die;
    heeft de dag er geen (onkosten staan in de uren soms op een andere dag), dan de facturen van dezelfde week."""
    dagen = verdeling.dagen[verdeling.dagen.datum == dag]
    if len(dagen):
        return not dagen.factuurnummer.isin(open_nummers).any()
    week = verdeling.weken[verdeling.weken.wk == inst.week(dag)]
    return len(week) > 0 and not week.factuurnummer.isin(open_nummers).any()


def _creditcorrectie(
    inst: Instellingen,
    ok: pd.DataFrame,
    incl_van: pd.Series,
    o: _Ontvangsten,
    verdeling: _Verdeling,
    open_nummers: pd.Series,
) -> tuple[float, float, list[str]]:
    """Wat er op de kaart staat voor dagen waarvan de factuur al is afgeletterd. Geeft `(teruggehaald, al_afgeletterd,
    creditorders)`.

    Een creditorder die pas na het afletteren op een betaling is verrekend, telt weer als open (`teruggehaald`). Een
    ontvangst op de kaart voor een gewone order waarvan de factuur al is afgeletterd (`al_afgeletterd`) is geen betaling
    van een open factuur en gaat niet van de open facturen af."""
    laat = ok[ok.order.isin(o.exact) & ok.datum.map(lambda dag: _is_afgeletterd(dag, inst, verdeling, open_nummers))]
    credit = laat.order.map(incl_van) < 0
    return (
        _cent(-laat.ontv[credit].sum()),
        _cent(laat.ontv[laat.order.map(incl_van) > 0].sum()),
        sorted(set(laat.order[credit])),
    )


def _kaart_regels(
    inst: Instellingen, voornaam: str, open_: float, o: _Ontvangsten, wie_credit: str, correctie, zonder_factuur: float
) -> tuple[list[tuple[str, float, str]], list[str]]:
    """De regels van blok B in de volgorde van het blad: (label, bedrag met teken, uitleg), en hun sleutels (`R_...`).
    De som is wat er echt openstaat."""
    teruggehaald, al_afgeletterd, credit_laat = correctie
    af = f"Af: ontvangen op orders van {voornaam}, nog niet afgeletterd"
    af_ontvangen = _cent(o.ontvangen + teruggehaald - al_afgeletterd)
    regels = [
        (R_OPEN, f"Open facturen van {voornaam} op de kaart", open_, ""),
        (
            R_AF_ONTVANGEN,
            af + (" (zonder wat op al afgeletterde facturen binnenkwam)" if al_afgeletterd else ""),
            -af_ontvangen,
            "",
        ),
    ]
    if teruggehaald:
        regels.append(
            (
                R_BIJ_CREDIT,
                "Bij: creditorder verrekend na het afletteren",
                teruggehaald,
                f"Creditorder {', '.join(credit_laat)} is verrekend op een betaling nadat de factuur over die dagen "
                "was afgeletterd. Daardoor telt dit bedrag weer als open.",
            )
        )
    if abs(o.ingehouden) > AFRONDING:
        regels.append(
            (
                R_AF_VERREKEND,
                "Af: op de betaling staat een creditorder van een ander (minder geld ontvangen)",
                -o.ingehouden,
                f"De betalingen zijn {nl_bedrag(o.ingehouden)} lager dan de orders van {voornaam} die erop staan; "
                f"creditorder {', '.join(o.credits)} ({wie_credit}) staat op die boekingen. Ga na of de creditorder "
                "in de boekhouding is geboekt.",
            )
        )
    regels.append(
        (
            R_BIJ_NIET_GEFACTUREERD,
            f"Bij: daarvan ontvangen voor dagen die {inst.bureau} nooit heeft gefactureerd",
            zonder_factuur,
            zin(
                f"{inst.opdrachtgever} betaalde deze dagen via een order; er is geen factuur van {inst.bureau} om "
                f"tegen af te letteren. Zie blad Verschillen, '{O_NIETGEF}'."
            ),
        )
    )
    return [(label, bedrag, uitleg) for _, label, bedrag, uitleg in regels], [sleutel for sleutel, *_ in regels]


def _los_op_kaart(orders_van_factuur: str, op_kaart: set[str], betaald: dict) -> bool:
    """Staat het geld van deze orders los op de kaart? Ja als een boeking van een order (of het restant ervan) op de
    kaart staat, of als de order niet in de bankexport staat (dan is de kaart het enige bewijs). Een order telt al als
    "op de kaart" zodra een van zijn boekingen er staat; per boeking uitzoeken als dat ooit scheef loopt."""
    return any((nr in op_kaart) or not betaald.get(nr) for nr in orders_van_factuur.split(", ") if nr)


def _kaart_eraf(
    inst: Instellingen, fk: pd.DataFrame, o: _Ontvangsten, betaald: dict, zonder_factuur: float
) -> list[tuple[str, float, str]]:
    """Wat er van wat echt openstaat afgaat voor de netto vordering: geld dat de opdrachtgever betaalde zonder dat het
    bureau het factureerde, als (label, bedrag zonder teken, uitleg). Bij afgeletterde facturen waarop meer is betaald
    telt dat alleen als dat geld nog los op de kaart staat; anders is het verschil bij het afletteren weggeboekt en
    klopt de som niet met het saldo van de kaart."""
    meer_betaald = (
        (fk.kaart < 0.005) & (fk.echt_open < -KLEIN) & fk.orders.map(lambda s: _los_op_kaart(s, o.op_kaart, betaald))
    )
    meer = _cent(-fk.echt_open[meer_betaald].sum())
    posten = (
        (
            meer,
            f"Daartegenover: door {inst.opdrachtgever} meer betaald dan gefactureerd, op facturen die al zijn "
            "afgeletterd",
            zin(
                f"{inst.opdrachtgever} betaalde bij deze facturen meer dan {inst.bureau} factureerde; zie blok C en "
                "blad Verschillen."
            ),
        ),
        (
            zonder_factuur,
            f"Daartegenover: door {inst.opdrachtgever} betaald voor dagen die {inst.bureau} nooit heeft gefactureerd",
            "Hetzelfde bedrag als hierboven bij 'Bij'. Wordt dit alsnog gefactureerd, dan vervalt deze aftrek.",
        ),
    )
    return [(label, bedrag, uitleg) for bedrag, label, uitleg in posten if bedrag > 0.005]


def _kaart_heel(d: Dossier, kf: pd.DataFrame, ko: pd.DataFrame) -> dict:
    """Blok A: de bedragen en aantallen van de hele kaart, en de open facturen per medewerker uit de uren. Geeft een
    woordenboek met de velden van `Kaartblok` die erbij horen."""
    wie = d.uren[_heeft_factuur(d.uren)].drop_duplicates("factuurnummer").set_index("factuurnummer").medewerker
    van_wie = kf.nummer.map(wie)
    buiten = van_wie.isna()
    heel = dict(
        open=_cent(kf.bedrag.sum()), ontvangsten=_cent(ko.bedrag.sum()), saldo=_cent(kf.bedrag.sum() + ko.bedrag.sum()),
        buiten_uren=_cent(kf.bedrag[buiten].sum()),
    )  # fmt: skip
    return dict(
        heel=heel,
        per_medewerker=kf.bedrag.groupby(van_wie, sort=False).sum().round(2).rename("open").rename_axis("medewerker"),
        aantallen=dict(open=len(kf), ontvangsten=len(ko), buiten_uren=int(buiten.sum())),
        per_medewerker_aantal=van_wie.groupby(van_wie, sort=False).size().rename_axis("medewerker"),
        buiten_nummers=list(kf.nummer[buiten]),
        volgorde=list(dict.fromkeys(van_wie.fillna(""))),
    )


def _kaart_blok(
    a: Aansluiting,
    d: Dossier,
    u: pd.DataFrame,
    eff: pd.DataFrame,
    pdag: pd.DataFrame,
    orders: pd.DataFrame,
    oordelen: dict[str, str],
    in_toezegging: dict[str, str],
) -> Kaartblok:
    """De debiteurenkaart van deze medewerker naast de bank en de orders. Sluit wat er echt openstaat niet aan op wat
    bank en orders per factuur zeggen, dan is `sluit` onwaar."""
    inst, kaart, voornaam = a.inst, d.kaart, a.medewerker.split()[0]
    kf, ko = kaart[kaart.soort == "factuur"], kaart[kaart.soort == "ontvangst"]
    incl_van = _incl_van(a)
    ok, aandeel = _orderbedragen(a, pdag, orders, incl_van)
    verdeling = _verdeel_ontvangsten(inst, eff, ok, set(kf.nummer[kf.bedrag > 0.005]))
    fk = _kaart_facturen(a, u, verdeling, kf.groupby("nummer").bedrag.sum(), incl_van, oordelen, in_toezegging)
    o = _kaart_ontvangsten(a, d, aandeel, incl_van, voornaam)
    correctie = _creditcorrectie(inst, ok, incl_van, o, verdeling, kf.nummer)
    teruggehaald, al_afgeletterd, _ = correctie
    wie_credit = samenvoegen(a.order_regels.medewerker[a.order_regels.order.isin(o.credits)])
    zonder_factuur = verdeling.zonder_factuur

    open_ = _cent(fk.kaart.sum())
    regels, sleutels = _kaart_regels(inst, voornaam, open_, o, wie_credit, correctie, zonder_factuur)
    echt_open = _cent(sum(bedrag for _, bedrag, _ in regels))
    controle = _cent(fk.echt_open[fk.kaart > 0].sum() + teruggehaald)
    eraf = _kaart_eraf(inst, fk, o, a.betaald, zonder_factuur)
    return Kaartblok(
        **_kaart_heel(d, kf, ko), open=open_,
        af_ontvangen=_cent(o.ontvangen + teruggehaald - al_afgeletterd), bij_credit=teruggehaald,
        bij_niet_gefactureerd=zonder_factuur, echt_open=echt_open, controle=controle, eraf=eraf,
        per_saldo=_cent(echt_open - sum(bedrag for _, bedrag, _ in eraf)), facturen=fk, ontvangsten=o.blok_d,
        af_verrekend=o.ingehouden, regels=regels, sluit=abs(echt_open - controle) < CONTROLE_VERSCHIL,
        al_afgeletterd=al_afgeletterd, regel_sleutels=sleutels,
    )  # fmt: skip


def _controleer_koppeling(a: Aansluiting) -> None:
    """Elke order op een bankboeking heeft een betaling voor de rekening van die boeking. `bank.koppel` vult de
    boekingen en de betalingen uit dezelfde lijst, dus een verschil is een fout in de tool en niet in de invoer."""
    if a.bankregels is None:
        return
    for r in a.bankregels.itertuples():
        for nr in r.orders:
            if r.rekening not in a.betaald.get(nr, {}):
                raise ValueError(
                    f"order {nr} staat op een bankboeking van {r.datum:%d-%m-%Y} maar heeft geen betaling voor "
                    f"{r.rekening}; dit is een fout in de koppeling van de bank, meld het"
                )


def maak(a: Aansluiting, d: Dossier, eigen: dict[tuple[str, str], str] | None = None) -> Overzicht:
    """Rekent het overzicht van de medewerker van `a`, met de bronnen uit het dossier.

    `eigen` is de keuze van de gebruiker per (week, oorzaak) uit een eerder werkboek; die gaat voor het voorstel.
    Klopt een controle niet (alles moet aansluiten op de bronnen en de verschilregels moeten optellen tot het
    totale verschil), dan volgt een `ValueError`; ook als de bankboekingen en de betalingen van `a` elkaar tegenspreken
    (een fout in de tool, zie `_controleer_koppeling`)."""
    inst, medewerker = a.inst, a.medewerker
    _controleer_koppeling(a)
    _controleer_status(a)
    _controleer_factuurnummers(a)
    u = a.uren.copy()
    u["week"] = weeksleutels(u.datum, inst)
    eff = u[u.telt_mee]
    pdag = _per_dag(a)

    t = _toezegging_van(d, medewerker)
    met_bedrag = (t.week != "") & t.bedrag.notna()
    in_toezegging = dict(zip(t.week[met_bedrag], t.nr[met_bedrag], strict=True))
    toezegging_naam = " en ".join(
        f"{x.nr} van {x.datum:%d-%m-%Y}" if pd.notna(x.datum) else str(x.nr)
        for x in t[["nr", "datum"]].drop_duplicates().itertuples()
    )
    maanden = set(t.maand) - {""}
    afwijkend = d.urenstaat_afwijkend
    if afwijkend is not None:
        afwijkend = afwijkend[afwijkend.medewerker == medewerker]
    stukken = d.zonder_order[d.zonder_order.medewerker == medewerker] if d.zonder_order is not None else None
    open_op_kaart = set(d.kaart.nummer[d.kaart.soort == "factuur"]) if d.kaart is not None else None

    verschillen = _verschillen(
        pdag, eff, inst, in_toezegging, toezegging_naam, maanden, d.saldo(medewerker), eigen or {}, medewerker
    )
    totaal_verschil = (pdag.fact_bedrag_uren + pdag.fact_onkosten - pdag.order_bedrag_uren - pdag.order_onkosten).sum()
    if abs(verschillen.verschil.sum() - totaal_verschil) >= 0.01:
        extra = ""
        if (oordeel := d.saldo(medewerker)) is not None and (
            oordeel.gefactureerd.notna() | oordeel.order.notna()
        ).any():
            extra = f"; ga na of de posten met bedragen in {saldo_bestand(medewerker)} samen nul zijn"
        raise ValueError(f"de verschilregels tellen niet op tot het totale verschil{extra}")
    weken = _weken(u, pdag, in_toezegging, afwijkend, inst)
    orders, oordelen = _orders(a, pdag, eff, open_op_kaart)
    facturen = _facturen(u)
    if abs(facturen.netto.sum() - eff.netto.sum()) >= 0.01:
        raise ValueError("de facturen tellen niet op tot de urenregels (een fout in de tool)")
    perioden = _perioden(
        weken, verschillen, pdag, oordelen, dict(zip(orders.order, orders.soort, strict=True)), a.bankregels is not None
    )
    zonder_order, zonder_order_dag = _zonder_order(a, u, eff, pdag, in_toezegging, stukken)
    stand = dict(
        urenstaat=round(weken.urenstaat_bedrag.sum(), 2),
        gefactureerd=round(weken.fact.sum(), 2),
        op_orders=round(weken.order.sum(), 2),
        betaald=round(orders.betaald_ex.sum(), 2) if len(orders) else 0.0,
    )
    kaart = None
    if d.kaart is not None and a.bankregels is not None:  # de kaart wordt tegen de bank gelegd: zonder bank geen blok
        kaart = _kaart_blok(a, d, u, eff, pdag, orders, oordelen, in_toezegging)
    return Overzicht(
        medewerker=medewerker, inst=inst, aansluiting=a, per_dag=pdag, stand=stand, verschillen=verschillen,
        weken=weken, perioden=perioden, orders=orders, facturen=facturen, zonder_order=zonder_order,
        zonder_order_dag=zonder_order_dag, bank_los=_bank_los(a, d.bank_notities),
        toezegging=_toezegging_blok(a, t, eff, pdag, oordelen), kaart=kaart, bank=_bank(a, d.bank_notities),
    )  # fmt: skip
