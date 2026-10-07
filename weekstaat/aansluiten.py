"""Legt per medewerker de gefactureerde uren naast de orders van de opdrachtgever.

De factuurkant zijn de urenregels van het bureau, de orderkant de regels op de orders van de opdrachtgever. Beide
kanten worden per dag en per order vergeleken; `sluit_aan` geeft per dag een status en een toelichting."""

import re
from dataclasses import dataclass

import pandas as pd

from . import bank
from .dossier import in_naam
from .instellingen import Instellingen, Rekening
from .opmaak import nl_bedrag

GEEN = "—"  # de dag staat niet op een order

KLOPT = "Klopt"
ONK = "Uren kloppen, onkosten wijken af"
UREN = "Urenbedrag wijkt af"
ALLEEN = "Alleen op order"
KM = "Bedragen kloppen, km zonder bedrag op order"
CORR = "Correctie of credit bij een andere order"
GEEN_P = "Geen order - hele periode ontbreekt"
GEEN_D = "Geen order - dag ontbreekt in periode"
STATUSSEN = (KLOPT, KM, ONK, UREN, ALLEEN, CORR, GEEN_D, GEEN_P)

OW, OV = "Opdrachtgever te weinig opgenomen", "Opdrachtgever te veel opgenomen"
CW, CV = "Bureau te weinig gefactureerd", "Bureau te veel gefactureerd"
NOG = "Nog uitzoeken"
TOEWIJZINGEN = (OW, OV, CW, CV, NOG)

TEKSTKOLOMMEN = ["urenstaten", "facturen", "entiteit", "projecten", "verzamel"]
BEDRAGEN = [
    "fact_uren",
    "fact_bedrag_uren",
    "fact_onkosten",
    "order_uren",
    "order_bedrag_uren",
    "order_km",
    "order_onkosten",
]


@dataclass
class Aansluiting:
    """De uitkomst van `sluit_aan`: alles wat het overzicht nodig heeft.

    `inst` zijn de instellingen waarmee is aangesloten (blok, btw, betaaltermijn); de latere stappen rekenen ermee
    verder, zodat een week of periode overal hetzelfde heet.
    `per_dag` heeft één regel per dag en order; een dag zonder order heeft `GEEN` als order.
    `uren` zijn de urenregels van de medewerker en `order_regels` de regels van alle orders, elk met een kolom
    `post` (uren of onkosten) en `telt_mee`; `uren` heeft ook de toegewezen `order`. `kop` zijn de orders zonder
    de vervallen orders, `vervallen` de vervallen orders met hun kop. `bankregels`, `betaald` en `bank_tot` zijn
    leeg als er geen bankregels waren; `bank_tot` is de laatste datum in de bank. `saldo` heeft één regel per
    verschil per week en soort, met de toewijzing."""

    medewerker: str
    rekeningen: tuple[Rekening, ...]
    inst: Instellingen
    per_dag: pd.DataFrame
    uren: pd.DataFrame
    order_regels: pd.DataFrame
    kop: pd.DataFrame
    vervallen: pd.DataFrame
    bankregels: pd.DataFrame | None
    betaald: dict[str, dict[str, bank.Betaling]]
    bank_tot: pd.Timestamp | None
    saldo: pd.DataFrame

    def samenvatting(self) -> pd.DataFrame:
        """Per status het aantal dagen, het bedrag aan de factuurkant en aan de orderkant, en het verschil."""
        d = self.per_dag
        t = pd.DataFrame(
            dict(
                status=d.status,
                factuurkant=d.fact_bedrag_uren + d.fact_onkosten,
                orderkant=d.order_bedrag_uren + d.order_onkosten,
            )
        )
        s = t.groupby("status").agg(
            dagen=("status", "size"), factuurkant=("factuurkant", "sum"), orderkant=("orderkant", "sum")
        )
        s["verschil"] = s.factuurkant - s.orderkant
        return s.reindex([x for x in STATUSSEN if x in s.index]).round(2)


def naamsleutel(naam: str) -> tuple[str, str]:
    """Waarmee namen worden vergeleken als ze niet letterlijk gelijk zijn: de voornaam en het laatste woord, zonder
    hoofdletters. Een order schrijft "Anna de Vries" waar de uren "Anna Maria De Vries" hebben."""
    delen = naam.lower().split()
    return (delen[0], delen[-1]) if delen else ("", "")


def _naam_in_de_uren(naam, uren_namen: list[str], order: str) -> str:
    """De naam uit de uren waar een naam op een order bij hoort. Een naam die letterlijk in de uren staat, gaat voor;
    daarna de naam met dezelfde `naamsleutel`. Passen er twee, dan is de naam op de order niet te koppelen: een fout
    in de invoer, geen keuze van de tool."""
    if not isinstance(naam, str) or naam in uren_namen:
        return naam
    passend = [n for n in uren_namen if naamsleutel(n) == naamsleutel(naam)]
    if len(passend) > 1:
        raise ValueError(
            f"order {order}: de naam '{naam}' past bij twee medewerkers in de uren ({' en '.join(passend)}). Maak de "
            "naam op de order of in de uren voluit gelijk, zodat duidelijk is wie bedoeld is"
        )
    return passend[0] if passend else naam


def weeksleutels(datums: pd.Series, inst: Instellingen) -> pd.Series:
    """De week van elke datum volgens de instellingen; een lege datum geeft een lege tekst."""
    return datums.map(lambda d: inst.week(d) if pd.notna(d) else "")


def samenvoegen(lijst: pd.Series) -> str:
    return ", ".join(sorted(set(str(x) for x in lijst.dropna())))


def _per_dag_en_order(df: pd.DataFrame, regel, kolommen: list[str]) -> pd.DataFrame:
    """Eén regel per dag en order. Een lege kant geeft een lege tabel met dezelfde kolommen."""
    if df.empty:
        return pd.DataFrame(columns=kolommen, index=pd.MultiIndex.from_arrays([[], []], names=["datum", "order"]))
    return df.groupby(["datum", "order"]).apply(regel, include_groups=False)


def _orders_bepalen(kop: pd.DataFrame, regels: pd.DataFrame, namen: pd.Series, vervallen: pd.DataFrame | None):
    """Maakt de orderkant schoon: dubbele orders eruit, namen gelijk aan die van de urenregels en correctieregels
    bij hun medewerker. Geeft (kop zonder vervallen orders, alle regels, vervallen orders)."""
    # Een order kan twee keer in de map zitten; de kortste bestandsnaam (zonder toevoegingen) wint.
    kop = kop.sort_values("bestand", key=lambda s: s.str.len(), kind="stable").drop_duplicates("order")
    alle = regels[regels.bestand.isin(kop.bestand)].copy()
    # Een order schrijft "Anna de Vries", de urenstaat "Anna Maria De Vries": vergelijk op voornaam en laatste woord,
    # maar een naam die letterlijk in de uren staat gaat voor.
    uren_namen = [n for n in namen.unique() if isinstance(n, str)]
    alle["medewerker"] = [
        _naam_in_de_uren(n, uren_namen, nr) for n, nr in zip(alle.medewerker, alle.order, strict=True)
    ]
    # Een correctieregel zonder naam ("correctie uurtarief op 16-11-2025") hoort bij wie op die order op die dag staat.
    for i, r in alle[alle.medewerker.isna()].iterrows():
        m = re.search(r"\d{2}-\d{2}-\d{4}", str(r.omschrijving))
        dag = pd.to_datetime(m.group(), format="%d-%m-%Y") if m else None
        wie = alle[(alle.order == r.order) & (alle.datum == dag)].medewerker.dropna().unique()
        if len(wie) == 1:
            alle.loc[i, "medewerker"], alle.loc[i, "datum"] = wie[0], dag
    alle["post"] = alle.eenheid.eq("Uren").map({True: "uren", False: "onkosten"})
    # Een vervallen order is door een andere vervangen zonder te zijn gecrediteerd. Hij telt nergens mee (ook niet
    # voor de bank), maar blijft zichtbaar in het blad met de orderregels.
    if vervallen is None:
        vervallen = pd.DataFrame(columns=["order", "vervangen_door", "toelichting"], dtype=str)
    vervallen = vervallen.merge(kop, on="order")
    return kop[~kop.order.isin(vervallen.order)], alle, vervallen


def _verzamelorders(pdf: pd.DataFrame, eff: pd.DataFrame) -> pd.DataFrame:
    """Een verzamelorder met alleen onkosten (bijvoorbeeld km over een heel jaar) die het bureau als één bedrag heeft
    gefactureerd, telt als één regel op de datum van de factuur: anders valt hij per dag uit elkaar."""
    for nr, g in pdf.groupby("order"):
        kandidaat = eff[(eff.post == "onkosten") & ((eff.netto - g.bedrag.sum()).abs() < 0.005)]
        if (g.post == "onkosten").all() and len(g) > 1 and len(kandidaat) == 1:
            een = g.iloc[[0]].copy()
            een["datum"], een["aantal"], een["bedrag"] = kandidaat.datum.iloc[0], g.aantal.sum(), g.bedrag.sum()
            een["verzamel"] = (
                f"verzamelorder onkosten, {len(g)} regels van {g.datum.min():%d-%m-%Y} t/m {g.datum.max():%d-%m-%Y}"
            )
            pdf = pd.concat([pdf[pdf.order != nr], een])
    return pdf


def _wijs_toe(rij, dag: pd.DataFrame) -> str:
    """Kies de order waar een urenregel bij hoort. Alleen spannend als er twee orders op één dag staan:
    dan wint de order met precies hetzelfde bedrag, anders de order van dezelfde soort met het hoogste bedrag."""
    orders = list(dict.fromkeys(dag.order))
    if len(orders) <= 1:
        return orders[0] if orders else GEEN
    zelfde = dag[dag.post == rij.post]
    exact = zelfde[(zelfde.bedrag - rij.netto).abs() < 0.005]
    for kandidaat in (exact, zelfde[zelfde.verzamel == ""], dag[dag.post == "uren"], dag):
        if len(kandidaat):
            return kandidaat.groupby("order").bedrag.sum().idxmax()
    return GEEN


def _beoordeel(r, gedekt: set[str], km_met_bedrag: dict) -> tuple[str, str]:
    """De status en de toelichting van één dag en order."""
    du, do = round(r.fact_bedrag_uren - r.order_bedrag_uren, 2), round(r.fact_onkosten - r.order_onkosten, 2)
    opm = []
    if r.order == GEEN:
        return (GEEN_D if r.periode in gedekt else GEEN_P), ""
    elders = ", ".join(sorted(km_met_bedrag.get(r.datum, set()) - {r.order}))
    if r.verzamel:
        opm.append(r.verzamel)
    if r.km_zonder_bedrag:
        opm.append(f"{r.order_km:g} km vermeld zonder bedrag" + (f", later met bedrag op {elders}" if elders else ""))
    elif r.order_km and elders and not r.verzamel:
        opm.append(f"dezelfde dag staan ook kilometers op {elders}")
    if r.fact_uren and r.order_uren and abs(r.fact_tarief - r.order_tarief) > 0.005:
        opm.append(f"tarief factuur {r.fact_tarief:.2f} vs order {r.order_tarief:.2f}")
    if do > 0 and r.order_onkosten == 0 and not r.km_zonder_bedrag:
        opm.append(f"onkosten {do:.2f} niet op order")
    if do < 0 and r.fact_onkosten == 0:
        opm.append(f"onkosten {-do:.2f} op order, niet gefactureerd")
    if r.niet_facturabel:
        opm.append("op de order als 'Niet Facturabel' benoemd, wel met bedrag")
    if r.km_zonder_bedrag and not r.fact_onkosten and not elders:
        opm.append("ook geen onkosten gefactureerd")
    if not r.urenstaten and (r.order_bedrag_uren or r.order_onkosten):
        status = ALLEEN
    else:
        status = UREN if du else ONK if do else KM if (r.km_zonder_bedrag and not elders) else KLOPT
    return status, "; ".join(opm)


def _per_dag(eff: pd.DataFrame, pdf: pd.DataFrame, km_met_bedrag: dict, inst: Instellingen) -> pd.DataFrame:
    """De vergelijking per dag en order, met status en toelichting."""
    A = _per_dag_en_order(
        eff,
        lambda g: pd.Series(
            {
                "urenstaten": samenvoegen(g.urenstaat),
                "facturen": samenvoegen(g.factuurnummer),
                "fact_uren": g.uren.sum(),
                "fact_bedrag_uren": g.netto[g.post == "uren"].sum(),
                "fact_onkosten": g.netto[g.post == "onkosten"].sum(),
            }
        ),
        ["urenstaten", "facturen", "fact_uren", "fact_bedrag_uren", "fact_onkosten"],
    )
    B = _per_dag_en_order(
        pdf,
        lambda g: pd.Series(
            {
                "entiteit": samenvoegen(g.entiteit),
                "projecten": samenvoegen(g.project),
                # Een regel met een tarief ver onder het uurtarief (1,46 of -10,00) corrigeert het tarief:
                # dat zijn geen extra uren.
                "order_uren": g.aantal[(g.post == "uren") & ~(g.tarief < 20)].sum(),
                "order_bedrag_uren": g.bedrag[g.post == "uren"].sum(),
                "order_km": g.aantal[g.eenheid == "Kilometers"].sum(),
                "order_onkosten": g.bedrag[g.post == "onkosten"].sum(),
                "km_zonder_bedrag": ((g.eenheid == "Kilometers") & (g.bedrag == 0)).any(),
                "niet_facturabel": g.omschrijving.str.contains("Niet Facturabel", na=False).any(),
                "verzamel": samenvoegen(g.verzamel[g.verzamel != ""]),
            }
        ),
        ["entiteit", "projecten", "order_uren", "order_bedrag_uren", "order_km", "order_onkosten",
         "km_zonder_bedrag", "niet_facturabel", "verzamel"],
    )  # fmt: skip
    D = A.join(B, how="outer").reset_index().sort_values(["datum", "order"]).reset_index(drop=True)
    D[BEDRAGEN] = D[BEDRAGEN].astype(float).fillna(0).round(2)
    D[TEKSTKOLOMMEN] = D[TEKSTKOLOMMEN].fillna("").astype(str)
    D["km_zonder_bedrag"] = D.km_zonder_bedrag.eq(True)
    D["niet_facturabel"] = D.niet_facturabel.eq(True)

    # De opdrachtgever plaatst zijn orders per blok (bijvoorbeeld week 21-24, 33-36, ...): daarop zoeken we
    # ontbrekende orders. Wat een blok is, staat in de instellingen.
    D["week"] = weeksleutels(D.datum, inst)
    D["periode"] = D.datum.map(inst.periode)
    gedekt = set(D.periode[D.order != GEEN])
    D["fact_tarief"] = (D.fact_bedrag_uren / D.fact_uren.where(D.fact_uren != 0)).round(2)
    D["order_tarief"] = (D.order_bedrag_uren / D.order_uren.where(D.order_uren != 0)).round(2)

    D[["status", "toelichting"]] = [_beoordeel(r, gedekt, km_met_bedrag) for r in D.itertuples()]
    # Een correctie- of creditorder staat op een eigen regel. Sluit de dag over alle orders samen aan, dan klopt hij.
    dagsom = D.groupby("datum")[["fact_bedrag_uren", "order_bedrag_uren", "fact_onkosten", "order_onkosten"]].transform(
        "sum"
    )
    samen = (
        ((dagsom.fact_bedrag_uren - dagsom.order_bedrag_uren).abs() < 0.005)
        & ((dagsom.fact_onkosten - dagsom.order_onkosten).abs() < 0.005)
        & D.status.isin([UREN, ONK, ALLEEN])
        & (D.groupby("datum").order.transform("size") > 1)
    )
    for i in D.index[samen]:
        rest = ", ".join(x for x in D.order[D.datum == D.datum[i]] if x != D.order[i])
        D.loc[i, ["status", "toelichting"]] = (
            KLOPT,
            "; ".join(t for t in (D.toelichting[i], f"samen met {rest} sluit deze dag aan") if t),
        )
    # Sluit de dag niet aan, dan is een order zonder eigen factuur naast een order mét factuur een correctie daarop,
    # geen vergeten factuur.
    for i in D.index[(D.status == ALLEEN) & D.groupby("datum").urenstaten.transform(lambda s: s.ne("").any())]:
        rest = ", ".join(x for x in D.order[D.datum == D.datum[i]] if x != D.order[i])
        D.loc[i, ["status", "toelichting"]] = (
            CORR,
            f"staat op dezelfde dag als {rest}, dat wel een factuur heeft; samen sluit deze dag niet aan",
        )
    return D


def _saldo(D: pd.DataFrame, eff: pd.DataFrame, saldo: pd.DataFrame | None, medewerker: str) -> pd.DataFrame:
    """Elk verschil per week en soort krijgt één toewijzing: wie heeft te veel of te weinig.

    Het oordeel van de gebruiker (`saldo`) gaat voor: staan week en soort al in de lijst, dan vervangt het oordeel
    de toewijzing en komt de toelichting ervoor; anders is het een extra post."""
    lijnen = []  # week, soort, omschrijving, factuurbedrag, orderbedrag, toewijzing, toelichting, facturen, orders
    for w, g in D.groupby("week"):
        for soort, fk, ok in (
            ("Uren", "fact_bedrag_uren", "order_bedrag_uren"),
            ("Onkosten", "fact_onkosten", "order_onkosten"),
        ):
            afw = g[(g[fk] - g[ok]).abs() > 0.005]
            if afw.empty:
                continue
            fb, ob = round(afw[fk].sum(), 2), round(afw[ok].sum(), 2)
            oms = (
                "Geen order" if (afw.order == GEEN).all()
                else "Wel op order, niet gefactureerd" if not fb
                else "Niet (volledig) op order"
            )  # fmt: skip
            toel = "dagen: " + ", ".join(afw.datum.dt.strftime("%d-%m"))
            if soort == "Onkosten":  # laat zien waaruit het bedrag bestaat, bv. 4 x 12,19
                posten = (
                    eff[eff.post == "onkosten"]
                    .merge(afw[["datum", "order"]], on=["datum", "order"])
                    .netto.round(2)
                    .value_counts()
                )
                toel += " | " + ", ".join(f"{n} x {nl_bedrag(b)}" for b, n in posten.sort_index().items())
            facturen = samenvoegen(afw.facturen.str.split(", ").explode().replace("", pd.NA))
            lijnen.append(
                [
                    w,
                    soort,
                    oms,
                    fb,
                    ob,
                    OW if fb > ob else CW,
                    toel,
                    facturen,
                    samenvoegen(afw.order[afw.order != GEEN]),
                ]
            )
    if saldo is not None:
        bestand = f"saldo {in_naam(medewerker)}.csv"
        for x in saldo.fillna({"toewijzing": "", "toelichting": ""}).itertuples():
            bedragen = pd.notna(x.gefactureerd) or pd.notna(x.order)
            soort = str(x.soort).strip().capitalize()
            if not bedragen and soort not in ("Uren", "Onkosten"):
                raise ValueError(f"{bestand}: regel {x.Index + 2}, kolom soort: '{x.soort}' is Uren of Onkosten")
            bestaand = [ln for ln in lijnen if (ln[0], ln[1]) == (x.week, soort if not bedragen else x.soort)]
            if bestaand:
                bestaand[0][5], bestaand[0][6] = x.toewijzing, f"{x.toelichting} | {bestaand[0][6]}"
            elif not bedragen:
                raise ValueError(
                    f"{bestand}: regel {x.Index + 2}: er is in {x.week} geen verschil van de soort {soort}"
                )
            else:  # een extra post: bedragen zonder regel in de uren
                lijnen.append([x.week, x.soort, "Handmatige post", x.gefactureerd if pd.notna(x.gefactureerd) else 0.0,
                               x.order if pd.notna(x.order) else 0.0, x.toewijzing, x.toelichting, "", ""])  # fmt: skip
    kolommen = [
        "week",
        "soort",
        "omschrijving",
        "fact_bedrag",
        "order_bedrag",
        "toewijzing",
        "toelichting",
        "facturen",
        "orders",
    ]
    return pd.DataFrame(sorted(lijnen, key=lambda ln: (ln[0], ln[1])), columns=kolommen)


def sluit_aan(
    uren: pd.DataFrame,
    kop: pd.DataFrame,
    regels: pd.DataFrame,
    medewerker: str,
    inst: Instellingen,
    bankregels: pd.DataFrame | None = None,
    vervallen: pd.DataFrame | None = None,
    saldo: pd.DataFrame | None = None,
) -> Aansluiting:
    """Legt de urenregels van één medewerker naast de orders en, als er bankregels zijn, naast de bank.

    `uren`, `bankregels`, `vervallen` en `saldo` komen uit `inlezen`; `kop` en `regels` uit `orders.lees_orders`.
    Klopt een controle niet (alles moet aansluiten op de bronnen), dan volgt een `ValueError`."""
    # ---------- factuurkant: alleen regels die netto meetellen ----------
    u = uren[uren.medewerker == medewerker].copy()
    if u.empty:
        raise ValueError(f"{medewerker} komt niet voor in de uren")
    # Gecorrigeerde urenstaten hebben bedrag 0 en gecrediteerde regels vallen weg via bedrag + creditbedrag.
    u["telt_mee"] = u.netto.abs() > 0.005
    u["post"] = u.uren.ne(0).map({True: "uren", False: "onkosten"})
    eff = u[u.telt_mee].copy()

    # ---------- orderkant ----------
    kop, alle, vervallen = _orders_bepalen(kop, regels, uren.medewerker, vervallen)
    # Uren zonder bedrag (feestdag) zijn niet-facturabel en tellen niet mee; km zonder bedrag wel (bevinding).
    alle["telt_mee"] = (
        (alle.medewerker == medewerker)
        & ~((alle.post == "uren") & alle.bedrag.isna())
        & ~alle.order.isin(vervallen.order)
    )
    pdf = alle[alle.telt_mee].copy()
    pdf["bedrag"] = pdf.bedrag.fillna(0)
    pdf["verzamel"] = ""
    # Op welke order staan de kilometers van een dag mét bedrag (om km zonder bedrag en dubbele km te herkennen).
    km_met_bedrag = pdf[(pdf.eenheid == "Kilometers") & (pdf.bedrag > 0)].groupby("datum").order.agg(set).to_dict()
    pdf = _verzamelorders(pdf, eff)

    # ---------- bank ----------
    bankuit, betaald, bank_tot = None, {}, None
    if bankregels is not None:
        anderen = [a for a in uren.medewerker.unique() if a != medewerker]
        bankuit, betaald = bank.koppel(bankregels, kop, alle, medewerker, anderen, inst)
        bank_tot = bankuit.datum.max()
        bankuit = bankuit[bankuit.datum >= u.datum.min()].reset_index(drop=True)

    # ---------- toewijzing aan orders ----------
    per_dag_order = dict(list(pdf.groupby("datum")))
    leeg = pdf.iloc[0:0]
    eff["order"] = [_wijs_toe(r, per_dag_order.get(r.datum, leeg)) for r in eff.itertuples()]
    # Onkosten staan in de urenregels soms op een andere dag van de week dan op de order (maaltijden: de maandag).
    # Staat precies hetzelfde bedrag die week op een order, dan hoort de regel bij die order en niet bij "geen order".
    onkosten = pdf[pdf.post == "onkosten"]
    week_onk = weeksleutels(onkosten.datum, inst)
    for i in eff.index[(eff.order == GEEN) & (eff.post == "onkosten")]:
        zelfde = onkosten[(week_onk == inst.week(eff.datum[i])) & ((onkosten.bedrag - eff.netto[i]).abs() < 0.005)]
        if len(zelfde):
            eff.loc[i, "order"] = zelfde.order.iloc[0]
    u["order"] = eff.order.reindex(u.index).fillna("")

    D = _per_dag(eff, pdf, km_met_bedrag, inst)

    # ---------- controles: alles moet aansluiten op de bronnen ----------
    if abs(D.fact_bedrag_uren.sum() + D.fact_onkosten.sum() - u.netto.sum()) >= 0.01:
        raise ValueError("de urenregels sluiten niet aan op het overzicht per dag")
    if abs(D.order_bedrag_uren.sum() + D.order_onkosten.sum() - pdf.bedrag.sum()) >= 0.01:
        raise ValueError("de orderregels sluiten niet aan op het overzicht per dag")
    som = alle.groupby("order").bedrag.sum()  # ook de vervallen orders: die zitten niet meer in kop
    fout = [k.order for k in kop.itertuples() if abs(som.get(k.order, 0) - k.excl) >= 0.01]
    if fout:
        raise ValueError(f"de regels tellen niet op tot het ordertotaal (ex btw) bij order {', '.join(fout)}")
    s = _saldo(D, eff, saldo, medewerker)
    if (
        abs(
            (s.fact_bedrag - s.order_bedrag).sum()
            - (D.fact_bedrag_uren + D.fact_onkosten - D.order_bedrag_uren - D.order_onkosten).sum()
        )
        >= 0.01
    ):
        extra = ""
        if saldo is not None and (saldo.gefactureerd.notna() | saldo.order.notna()).any():
            extra = f"; ga na of de posten met bedragen in saldo {in_naam(medewerker)}.csv samen nul zijn"
        raise ValueError(f"de saldoregels tellen niet op tot het totale verschil{extra}")

    return Aansluiting(medewerker, inst.rekeningen, inst, D, u, alle, kop, vervallen, bankuit, betaald, bank_tot, s)
