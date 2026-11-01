# DZ TOP 50

Deux chaînes distinctes, dans le même dépôt :

| Chaîne | Script | Ce qu'elle produit |
|---|---|---|
| **Liste de lecture** | `build_playlist.py` | La même liste en tableau Markdown/CSV/HTML, avec liens Spotify + YouTube Music (lit le classement multi-source ; repli sur la playlist « Hot Hit Algeria » pour les anciens snapshots) |
| **Classement** | `collect.py` → `merge.py` | Un classement multi-source avec scores, évolution et liens vers 5 plateformes |

## Démarrage rapide

```bash
python collect.py        # 1. collecte les positions du jour  -> <date>/sources.json
python merge.py          # 2. fusionne et classe             -> CLASSEMENT.md/.html + classement.csv
```

`collect.py` ajoute par défaut les liens YouTube Music (`--no-youtube-music`
pour les désactiver, `--youtube-music-refresh` pour relancer les recherches).

Pour la liste de liens :

```bash
python build_playlist.py --youtube --html --html-update-source
```

Aucune dépendance externe : uniquement la bibliothèque standard.

### Automatiser une collecte par jour

Une collecte quotidienne est ce qui fait vivre l'évolution (`▲`, `▼`, `=`) :
chaque snapshot est comparé au précédent de même méthode.

**Windows (Planificateur de tâches)** — programme `python`, arguments
`collect.py; merge.py` via un petit `.bat`, répertoire de départ = le dépôt :

```bat
@echo off
cd /d C:\chemin\vers\top-50-dz-playlist-python
python collect.py
python merge.py
```

**GitHub Actions** (`.github/workflows/chart.yml`), tous les jours à 06:00 UTC :

```yaml
on:
  schedule: [{ cron: "0 6 * * *" }]
  workflow_dispatch:
jobs:
  chart:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python collect.py
      - run: python merge.py
      - run: |
          git config user.name "chart-bot"
          git config user.email "bot@users.noreply.github.com"
          git add <date>/ CLASSEMENT.md CLASSEMENT.html classement.csv
          git commit -m "chart: $(date -I)" || true
          git push
```

## Configuration facultative

Copier `.env.example` en `.env` :

| Variable | Usage | Obligatoire ? |
|---|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | ISRC + durée via la Web API (`build_playlist.py --isrc`) | non |
| `YOUTUBE_API_KEY` | Ajoute le chart YouTube Algérie (vues réelles) au classement | non |

Le classement est certifié **sans aucune clé** : Apple Music, Audiomack et
Spotify sont lus sur des pages publiques.

## Méthode de classement

Chaque plateforme fournit une position (1..N). Elle est convertie en score :

```
score = 100 × (1 − (position − 1) / (N − 1))
```

Le score final est la moyenne pondérée des sources présentes pour le titre
(une source absente ne pénalise pas : elle sort du dénominateur). Les poids sont
renormalisés sur les sources réellement disponibles ce jour-là.

| Source | Poids | Nature | Accès |
|---|---:|---|---|
| Apple Music DZ | 35 % | chart pays, quotidien | RSS public, sans clé |
| Audiomack DZ | 25 % | chart pays « Weekly 100 », hebdomadaire | page publique, sans clé |
| Spotify (playlist) | 25 % | playlist éditoriale locale | page embed, sans clé |
| YouTube DZ | 25 % | chart pays, vues réelles | clé API gratuite (facultatif) |

Deezer n'entre **pas** dans le score : l'API publique n'expose pas de chart pays.
Il sert uniquement à enrichir chaque titre (ISRC, lien, indice de popularité
informatif).

Chaque titre reçoit également un **lien YouTube Music** (recherche InnerTube,
sans clé, score de correspondance ≥ 85, cache `youtube_music_links.json`).
Quand aucune correspondance n'est assez fiable, le classement propose un lien de
**recherche** YouTube Music, signalé par `↗`.

Règles :

- **au moins 2 sources concordantes** pour être « confirmé » (`--min-sources`) ;
- les autres titres complètent la liste et sont marqués comme tels ;
- regroupement des versions par **ISRC** quand il est connu, sinon par
  titre normalisé + artiste en commun ;
- `song_id` = SHA-1 tronqué de `titre|artiste` : identifiant stable entre snapshots ;
- l'évolution n'est calculée que contre un snapshot **de même méthode**
  (`pipeline: multi_source`), jamais contre une liste de playlist.

## Limites mesurées le 2026-11-01

Le recouvrement entre sources est faible, et c'est un résultat, pas un bug :

| Comparaison | Titres communs |
|---|---:|
| Apple Music DZ ∩ Audiomack DZ | 3 / 50 |
| Apple Music DZ ∩ Hot Hit Algeria | 1 / 50 |
| Audiomack DZ ∩ Hot Hit Algeria | 13 / 50 |

Deux univers coexistent en Algérie : les **titres globaux** (Apple reflète
l'écoute du pays, très ouverte sur le rap français et international) et la
**scène locale** (Audiomack + playlist éditoriale, qui se recoupent réellement).
La règle des 2 sources certifie donc la scène locale ; le reste du Top 50 est
complété par les titres Apple et signalé comme non certifié.

Vérifications faites pour la source YouTube :

- **Spotify** ne publie pas de « Top 50 – Algérie » (plusieurs playlists
  candidates testées : vides, gelées ou hors sujet) ;
- **YouTube Charts** n'a pas de classement Algérie : l'API publique de
  charts.youtube.com répond correctement pour la France ou l'Égypte, mais
  `country_code=DZ` retombe silencieusement sur le classement **mondial** ;
- **YouTube Music** verrouille ses classements sur le pays de la machine ;
- Anghami et Shazam bloquent les requêtes automatisées depuis cette machine
  (HTTP 403 / 405), même avec un navigateur.

Conclusion : la clé `YOUTUBE_API_KEY` reste une option, mais aucune source
YouTube publique ne fournit un chart DZ sans elle.

## Fichiers

| Fichier | Rôle |
|---|---|
| `build_playlist.py` | Liste Spotify/YouTube, rendu Markdown/CSV/HTML |
| `spotify_api.py` | Authentification Spotify + ISRC/durée (`--isrc`) |
| `collect.py` | Collecte par plateforme + enrichissement Deezer |
| `merge.py` | Fusion, score, historique, rendu du classement |
| `<date>/sources.json` | Snapshot brut du jour (positions par source) |
| `<date>/DZ_TOP_50_<date>.json` | Classement du jour, schéma du dépôt |
| `CLASSEMENT.md` / `.html` / `classement.csv` | Livrables du dernier classement |
| `youtube_links.json` | Cache des correspondances YouTube (évite 50 recherches) |
| `youtube_music_links.json` | Cache des liens YouTube Music du classement |
| `spotify_meta.json` | Cache ISRC/durée |
