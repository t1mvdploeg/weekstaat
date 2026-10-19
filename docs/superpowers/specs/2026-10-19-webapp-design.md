# Webapp: ontwerp

`weekstaat web` start een lokale webapp bovenop de bestaande opdracht `aansluiten`. De rekenregels en de bestanden die de tool wegschrijft veranderen niet.

## Voor wie en wanneer

Voor wie Weekstaat zelf draait en de uitkomst liever leest dan in de terminal ziet: na een aansluiting de samenvatting per medewerker bekijken, de bevindingen doorlopen en de Excel-bestanden downloaden. Het is geen dienst op een server.

## Keuzes

- Alleen de standaardbibliotheek (`http.server`), geen nieuwe pakketten; de pagina is gewone HTML, CSS en JavaScript zonder buildstap of externe bronnen.
- De server luistert alleen op `127.0.0.1` (standaard poort 8710), controleert de `Host`- en `Origin`-kop en weigert andere sites. Alles blijft op deze computer; uploads en uitvoer staan in een tijdelijke map die bij afsluiten verdwijnt.
- Het dossier komt binnen als map op de schijf (vooraf ingevuld met `voorbeeld/`) of als bestanden uit de browser.
- Schermen: Dossier (bronnen kiezen en draaien), Overzicht (cijfers per medewerker), Bevindingen (filter, zoek, detail), Export (downloads en de uitvoer van de opdrachtregel).
- `cli._aansluiten` geeft nu de samenvattingen terug; de webapp roept die functie aan en leest `bevindingen.csv`. Wat er op het scherm van de terminal staat blijft gelijk.

## Niet in deze versie

Een scherm met de dagregels per medewerker, het bewaren van eerdere runs, het behouden van eigen keuzes in een bestaand overzicht (elke run schrijft naar een nieuwe map).
