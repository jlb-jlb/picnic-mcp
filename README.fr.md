![Picnic MCP : chercher des courses, gérer des recettes, planifier des repas sains](assets/banner.png)

# picnic-mcp

[English](README.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | **Français**

Un serveur MCP et un plugin Claude pour [Picnic](https://picnic.app), le supermarché en ligne livré à domicile. Claude peut parcourir le catalogue, choisir le meilleur produit pour chaque ingrédient (le moins cher, bio, meilleur rapport qualité-prix), remplir ton panier, lire les recettes Picnic avec leurs valeurs nutritionnelles et enregistrer tes propres recettes dans l'application. Tu confirmes et paies la commande dans l'application Picnic, Claude ne valide donc jamais la commande à ta place.

Le serveur n'utilise que la bibliothèque standard de Python. Rien à installer depuis PyPI et aucun code tiers à qui faire confiance. Le client Picnic, la couche du protocole MCP, la connexion par navigateur et le stockage des identifiants sont entièrement écrits dans ce dépôt.

## À lire d'abord

Ce projet est non officiel et n'est pas affilié à Picnic. Picnic n'a pas d'API publique. Les points d'accès ont été déterminés en lisant le code source public du projet maintenu `python-picnic-api2` comme référence (rien n'en est importé) et en testant avec un vrai compte. Picnic peut modifier ou bloquer cette API à tout moment, et son usage peut aller à l'encontre des conditions d'utilisation. Utilise-le uniquement pour ton propre compte et à titre personnel.

## Installation

Il te faut [uv](https://docs.astral.sh/uv/). Au premier démarrage, Python 3.14 (fixé dans `.python-version`) est téléchargé et le projet est construit, ce qui peut prendre un moment. Ensuite, le démarrage prend une seconde.

### Comme plugin Claude

```
/plugin marketplace add jlb-jlb/picnic-mcp
/plugin install picnic-mcp@picnic-mcp-marketplace
```

Claude Code demande ton pays Picnic (`nl`, `de`, `be` ou `fr`) quand tu actives le plugin. Le plugin ajoute aussi une skill qui explique à Claude l'ordre d'utilisation des outils.

### Comme simple serveur MCP

```bash
git clone https://github.com/jlb-jlb/picnic-mcp
claude mcp add picnic --env PICNIC_COUNTRY=fr -- uv run --locked --directory ./picnic-mcp picnic-mcp
```

Utilise le chemin absolu du clone si ton client lance le serveur depuis un autre dossier.

## Se connecter

Demande à Claude de se connecter à Picnic. Il appelle l'outil `start_login`, qui ouvre une page de connexion dans ton navigateur. Tu y saisis ton e-mail et ton mot de passe, tu choisis SMS ou e-mail pour le code à usage unique, puis tu saisis le code. Picnic exige cette double authentification sur les comptes où elle est activée.

La page tourne sur ton propre ordinateur et se ferme quand tu as terminé. Elle n'envoie tes informations qu'à Picnic. Claude ne voit jamais ton mot de passe ni le code. Sans Claude, la même page s'ouvre avec `uv run --directory <chemin de ce dépôt> picnic-mcp login --web`, et une invite simple dans le terminal est disponible en retirant `--web`. La commande `picnic-mcp` seule n'existe dans ton PATH que si tu as installé le paquet avec `uv tool install`.

## Où vont tes identifiants

| Quoi | Où | Remarques |
|---|---|---|
| Mot de passe et code à usage unique | De ton navigateur vers la page locale, puis vers Picnic | Gardés en mémoire uniquement pendant l'appel de connexion. Jamais écrits sur le disque, jamais journalisés, jamais visibles par Claude. |
| Jeton de session | Le magasin d'identifiants du système d'exploitation | Gestionnaire d'identifiants de Windows, trousseau macOS ou trousseau de bureau Linux via `secret-tool`. Il n'y a pas de repli vers un fichier. |

Sans magasin d'identifiants, la connexion échoue avec un message qui indique quoi installer. Sous Linux, il s'agit de `secret-tool` de libsecret (`libsecret-tools` sur Debian et Ubuntu) et d'un trousseau en cours d'exécution comme GNOME Keyring. Les serveurs sans écran et un WSL nu n'en ont généralement pas.

La page de connexion est une page web locale de courte durée avec ces protections :

- Elle n'écoute que sur `127.0.0.1`, sur un port aléatoire.
- L'adresse contient un secret aléatoire à usage unique. Toute autre adresse renvoie 404.
- Elle refuse les requêtes avec un autre nom d'hôte (DNS rebinding) et les formulaires venant d'autres sites.
- Elle ne pose aucun cookie et n'exécute aucun script, et une politique de sécurité du contenu stricte interdit tout le reste.
- La taille des requêtes est limitée, les tentatives échouées sont limitées, et la page expire au bout de cinq minutes.
- Elle ne journalise rien.

## Données et réseau

Le serveur ne communique qu'avec un seul endroit, l'API Picnic à `storefront-prod.<pays>.picnicinternational.com`. Il n'envoie aucune télémétrie et ne stocke aucun contenu de conversation. Ton panier, tes recettes et ton historique de commandes restent chez Picnic. La seule chose stockée sur ton ordinateur est le jeton de session dans le magasin d'identifiants. Avec `picnic-mcp logout`, tu te déconnectes, ce qui supprime le jeton enregistré pour ton pays.

## Outils

| Outil | Ce qu'il fait |
|---|---|
| `start_login` | Ouvre la page de connexion locale décrite ci-dessus. |
| `search_products` | Cherche dans le catalogue. Renvoie l'identifiant, le nom, le prix en centimes, la taille du conditionnement et le prix à l'unité. |
| `find_ingredients` | Choisit par ingrédient le meilleur produit selon une préférence (le moins cher pour la quantité, meilleur prix au kilo, bio, conventionnel, plus petit ou plus grand conditionnement) et renvoie les conditionnements à acheter, le coût total et le reste. Ne touche pas au panier. |
| `get_cart` | Affiche les articles du panier et le total en centimes. |
| `add_to_cart` | Ajoute un produit par identifiant (quantité de 1 à 50). |
| `remove_from_cart` | Retire une quantité d'un produit. |
| `clear_cart` | Vide le panier. |
| `search_recipes` | Cherche dans les recettes de Picnic et dans les tiennes. Renvoie l'identifiant, le nom, la durée et des étiquettes comme Vegan. |
| `get_recipe` | Affiche une recette avec les étapes de préparation, les ingrédients et le produit associé, les quantités et les valeurs nutritionnelles par portion. |
| `add_recipe_to_cart` | Ajoute les ingrédients d'une recette au panier comme le bouton tout ajouter de l'application, avec les portions et des ingrédients facultatifs à inclure ou à ignorer. |
| `remove_recipe_from_cart` | Retire de nouveau du panier les ingrédients d'une recette. |
| `create_recipe` | Enregistre ta propre recette dans l'application Picnic (« Eigenes Rezept ») à partir d'un nom, de portions et des identifiants de produits des ingrédients. Ne touche pas au panier. |
| `delete_recipe` | Supprime une recette que tu as créée avec `create_recipe`. |
| `get_delivery_slots` | Liste les créneaux de livraison réservables pour le panier actuel, le plus proche en premier. N'en réserve aucun. |
| `get_order_history` | Liste les dernières livraisons terminées avec la date, le créneau et le montant payé. Les produits n'y figurent pas. |

Chaque outil déclare un titre ainsi que les indications lecture seule, destructif, idempotent et monde ouvert, pour que les clients puissent demander confirmation avant ce qui supprime des données.

Il n'y a volontairement pas d'outil pour valider la commande. Le serveur remplit le panier et tu confirmes la commande dans l'application Picnic.

## Exemples de demandes

- « Connecte-toi à Picnic, trouve ensuite les produits les moins chers pour 400 g de spaghettis, 600 g de carottes et 3 œufs et montre-moi le total avant d'ajouter quoi que ce soit. »
- « Cherche chez Picnic une recette végane aux lentilles de moins de 30 minutes, montre-moi les valeurs nutritionnelles par portion et ajoute-la pour deux personnes. »
- « Je veux des légumes bio et les pâtes les moins chères. Compose un panier pour ces cinq ingrédients. »
- « Cherche chez Picnic un dîner sain prêt en moins de 30 minutes et ajoute-le pour deux personnes. »

## Bon à savoir

- Picnic vend des conditionnements entiers. Une recette que tu crées contient des produits et des nombres de conditionnements, pas « 3 carottes », et l'application n'offre pas de place pour les étapes de préparation ni pour une section « déjà à la maison » dans tes propres recettes. Garde-les dans tes notes.
- Le bio est reconnu d'après le nom du produit (Bio, Bioland, Demeter, Naturland), car les résultats de recherche ne portent pas d'indicateur bio.
- `find_ingredients` signale un résultat comme approximatif quand aucun nom de produit ne contient tous les mots de la requête.
- Les valeurs nutritionnelles viennent des recettes de Picnic et sont des estimations.

## Dépannage

| Problème | Que faire |
|---|---|
| « no stored session » | Demande à Claude de se connecter, ou exécute `uv run --directory <chemin de ce dépôt> picnic-mcp login --web`. |
| La connexion échoue juste après le code | Vérifie que le réglage du pays correspond à ton compte. Les codes expirent vite. |
| « no desktop keyring found » (Linux) | Installe `libsecret-tools` et un trousseau comme `gnome-keyring`, et travaille dans une session de bureau. |
| HTTP 403 à chaque appel | Le jeton de session a expiré. Reconnecte-toi. |
| Le premier démarrage est lent | uv télécharge Python et construit le projet. Les démarrages suivants sont rapides. |
| Les trémas s'affichent mal | Passe à la version actuelle, qui lit et écrit en UTF-8 sous Windows. |

## Développement

```bash
uv run python -m unittest discover -s tests
```

Les tests s'exécutent hors ligne contre une couche réseau simulée. L'intégration continue les lance sous Windows, macOS et Linux et effectue en plus un vrai aller-retour avec le magasin d'identifiants de chaque système.

Structure :

- `src/picnic_mcp/client.py` : client HTTP (`urllib`), connexion, recherche, panier, recettes, livraisons et fonctions qui raccourcissent les réponses pour le modèle.
- `src/picnic_mcp/finder.py` : le chercheur d'ingrédients (lecture des conditionnements, détection du bio, classement).
- `src/picnic_mcp/auth.py`, `weblogin.py`, `secretstore.py` : connexion, la page de connexion dans le navigateur et les magasins d'identifiants.
- `src/picnic_mcp/server.py` : JSON-RPC 2.0 sur stdio, registre des outils, annotations et validation des arguments.
- `.claude-plugin/` et `skills/` : le manifeste du plugin, le fichier de marketplace et la skill de courses.
- `tests/` : tests hors ligne.

## Idées pour plus tard

- Recherche de valeurs nutritionnelles via Open Food Facts pour planifier des repas sains.
- Un prompt MCP `plan_week` qui transforme les objectifs alimentaires et les préférences en panier.

## Licence

MIT, voir [LICENSE](LICENSE).
