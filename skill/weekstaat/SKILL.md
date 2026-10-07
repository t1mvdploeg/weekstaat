---
name: weekstaat
description: Gebruik bij het aansluiten van gefactureerde uren op de orders van een opdrachtgever, de bank en de debiteurenkaart ("sluit de facturen aan", "klopt wat er betaald is", "leg de uren naast de inleenorders", "waarom is dit niet betaald", "wat staat er open"), bij het ombouwen van een bron in een andere vorm naar de sjablonen (Word, Excel, csv, een urenexport, een bankexport of een order-pdf in een andere opmaak), en bij het schrijven van de opmerkingen en de actielijst bij een aansluiting.
---

# Weekstaat

Weekstaat legt vier bronnen naast elkaar: de uren die het bureau factureerde, de orders van de opdrachtgever (het stuk waarop de opdrachtgever de uren erkent en waarop hij betaalt), de bank en de debiteurenkaart (wat in de boekhouding nog openstaat). De tool rekent en controleert, elke keer op dezelfde manier. Jij doet wat oordeel vraagt: de bronnen herkennen en ombouwen, in mails en pdf's zoeken wat er over een verschil is geschreven, de opmerkingen en acties schrijven en de uitkomst uitleggen. Wat jij schrijft komt in vaste bestanden die de tool terugleest. De kolommen van al die bestanden staan in `sjablonen/README.md` en staan hier niet nog eens.

Er zijn zeven stappen. Elke stap eindigt met een controle. Ga pas verder als die klopt, want een fout in een vroege stap komt in de latere stappen terug als een nette uitkomst.

## 1. Inventaris

Vraag om de map met bestanden en kijk wat erin zit. Verplicht zijn de uren, de orders (pdf's, mails met pdf's als bijlage, of een sjabloon `orders.csv`) en `instellingen.toml`. Nuttig zijn verder de bank, de debiteurenkaart, een toezegging (een ander schriftelijk stuk van de opdrachtgever voor een week of maand waarvoor geen order is, zoals een gewone inkooporder) en de mails met de opdrachtgever.

Zeg eerst wat ontbreekt en wat dat voor de uitkomst betekent: zonder bank is er geen betaling om naast de orders te leggen, zonder kaart geen blad Debiteurenkaart. Vraag of het nog komt en reken pas daarna. Rekenen op een half dossier geeft cijfers die de gebruiker voor waar aanneemt, en bij elke aanvulling moet alles opnieuw. Vraag ook of de bankexport alles omvat: tot welke datum loopt hij, en betaalt de opdrachtgever onder meer dan één naam, dan moet de export op alle namen zijn gezocht.

`instellingen.toml` schrijf je samen met de gebruiker: de entiteiten van de opdrachtgever zoals ze op de orders heten, de rekeningen van het bureau met hun aandeel, en de vaste waarden (btw, blok, betaaltermijn). Raad geen IBAN en geen aandeel.

Controle: de gebruiker heeft de lijst "ligt er, ontbreekt, komt later" gezien.

## 2. Ombouwen naar de sjablonen

Een bron die al de kolommen van het sjabloon heeft, als csv of xlsx, hoeft niet om. Voor de rest zijn er drie opdrachten, die je eerst probeert:

- Uren uit Salesforce als Excel: `weekstaat omzetten-uren <export.xlsx> <map>/uren.csv`.
- Exporten van de Rabobank, ook meer dan één: `weekstaat omzetten-bank <map>/bank.csv <export.csv> ... --instellingen <map>/instellingen.toml`. Met `--debiteurenkaart <kaart.xlsx>` komen ontvangsten die alleen op de kaart staan erbij.
- De debiteurenkaart uit de boekhouding: `weekstaat omzetten-kaart <kaart.xlsx> <map>/debiteurenkaart.csv`. Hij leest het eerste blad met de kopregel op rij 3 en de kolommen Datum, Dagboek, Omschrijving en Te vorderen. Een dagboek dat met V begint is een verkoopfactuur (het factuurnummer staat in de omschrijving), elk ander dagboek een ontvangst (het IBAN van de rekening staat in het dagboek).

Een debiteurenkaart waarop niets meer openstaat, is een bestand met alleen een kopregel. Houd dat bestand: het zegt dat alles is afgeletterd, iets anders dan geen kaart.

Past de vorm niet, dan zegt de tool welke kolom hij mist. Dan lees je de kopregel en een paar rijen van de bron en stel je per kolom van het sjabloon voor waar hij vandaan komt. Leg die toewijzing aan de gebruiker voor, wacht op akkoord en schrijf daarna het sjabloonbestand met een klein script.

Orders in een andere vorm dan de pdf's die de tool leest (een Word-document, een Excel-lijst, een pdf met een andere opmaak) neem je regel voor regel over in `orders.csv`. Bij elke regel staat het totaal van de order exclusief en inclusief btw, en dat totaal neem je over uit het stuk zelf, niet uit de som van je eigen regels. De tool telt de regels per order op en stopt als ze niet gelijk zijn aan dat totaal. Dat is de controle op je werk, en ze werkt alleen als het totaal uit het stuk komt.

Regels bij het ombouwen: verzin geen waarden (ontbreekt een kolom die het sjabloon vraagt, zeg dat en vraag waar hij vandaan moet komen), laat bedragen en datums zoals ze zijn en zet alleen de notatie om, en laat een bedrag op de kaart staan zoals op de kaart: een factuur positief, een ontvangst negatief.

Controle, voor elke omgebouwde bron, ook als de export er bekend uitziet:

1. **Aantal en totaal.** Tel de regels en het totaalbedrag in de bron en in het sjabloonbestand. Ze moeten gelijk zijn; verklaar elk verschil, bijvoorbeeld een voetregel of een lege rij. Bij de kaart is de som het saldo dat de boekhouding noemt.
2. **Steekproef.** Leg vijf regels verspreid over het bestand veld voor veld naast de bron. Neem een onkostenregel en een creditregel mee als die er zijn.
3. **Betekenis.** Staat in elke kolom wat het sjabloon vraagt? Bedragen zonder btw, een credit negatief. Dag en maand niet omgedraaid: zoek een datum met een dag boven de 12. Decimalen goed gelezen (1.234,56 is geen 1,23). Een onkostenregel heeft 0 uren. De namen van de medewerkers komen overeen met die op de orders. Elk IBAN staat in `instellingen.toml`.
4. **Meld het.** Zeg wat je hebt gecontroleerd, wat klopte en wat je niet zeker weet. Twijfel je over de betekenis van een kolom, vraag het dan. Raad niet.

## 3. Draaien

Komt de installatie ter sprake: de tool vraagt Python 3.11 en pandas 3 of nieuwer; `pip install -e .` in de map van de tool haalt dat binnen.

```bash
weekstaat aansluiten <map> --uit <map>/uitvoer
```

Stopt de tool met een melding, dan klopt er iets niet in de invoer. De melding noemt het bestand, de regel en de kolom. Los het op in de invoer, niet in de code en niet door een controle te omzeilen. Drie stops komen vaak voor. Bij een order die niet optelt: `order X: de regels tellen op tot A, het totaal is B` (uit `orders.csv`) of `regels tellen op tot A, totaal exclusief btw is B` (uit een pdf): kijk of je een regel hebt gemist of dubbel overgenomen, en of het totaal uit het stuk komt. Bij `order X staat twee keer in het bestand`: de regels van die order staan twee keer in `orders.csv`; haal de kopie weg. Bij "de goedgekeurde urenstaat wijkt af van wat gefactureerd is in week ...": zoek uit waarom, bijvoorbeeld een dag die op twee goedgekeurde urenstaten staat. Is het verschil echt, zet de week met het goedgekeurde bedrag en een toelichting in `urenstaat-afwijkend.csv`; anders herstel je de uren.

Een regel die met "let op:" begint, is een melding waarbij de tool doorgaat. Lees ze allemaal en ga na of het klopt. Ze zeggen dat een pdf is overgeslagen omdat er geen order in staat (dat kan een order in een andere opmaak zijn; dan ga je terug naar stap 2), dat een pdf dubbel is, dat een order in de pdf en in `orders.csv` staat (de pdf gaat voor), dat een bankreferentie bij meer dan één boeking staat (ga na of het echt twee boekingen zijn en niet één boeking uit twee exporten met een ander bedrag), of dat een opmerking naar een sleutel verwijst die er niet is (stap 5).

De opdrachtregel schrijft drie soorten bestanden in de uitvoermap: per medewerker `Overzicht <naam>.xlsx`, daarnaast `Overzicht totaal.xlsx` en `bevindingen.csv`. Hij eindigt met het aantal bevindingen.

Controle: de tool loopt door, het aantal orders en urenregels in de eerste regel van de uitvoer is wat je in de inventaris verwachtte, en alle "let op:"-regels zijn verklaard.

## 4. Bevindingen uitzoeken

De tool schrijft `bevindingen.csv` in de uitvoermap: elke afwijking die hij vond, met een sleutel, een bedrag, een zin over wat er aan de hand is en een voorstel voor de actie. Lees het bestand helemaal. Zoek per bevinding in de stukken wat erover is geschreven: de mails, de pdf's, de bijlagen. Is er een schriftelijk stuk van de opdrachtgever, wie zei wat en wanneer, en erkent of betwist hij het?

Wat je vindt, verandert soms de cijfers en komt dan in een hulpbestand: een mail die uren erkent in `zonder-order.csv`, een gewone inkooporder in `toezegging.csv`, een order die een andere vervangt in `vervallen-orders.csv`, een eigen oordeel over een week in `saldo <naam>.csv`, een toelichting bij een bankboeking in `bank-notities.csv`. Neem alleen over wat in een stuk staat of wat de gebruiker zegt. Bij een vervangen order vraag je eerst, want die telt daarna nergens meer mee.

Is er veel te lezen, zet dan subagents in. Geef elk de bronnen en de vraag ("wat is er geschreven over de uren van X in week Y?") en niet de uitkomst die je verwacht, anders zoekt hij bevestiging in plaats van bewijs. Lees een citaat zelf na in de bron voordat je het overneemt.

Controle: bij elke bevinding weet je of er bewijs is, met bestand, datum en citaat, of niet.

## 5. Opmerkingen en acties schrijven

Schrijf `opmerkingen.csv` (de opmerkingen bij het werkboek) en `to-do.md` (wat de gebruiker nog moet doen). De regels, met de reden erbij:

- Verzin geen bewijs. De gebruiker legt een opmerking misschien aan de opdrachtgever voor, en verzonnen bewijs is erger dan geen bewijs.
- Zet bij elk bewijs het bestand, de datum en een citaat, zodat de gebruiker het in een minuut terugvindt.
- Wat je niet kunt vinden heet "nog uitzoeken". Schrijf niet dat het niet bestaat: je weet alleen dat je het niet hebt gevonden.
- Bedragen komen uit `bevindingen.csv`, niet uit je hoofd. Een eigen optelling kan afwijken van wat de tool rekent.
- Zet de sleutel van de bevinding in kolom `bevinding`. Zo vervangt jouw opmerking de automatische en staat het punt niet dubbel in het werkboek. Hoort een opmerking bij meer bevindingen, zet dan alle sleutels erin, gescheiden door een komma. Kopieer de sleutel uit `bevindingen.csv`: een sleutel die niet bestaat (een typfout) geeft bij het draaien een "let op:"-regel, en dat is je controle.
- Elk punt in `to-do.md` begint met een vetgedrukte titel, `- **titel** toelichting`, onder een regel `## groep`. Tekst vóór de eerste `## ` wordt overgeslagen, een inleiding mag dus. Een dubbele punt of punt aan het eind van een titel valt weg.
- `to-do.md` bevat alleen wat de gebruiker nog moet doen, in de volgorde waarin hij het afwerkt. Wat al gebeurd is, hoort in de opmerkingen.
- Zet de naam van de medewerker in de actie. De Actielijst zet een actie bij een medewerker als zijn voornaam, volledige naam of achternaam in de titel of de toelichting staat; anders staat hij bij Algemeen. Gebruik daarom de volledige naam: een achternaam wordt herkend vanaf vier letters, en delen twee medewerkers een naam, dan komt de actie bij allebei.
- Schrijf voor de gebruiker zonder jargon: per punt wat de vraag is en wat er van de lezer verwacht wordt.

Controle: lees `opmerkingen.csv` terug tegen `bevindingen.csv`. Elk bedrag is gelijk, elke sleutel bestaat, elk bewijs heeft een bestand, een datum en een citaat. Staat een naam in een hulpbestand niet in de uren, dan geeft het draaien een "let op:"-regel; dat is je controle op typfouten in namen.

## 6. Opnieuw draaien en nalopen

Draai de tool opnieuw: de hulpbestanden uit stap 4 en de opmerkingen uit stap 5 veranderen de uitkomst. Loop dan na:

- Blok 2 van blad Stand (in elk overzicht en in "Totaal uitgebreid") eindigt met "Controle: gelijk aan het verschil tussen factuur en order in blok 1". Staat er "LET OP: sluit niet aan op blok 1", dan klopt er iets in de invoer; zoek uit waar.
- Op blad Zonder order staat naast het totaal "Controle: gelijk aan kolom I in blad Facturen"; het alternatief is "LET OP: sluit niet aan op blad Facturen".
- Op blad Debiteurenkaart (alleen met een kaart én een bank) staat achter de controleregel van blok B "Sluit aan"; het alternatief is "LET OP: sluit niet aan, verschil ...".
- Blok 5 van "Totaal uitgebreid" eindigt met "Sluit aan op het saldo van de debiteurenkaart", of "LET OP: sluit niet aan op het saldo van de debiteurenkaart, verschil ...". Staat er "Niet gecontroleerd", dan heeft de kaart ook open facturen van een medewerker die niet in dit bestand staat.
- Elke actie in `to-do.md` is terug te vinden in de opmerkingen.
- Geen automatische opmerking staat dubbel naast een handmatige: in blad Opmerkingen (kolom Bron) komt elke sleutel uit `bevindingen.csv` in kolom Bevinding één keer voor, handmatig of automatisch.

Een kaart die niet sluit stopt de tool niet. Het is een signaal en geen fout in je invoer: de kaart zegt iets anders dan bank en orders. Zoek de bevinding "Debiteurenkaart sluit niet aan" uit. Kijk of de kaart compleet is (zijn alle ontvangsten en open facturen omgebouwd, is de kaart van dezelfde datum als de bank), of er facturen op staan die in de uren ontbreken ("Open facturen buiten de uren"), en leg de rest aan de gebruiker voor als een open punt met het verschil erbij. Klopt een andere controle niet, zoek de oorzaak dan in je invoer of je opmerkingen en draai opnieuw.

## 7. Uitleggen

Begin met het totaal: hoeveel is gefactureerd, hoeveel staat op orders, hoeveel is betaald en wat is het verschil. Noem daarna de drie grootste punten, elk met de weken en het bedrag. Zeg dan wat de gebruiker moet doen en in welk blad hij het vindt: in `Overzicht totaal.xlsx` staan het totaal op blad Samenvatting, de acties op blad Actielijst en de uitleg bij elk punt, met bewijs, op blad Opmerkingen. Zeg erbij wat je hebt gecontroleerd en wat je niet hebt kunnen vinden.

De toewijzing op blad Verschillen in het overzicht van de medewerker (wie heeft te veel of te weinig) is een voorstel van de tool. Presenteer het als voorstel; de gebruiker beslist, in de gele kolom "Eigen keuze". Die keuze blijft staan als je het werkboek opnieuw maakt.

## Wat de tool niet kan

Hij leest zelf één opmaak van order-pdf's; elke andere opmaak gaat via `orders.csv`, door jou omgebouwd en door de tool op de optelling gecontroleerd. De tekst van een mail leest hij niet (de pdf-bijlagen in `mail/` wel): het zoeken naar bewijs doe jij. Hij beslist niet wie gelijk heeft: hij laat zien wat niet aansluit, en de gebruiker besluit wat daarmee gebeurt.
