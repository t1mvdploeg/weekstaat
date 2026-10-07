# Sjablonen

Weekstaat leest alles wat je aanlevert uit een vaste vorm: een map met bestanden die de kolommen hebben die hieronder staan. Komt een export uit een ander systeem, bouw hem dan om naar deze kolommen. Voor een urenrapport uit Salesforce (geëxporteerd als Excel-bestand), een export van de Rabobank en een debiteurenkaart uit de boekhouding doet de tool dat zelf, met `weekstaat omzetten-uren`, `omzetten-bank` en `omzetten-kaart`. Voor elke andere vorm laat de [skill](../skill/weekstaat/SKILL.md) Claude de kolommen toewijzen en daarna het omgebouwde bestand controleren tegen je export. In deze map staat van elk bestand een kort voorbeeld.

## Wat in de map staat

| Bestand | Verplicht | Waar het voor dient |
| --- | --- | --- |
| `instellingen.toml` | ja | De entiteiten van de opdrachtgever, de rekeningen van het bureau en de vaste waarden |
| `uren.csv` | ja | Eén regel per gefactureerde urenregel |
| `orders/`, `mail/` | een van beide of `orders.csv` | Orders als pdf, ook als bijlage van een `.eml` |
| `orders.csv` | een van beide of de pdf's | Orders in sjabloonvorm, voor wat de tool niet uit een pdf kan lezen |
| `bank.csv` | nee | Ontvangsten op de rekeningen van het bureau; zonder bank is er geen betaling om te vergelijken |
| `debiteurenkaart.csv` | nee | Wat in de boekhouding nog openstaat; het blad Debiteurenkaart komt er alleen met een kaart én een bank |
| `opmerkingen.csv` | nee | Je eigen opmerkingen, die de automatische vervangen of aanvullen |
| `to-do.md` | nee | De actielijst; zonder dit bestand maakt de tool er een uit de bevindingen |
| `zonder-order.csv` | nee | Het schriftelijke stuk van de opdrachtgever bij uren zonder order |
| `toezegging.csv` | nee | Een ander schriftelijk stuk van de opdrachtgever voor een week of maand waarvoor geen order is |
| `urenstaat-afwijkend.csv` | nee | Weken waar de goedgekeurde urenstaat niet de som van de uren is |
| `bank-notities.csv` | nee | Je eigen opmerking bij een bankboeking |
| `vervallen-orders.csv` | nee | Orders die door een andere zijn vervangen |
| `saldo <naam>.csv` | nee | Je eigen oordeel over een verschil in een week |

## De regels voor alle bestanden

Een csv heeft altijd dezelfde vorm: puntkomma tussen de kolommen, komma als decimaalteken en datums als dag-maand-jaar (`31-12-2025`). Zo open en bewerk je hem in een Nederlandse Excel. Elk bestand met kolommen mag ook een Excel-werkboek zijn met dezelfde kolommen op het eerste blad, met dezelfde bestandsnaam en de extensie `.xlsx`. Staan beide in de map, dan wint de csv. In Excel mogen datums en bedragen echte waarden zijn of tekst in dezelfde schrijfwijze als in de csv.

- Alle kolommen van een bestand moeten er staan, ook als ze leeg blijven. Alleen `rij` in `uren.csv`, `referentie` in `bank.csv` en `bevinding` in `opmerkingen.csv` mogen ontbreken.
- Orders uit pdf en uit `orders.csv` mogen samen. Hetzelfde ordernummer uit beide bronnen telt één keer, en de pdf gaat voor; de tool meldt dat met een regel die met "let op:" begint.
- De regels van een order moeten optellen tot het totaal exclusief btw. Zo niet, dan stopt de tool met het ordernummer en het verschil in de melding. Staat een hele order twee keer in `orders.csv` (alle regels dubbel en samen twee keer het totaal), dan zegt de melding dat de order twee keer in het bestand staat.
- Een bankboeking met een `referentie` telt één keer als ook rekening, datum en bedrag gelijk zijn. Zo mogen exporten die elkaar overlappen achter elkaar staan. Dezelfde referentie bij een andere rekening, datum of een ander bedrag is een andere boeking; de tool laat beide staan en meldt het met een regel die met "let op:" begint. Een boeking zonder referentie telt altijd.
- Een bedrag op de debiteurenkaart staat zoals op de kaart: een factuur positief, een ontvangst negatief. Een `debiteurenkaart.csv` met alleen een kopregel is een kaart waarop niets meer openstaat (alles afgeletterd). Dat is iets anders dan geen bestand: dan weet de tool niet wat er openstaat en komt er geen blad Debiteurenkaart.
- Een naam in `orders.csv` vergelijkt de tool op voornaam en laatste woord met de naam in `uren.csv`. In alle andere bestanden met een kolom `medewerker` moet de naam precies zijn zoals in `uren.csv`.
- Tekst die met `=`, `+`, `-` of `@` begint, leest Excel als formule. De tool schrijft er een apostrof voor en haalt die bij het lezen weer weg; laat hem staan.
- Een fout in een bestand geeft een melding met het bestand, de regel en de kolom. Regel 1 is de kopregel.

## `uren.csv`: één regel per gefactureerde urenregel

| Kolom | Wat erin staat |
| --- | --- |
| `medewerker` | Naam. Voornaam en achternaam moeten overeenkomen met de naam op de order; tussenvoegsels en tweede namen mogen verschillen. |
| `urenstaat` | Nummer van de urenstaat of timesheet |
| `datum` | Dag waarop is gewerkt |
| `uren` | Aantal uren; 0 voor een onkostenregel (kilometers, vergoeding) |
| `soort` | Uursoort. De tool verwacht de waarde `Normale Uren` letterlijk zo voor gewone uren; een andere soort met uren (verlof) telt als verlof. Zet bij het ombouwen van een export een andere schrijfwijze om. |
| `tarief` | Uurtarief |
| `status` | Status van de urenstaat. De tool verwacht `Goedgekeurd` letterlijk zo voor de geldende urenstaat (zet bij het ombouwen een andere schrijfwijze om): heeft een medewerker geen enkele regel met die status, dan stopt de tool met een melding. `Goedgekeurd` is de geldende urenstaat in het blad Per week; andere waarden (in de voorbeeldset `Corrected` en `Terugdraaien`) zijn eerdere versies. Of een regel meetelt in de aansluiting bepaalt het bedrag, niet de status. |
| `factuurnummer`, `factuurdatum` | De factuur waarop de regel staat. Alleen een regel met bedrag 0 mag zonder factuurnummer; een regel met een bedrag en zonder nummer is een fout (vul het nummer in, of zet het bedrag op 0 als de regel niet is gefactureerd) |
| `bedrag` | Gefactureerd bedrag zonder btw; 0 voor een gecorrigeerde urenstaat |
| `creditnummer`, `creditbedrag` | De creditfactuur, met een negatief bedrag; anders leeg |
| `factuurbedrag` | Het bedrag van de regel volgens de urenstaat |
| `rij` | Niet verplicht: het rijnummer in de bron. Zonder deze kolom telt de tool de regels van het bestand. |

`medewerker`, `datum`, `uren` en `bedrag` mogen niet leeg zijn.

## Orders

Een order is een pdf met de titel "Factuur Uitgereikt Door Afnemer" (of "Creditfactuur" voor een creditorder) in de opmaak van de voorbeeldset, los of als bijlage van een mail. Die opmaak leest de tool zelf, in `orders/` en `mail/`. Een order in een andere vorm (Word, Excel, een andere pdf-opmaak) zet Claude of jijzelf om in `orders.csv`.

## `orders.csv`: één regel per orderregel

De kop van de order staat op elke regel van die order. Entiteit, factuurdatum en de twee totalen moeten op alle regels van een order gelijk zijn.

| Kolom | Wat erin staat |
| --- | --- |
| `order` | Ordernummer |
| `entiteit` | De code van de entiteit van de opdrachtgever, zoals in `instellingen.toml` |
| `factuurdatum` | Datum van de order |
| `referentie` | De referentie op de order, bijvoorbeeld "week 9-12"; mag leeg |
| `totaal_excl`, `totaal_incl` | Het totaal van de order zonder en met btw, overgenomen uit het stuk zelf. Tel ze niet zelf uit de regels op: de tool doet dat en vergelijkt. |
| `medewerker` | De medewerker op deze regel |
| `project` | Project of omschrijving van het werk |
| `datum` | Dag van de regel |
| `omschrijving` | Wat er is gedaan of vergoed |
| `eenheid` | `Uren` voor uren; `Kilometers` of een andere eenheid telt als onkosten |
| `aantal`, `tarief`, `bedrag` | Aantal, tarief en bedrag zonder btw; bij een creditorder negatief |

Een order zonder regels heeft één regel met alleen de kop; laat de kolommen vanaf `medewerker` dan leeg. Een regel in `eenheid` die niet `Uren` is, telt als onkosten. Uren met een tarief onder de 20 telt de tool als een correctie en niet als gewerkte uren.

## `bank.csv`: één regel per ontvangst

| Kolom | Wat erin staat |
| --- | --- |
| `datum` | Boekdatum |
| `rekening` | IBAN van de eigen rekening waarop het binnenkwam; moet in `instellingen.toml` staan |
| `bedrag` | Ontvangen bedrag |
| `tegenpartij` | Naam van de betaler; hieraan herkent de tool de entiteit. Mag leeg zijn. |
| `omschrijving` | Omschrijving van de boeking |
| `referentie` | Het referentienummer van de bank; de kolom mag ontbreken. `omzetten-bank` vult hem met de boekingsreferentie, anders de transactiereferentie. |

## `debiteurenkaart.csv`: wat nog openstaat in de boekhouding

De kaart toont alleen wat niet is afgeletterd (aan elkaar gekoppeld): een factuur die er niet op staat is betaald, een ontvangst die er niet op staat is aan een factuur gekoppeld.

| Kolom | Wat erin staat |
| --- | --- |
| `datum` | Datum van de factuur of de ontvangst |
| `soort` | `factuur` of `ontvangst` |
| `nummer` | Bij een factuur het factuurnummer; bij een ontvangst leeg |
| `omschrijving` | Bij een ontvangst de omschrijving; bij een factuur leeg |
| `rekening` | Bij een ontvangst het IBAN van de eigen rekening (mag leeg; staat het erin, dan moet het in `instellingen.toml` staan); bij een factuur leeg |
| `bedrag` | Zoals op de kaart: een factuur positief, een ontvangst negatief |

## `opmerkingen.csv`: je eigen opmerkingen

Een opmerking staat op blad Opmerkingen van het werkboek. Elke automatische bevinding (zie `bevindingen.csv` hieronder) waarvan de sleutel niet in kolom `bevinding` staat, komt er zelf bij, met een nummer als `A1`; zo'n automatische opmerking is belangrijk boven de 1.000 en bij een order die niet is betaald. Zonder dit bestand zijn alle opmerkingen automatisch. De kolom Bron van het blad zegt of een opmerking van jou komt of van de tool.

| Kolom | Wat erin staat |
| --- | --- |
| `medewerker` | Naam zoals in `uren.csv`; leeg voor een opmerking die voor alle medewerkers geldt |
| `nr` | Nummer van de opmerking, een tekst naar keuze; de automatische opmerkingen heten `A1`, `A2` ... |
| `belangrijk` | `ja` of `nee`; leeg telt als `nee`. `ja` is voor wat de lezer als eerste moet zien. |
| `onderwerp` | Korte titel; mag niet leeg zijn |
| `bedrag`, `btw` | Het bedrag en of het exclusief (`ex`) of inclusief (`incl`) btw is |
| `wat` | Wat er aan de hand is |
| `bewijs` | Waar het uit blijkt: het bestand, de datum en een citaat |
| `actie` | Wat er moet gebeuren |
| `wie` | Wie aan zet is |
| `bevinding` | De sleutel of sleutels van de bevindingen die deze opmerking vervangt, gescheiden door een komma; mag leeg. Een sleutel die bij geen bevinding hoort, geeft bij het draaien een regel "let op:". |

## `to-do.md`: de actielijst

Een regel `## groep` begint een groep en `- **titel** toelichting` een actie. Tekst op de regels direct onder een actie, zonder streepje, hoort bij de toelichting. Alles vóór de eerste `## groep` wordt overgeslagen, een inleiding of een titel met één `#` mag dus. Backticks en sterretjes gaan uit de tekst; een dubbele punt of punt aan het eind van een titel valt weg. Een streepje zonder vetgedrukte titel, en losse tekst die onder een groep bij geen actie hoort, zijn een fout. Zet er alleen in wat de gebruiker nog moet doen, in de volgorde waarin hij het afwerkt. In de Actielijst staat een actie bij een medewerker als zijn voornaam, volledige naam of achternaam in de titel of toelichting voorkomt; anders staat hij bij Algemeen.

## `zonder-order.csv`: het stuk bij uren zonder order

| Kolom | Wat erin staat |
| --- | --- |
| `medewerker` | Naam zoals in `uren.csv` |
| `week` | De week, als `2024 wk 08` |
| `stuk` | Het andere schriftelijke stuk van de opdrachtgever, meestal een mail, met datum |
| `toelichting` | Wat dat stuk zegt |

## `toezegging.csv`: een ander stuk van de opdrachtgever dan een order

Een schriftelijk stuk van de opdrachtgever voor een week of maand waarvoor geen order is, bijvoorbeeld een gewone inkooporder waarop hij pas betaalt na een factuur van het bureau met het nummer erop. Jij of Claude neemt het regel voor regel over. Een toezegging is geen order: de uren staan er wel op, maar er is nog niets betaald. Eén regel per post: uren hebben een `week`, reiskosten een `maand`.

| Kolom | Wat erin staat |
| --- | --- |
| `medewerker` | Naam zoals in `uren.csv` |
| `nr` | Nummer van de toezegging, als tekst |
| `datum` | Datum van de toezegging |
| `week` | Bij uren de week, als `2024 wk 14` |
| `maand` | Bij reiskosten de maand, als `2024-03`; in Excel mag ook een datum, dan telt de maand ervan |
| `omschrijving`, `eenheid` | Wat er is toegezegd, en in welke eenheid |
| `aantal`, `prijs`, `bedrag` | Aantal, prijs per eenheid en bedrag zonder btw |

## `urenstaat-afwijkend.csv`: weken waar de urenstaat afwijkt

Voor elke week is de goedgekeurde urenstaat wat gefactureerd is. Wijkt hij af, bijvoorbeeld omdat een dag op twee goedgekeurde urenstaten staat, en staat de week hier niet in, dan stopt de tool.

| Kolom | Wat erin staat |
| --- | --- |
| `medewerker` | Naam zoals in `uren.csv` |
| `week` | De week, als `2024 wk 06` |
| `bedrag` | Het bedrag van de goedgekeurde urenstaat |
| `toelichting` | Waarom het afwijkt |

## `bank-notities.csv`: je eigen opmerking bij een bankboeking

| Kolom | Wat erin staat |
| --- | --- |
| `tekst` | Tekst die in de omschrijving van de boeking voorkomt; de eerste regel die past, geldt |
| `betreft` | De voornaam of voornamen van de medewerkers, gescheiden door een komma (`Sanne`, `Sanne, Thijmen`), of een tekst die met `onbekend` of `waarschijnlijk` begint. Hoort de boeking bij een order, dan beslist de order. |
| `opmerking` | De opmerking bij de boeking |

## `vervallen-orders.csv`: orders die zijn vervangen

Een order die de opdrachtgever door een andere heeft vervangen zonder hem te crediteren. Hij telt nergens mee, ook niet voor de bank, maar blijft zichtbaar.

| Kolom | Wat erin staat |
| --- | --- |
| `order` | Het vervallen ordernummer |
| `vervangen_door` | Het ordernummer van de vervanger |
| `toelichting` | Waarom je dat denkt |

## `saldo <naam>.csv`: je eigen oordeel

Eén bestand per medewerker, met de naam zoals in `uren.csv` in de bestandsnaam. Zonder bedragen geeft een regel de verschillen van die week en soort een andere toewijzing; met bedragen is het een extra post. De regel gaat voor het voorstel van de tool. Het voorbeeld in deze map heet `saldo.csv`; hernoem het zo.

| Kolom | Wat erin staat |
| --- | --- |
| `week` | De week, als `2025 wk 24` |
| `soort` | `Uren` of `Onkosten` |
| `gefactureerd`, `order` | Bij een extra post het bedrag aan de kant van het bureau en van de opdrachtgever; anders leeg |
| `toewijzing` | `Opdrachtgever te weinig opgenomen`, `Opdrachtgever te veel opgenomen`, `Bureau te weinig gefactureerd`, `Bureau te veel gefactureerd` of `Nog uitzoeken` |
| `toelichting` | Waarom |

## `instellingen.toml`

Bovenaan staan de vaste waarden, allemaal niet verplicht, met hun standaardwaarde. Daarna komen de entiteiten en de rekeningen.

| Instelling | Standaard | Wat het doet |
| --- | --- | --- |
| `bureau` | `"het bureau"` | Hoe de teksten het bureau noemen |
| `opdrachtgever` | `"de opdrachtgever"` | Hoe de teksten de opdrachtgever noemen |
| `btw` | `0.21` | Btw-tarief van het bureau, een getal van 0 tot 1 |
| `blok` | `"4 weken"` | Waarin de opdrachtgever zijn orders plaatst: `"week"`, `"4 weken"` of `"maand"` |
| `betaaltermijn` | `30` | Aantal dagen na de factuurdatum van een order voordat "niet ontvangen" geldt. Een verzamelbetaling zoekt ook de orders van de betaaltermijn min 10 tot en met de betaaltermijn plus 10 dagen terug, nooit van na de betaaldatum (bij 30 dagen: 20 tot 40). |

Minstens één `[[entiteit]]` en één `[[rekening]]` zijn verplicht.

| Onderdeel | Kolommen | Wat erin staat |
| --- | --- | --- |
| `[[entiteit]]` | `code`, `naam` | De opdrachtgever factureert vanuit een of meer entiteiten. De tool herkent ze aan hun naam in de kop van een order en in de tegenpartij van een bankregel; de langste naam die past, wint. De `code` staat in `orders.csv`. |
| `[[rekening]]` | `naam`, `iban`, `aandeel` | De rekeningen van het bureau waarover een betaling wordt verdeeld. Het IBAN staat zonder spaties. De aandelen tellen op tot 1: bij een G-rekening bijvoorbeeld 0.30 en 0.70, bij één rekening 1. |

## Wat de tool schrijft

`weekstaat aansluiten <map> --uit <map>/uitvoer` schrijft drie soorten bestanden.

| Bestand | Inhoud |
| --- | --- |
| `Overzicht <naam>.xlsx` | Per medewerker. Zichtbaar: Stand, Zonder order, Debiteurenkaart (alleen met kaart en bank), Per periode en Verschillen (met de gele kolom "Eigen keuze", die blijft staan als je opnieuw draait). Verborgen: Opmerkingen, Per week, Per dag, Orders, Facturen, Bank (alleen met bank), Urenregels en Orderregels. |
| `Overzicht totaal.xlsx` | Alle medewerkers samen, ook bij één medewerker. Donkere tabbladen: Samenvatting (één scherm), Actielijst en Zonder order alle (elke factuur zonder order). Grijze tabbladen: Totaal uitgebreid, Opmerkingen en per medewerker Stand en Zonder order. |
| `bevindingen.csv` | Elke afwijking die de tool vond, als lijst. Dit bestand leest de skill. |

De kolommen van `bevindingen.csv`:

| Kolom | Wat erin staat |
| --- | --- |
| `sleutel` | De naam van de bevinding in de vorm `soort|medewerker|periode, week of ordernummer`: de soort in kleine letters met streepjes (`uren-zonder-order`), de medewerker zoals in `uren.csv` of `Algemeen`, en het laatste deel (een komma wordt een plus: `2025 wk 17-20`, `I01250324+I02250348`). De sleutel blijft gelijk zolang de invoer gelijk blijft; je zet hem in kolom `bevinding` van `opmerkingen.csv`. |
| `soort` | Wat voor afwijking het is, uit de lijst hieronder |
| `medewerker` | Voor wie, of `Algemeen` |
| `onderwerp`, `wat` | Korte titel en een zin over wat er aan de hand is |
| `bedrag`, `btw` | Het bedrag en of het `ex` of `incl` btw is; leeg als er geen bedrag bij hoort |
| `bewijs` | Het blad waar het te zien is |
| `actie`, `wie` | Het voorstel voor de actie en wie aan zet is (de opdrachtgever, het bureau of Nog uitzoeken) |
| `groep` | De groep in de actielijst: Naar de opdrachtgever, Zelf doen in de boekhouding, Navragen, Nog uitzoeken of Aanleveren |

De soorten: Uren zonder order, Dagen ontbreken op de order, Uren op een toezegging, Week dubbel op toezegging en order, Order niet betaald, Order deels betaald, Creditorder niet verrekend, Creditorder verrekend, Wel op order, niet gefactureerd, Uren of tarief anders, Dagvergoeding niet op order, Losse onkostenpost niet op order, Onkosten anders dan op de order, Dubbel gefactureerd, Dubbel op orders, Meer gefactureerd dan de urenstaat, Minder gefactureerd dan de urenstaat, Meer betaald dan gefactureerd, Kan worden afgeletterd, Debiteurenkaart sluit niet aan, Vervangen order, Bron ontbreekt, Order voor een medewerker zonder uren en Open facturen buiten de uren. De tool zegt wat hij ziet en wat de lezer kan nagaan; waarom iets zo is, weet hij niet.
