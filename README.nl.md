![Picnic MCP: boodschappen zoeken, recepten beheren, gezond plannen](assets/banner.png)

# picnic-mcp

[English](README.md) | [Deutsch](README.de.md) | **Nederlands** | [Français](README.fr.md)

Een MCP-server en Claude-plugin voor [Picnic](https://picnic.app), de bezorgsupermarkt. Claude kan het assortiment doorzoeken, per ingrediënt het beste product kiezen (goedkoopste, bio, beste prijs-kwaliteit), je winkelmandje vullen, Picnic-recepten met voedingswaarden lezen en eigen recepten in de app opslaan. Je bevestigt en betaalt de bestelling zelf in de Picnic-app, dus Claude rekent nooit zelf af.

De server gebruikt alleen de standaardbibliotheek van Python. Er is niets te installeren van PyPI en geen code van derden om op te vertrouwen. De Picnic-client, de MCP-protocollaag, de inlog via de browser en de opslag van inloggegevens zijn helemaal in deze repository geschreven.

## Eerst lezen

Dit project is onofficieel en niet verbonden met Picnic. Picnic heeft geen openbare API. De endpoints zijn achterhaald door de openbare broncode van het onderhouden project `python-picnic-api2` als referentie te lezen (er wordt niets uit geïmporteerd) en door te testen met een echt account. Picnic kan deze API op elk moment wijzigen of blokkeren, en het gebruik ervan kan in strijd zijn met de gebruiksvoorwaarden. Gebruik het alleen voor je eigen account en voor persoonlijk gebruik.

## Installatie

Je hebt [uv](https://docs.astral.sh/uv/) nodig. Bij de eerste start wordt Python 3.14 gedownload (vastgelegd in `.python-version`) en het project gebouwd, dat kan even duren. Daarna start het in een seconde.

### Als Claude-plugin

```
/plugin marketplace add jlb-jlb/picnic-mcp
/plugin install picnic-mcp@picnic-mcp-marketplace
```

Claude Code vraagt om je Picnic-land (`nl`, `de`, `be` of `fr`) zodra je de plugin inschakelt. De plugin voegt ook een skill toe die Claude de volgorde van de tools uitlegt.

### Als gewone MCP-server

```bash
git clone https://github.com/jlb-jlb/picnic-mcp
claude mcp add picnic --env PICNIC_COUNTRY=nl -- uv run --locked --directory ./picnic-mcp picnic-mcp
```

Gebruik het absolute pad naar de kloon als je client de server vanuit een andere map start.

## Inloggen

Vraag Claude om bij Picnic in te loggen. Claude roept de tool `start_login` aan, die een inlogpagina in je browser opent. Daar vul je e-mailadres en wachtwoord in, kies je sms of e-mail voor de eenmalige code en voer je de code in. Picnic vraagt deze tweede stap bij accounts waar die is ingeschakeld.

De pagina draait op je eigen computer en sluit zodra je klaar bent. Ze stuurt je gegevens alleen naar Picnic. Claude ziet je wachtwoord en de code nooit. Zonder Claude opent dezelfde pagina met `uv run --directory <pad naar deze repository> picnic-mcp login --web`, en een gewone terminalvraag krijg je door `--web` weg te laten. Het kale commando `picnic-mcp` staat alleen in je PATH als je het pakket met `uv tool install` hebt geïnstalleerd.

## Waar je inloggegevens naartoe gaan

| Wat | Waar | Opmerkingen |
|---|---|---|
| Wachtwoord en eenmalige code | Van je browser naar de lokale pagina, door naar Picnic | Alleen tijdens de inlogaanroep in het geheugen. Nooit naar schijf geschreven, nooit gelogd, nooit zichtbaar voor Claude. |
| Sessietoken | De opslag voor inloggegevens van het besturingssysteem | Windows-referentiebeheer, macOS-sleutelhanger of de Linux-bureaubladsleutelhanger via `secret-tool`. Er is geen terugvaloptie met een bestand. |

Zonder opslag voor inloggegevens mislukt het inloggen met een melding die zegt wat je moet installeren. Op Linux is dat `secret-tool` uit libsecret (`libsecret-tools` op Debian en Ubuntu) en een draaiende sleutelhanger zoals GNOME Keyring. Servers zonder scherm en kale WSL hebben die meestal niet.

De inlogpagina is een kortlevende lokale webpagina met deze beveiligingen:

- Ze luistert alleen op `127.0.0.1`, op een willekeurige poort.
- Het adres bevat een willekeurig eenmalig geheim. Elk ander adres geeft 404.
- Ze weigert verzoeken met een andere hostnaam (DNS-rebinding) en formulieren van andere sites.
- Ze zet geen cookies en voert geen scripts uit, en een strikte content security policy verbiedt al het andere.
- De grootte van verzoeken is beperkt, mislukte pogingen zijn beperkt, en de pagina verloopt na vijf minuten.
- Ze logt niets.

## Gegevens en netwerk

De server praat met één plek, de Picnic-API op `storefront-prod.<land>.picnicinternational.com`. Hij stuurt geen telemetrie en slaat geen gespreksinhoud op. Je winkelmandje, recepten en bestelgeschiedenis blijven bij Picnic. Het enige wat op je computer staat, is het sessietoken in de opslag voor inloggegevens. Met `picnic-mcp logout` log je uit, dat verwijdert het opgeslagen token voor je land.

## Tools

| Tool | Wat het doet |
|---|---|
| `start_login` | Opent de hierboven beschreven lokale inlogpagina. |
| `search_products` | Doorzoekt het assortiment. Geeft id, naam, prijs in cent, verpakkingsgrootte en prijs per eenheid. |
| `find_ingredients` | Kiest per ingrediënt het beste product volgens een voorkeur (goedkoopste voor de hoeveelheid, beste prijs per kg, bio, gangbaar, kleinste of grootste verpakking) en geeft de te kopen verpakkingen, de totale kosten en wat er over blijft. Raakt het winkelmandje niet aan. |
| `get_cart` | Toont de artikelen in het winkelmandje en het totaal in cent. |
| `add_to_cart` | Voegt een product toe op id (aantal 1 tot 50). |
| `remove_from_cart` | Haalt een aantal van een product weg. |
| `clear_cart` | Maakt het winkelmandje leeg. |
| `search_recipes` | Doorzoekt de recepten van Picnic en je eigen recepten. Geeft id, naam, duur en labels zoals Vegan. |
| `get_recipe` | Toont een recept met bereidingsstappen, ingrediënten met het bijbehorende product, hoeveelheden en voedingswaarden per portie. |
| `add_recipe_to_cart` | Voegt de ingrediënten van een recept toe aan het winkelmandje zoals de knop alles toevoegen in de app, met porties en optionele ingrediënten om mee te nemen of over te slaan. |
| `remove_recipe_from_cart` | Haalt de ingrediënten van een recept weer uit het winkelmandje. |
| `create_recipe` | Slaat een eigen recept op in de Picnic-app („Eigen recept”) met een naam, porties en product-id's van de ingrediënten. Raakt het winkelmandje niet aan. |
| `delete_recipe` | Verwijdert een recept dat je met `create_recipe` hebt gemaakt. |
| `get_delivery_slots` | Toont boekbare bezorgvensters voor het huidige winkelmandje, het vroegste eerst. Boekt er geen. |
| `get_order_history` | Toont de laatste afgeronde bezorgingen met datum, bezorgvenster en betaald bedrag. De producten zijn niet inbegrepen. |

Elke tool geeft een titel en de hints alleen-lezen, destructief, idempotent en open wereld op, zodat clients kunnen vragen voordat er iets wordt verwijderd.

Een tool om af te rekenen is er bewust niet. De server vult het winkelmandje en jij bevestigt de bestelling in de Picnic-app.

## Voorbeeldvragen

- „Stel drie gezonde avondeten voor volgende week voor die elk binnen 30 minuten klaar zijn en ingrediënten delen. Een kilo wortels of een bosje koriander moet dus in twee recepten opgaan. Controleer de voedingswaarden per portie en laat me dan één gezamenlijke boodschappenlijst met de totale kosten zien voordat je iets aan het winkelmandje toevoegt.”
- „Ik kook voor twee personen. Zoek bij Picnic de beste producten voor 400 g spaghetti, 600 g wortels, 3 eieren, 200 g feta en een bosje peterselie. Kies bio bij de groenten en het goedkoopste product bij de rest, en laat de verpakkingen, het totaal en wat er overblijft zien.”
- „Zoek een veganistisch linzenrecept van minder dan 30 minuten met minstens 20 g eiwit per portie, voeg het toe voor twee personen en vertel me welke bezorgvensters vrijdagavond vrij zijn.”
- „Plan vijf snelle lunches die ik zondag kan voorbereiden voor de werkweek, vegetarisch en eiwitrijk, met zo weinig mogelijk verschillende ingrediënten. Zoek bij Picnic naar receptideeën en laat de kosten per portie zien.”
- „Sla mijn linzensoep op als Picnic-recept voor vier porties met deze ingrediënten, zodat ik het in de app met één tik aan het winkelmandje kan toevoegen.”

## Goed om te weten

- Picnic verkoopt hele verpakkingen. Een recept dat je zelf maakt, bevat producten en aantallen verpakkingen, niet „3 wortels”, en de app heeft voor eigen recepten geen plek voor bereidingsstappen of een onderdeel „thuis aanwezig”. Bewaar die in je notities.
- Bio wordt herkend aan de productnaam (Bio, Bioland, Demeter, Naturland), omdat de zoekresultaten geen biokenmerk bevatten.
- `find_ingredients` markeert een resultaat als los als geen enkele productnaam alle woorden van de zoekopdracht bevat.
- De voedingswaarden komen uit de eigen recepten van Picnic en zijn schattingen.

## Problemen oplossen

| Probleem | Wat te doen |
|---|---|
| „no stored session” | Vraag Claude om in te loggen of voer `uv run --directory <pad naar deze repository> picnic-mcp login --web` uit. |
| Inloggen mislukt direct na de code | Controleer of de landinstelling bij je account past. Codes verlopen snel. |
| „no desktop keyring found” (Linux) | Installeer `libsecret-tools` en een sleutelhanger zoals `gnome-keyring` en werk in een bureaubladsessie. |
| HTTP 403 bij elke aanroep | Het sessietoken is verlopen. Log opnieuw in. |
| De eerste start is traag | uv downloadt Python en bouwt het project. Latere starts zijn snel. |
| Umlauten zien er stuk uit | Werk bij naar de huidige versie, die op Windows UTF-8 leest en schrijft. |

## Ontwikkeling

```bash
uv run python -m unittest discover -s tests
```

De tests draaien offline tegen een nagebootste netwerklaag. De continuous integration voert ze uit op Windows, macOS en Linux en doet daarnaast een echte rondgang tegen de opslag voor inloggegevens van elk systeem.

Uitbrengen: verhoog de versie in `pyproject.toml`, `.claude-plugin/plugin.json` en `src/picnic_mcp/server.py`, voer `uv lock` uit en zet `ref` en `version` van het pluginitem in `.claude-plugin/marketplace.json` op de nieuwe tag. Commit en push daarna een tag zoals `v0.3.0`. De releaseworkflow controleert of de tag bij de versies past, draait de tests en publiceert de GitHub-release. Plugin-installaties volgen de tag uit het marketplacebestand en niet `main`.

Opbouw:

- `src/picnic_mcp/client.py`: HTTP-client (`urllib`), inloggen, zoeken, winkelmandje, recepten, bezorgingen en hulpfuncties die antwoorden voor het model inkorten.
- `src/picnic_mcp/finder.py`: de ingrediëntenzoeker (verpakkingen lezen, bio herkennen, rangschikking).
- `src/picnic_mcp/auth.py`, `weblogin.py`, `secretstore.py`: inloggen, de inlogpagina in de browser en de opslag voor inloggegevens.
- `src/picnic_mcp/server.py`: JSON-RPC 2.0 over stdio, toolregister, annotaties en controle van argumenten.
- `.claude-plugin/` en `skills/`: het pluginmanifest, het marketplacebestand en de boodschappenskill.
- `tests/`: offline tests.

## Ideeën voor later

- Voedingswaarden opzoeken via Open Food Facts voor gezonde maaltijdplanning.
- Een MCP-prompt `plan_week` die voedingsdoelen en voorkeuren omzet in een winkelmandje.

## Licentie

MIT, zie [LICENSE](LICENSE).
