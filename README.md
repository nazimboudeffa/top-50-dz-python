# DZ TOP 50

Deux chaînes distinctes, dans le même dépôt :

| Chaîne | Script | Ce qu'elle produit |
|---|---|---|
| **Liste de lecture** | `build_playlist.py` | Une liste de 50 titres avec liens Spotify + YouTube, à partir de la playlist « Hot Hit Algeria » |
| **Classement** | `collect.py` → `merge.py` | Un classement multi-source avec scores, évolution et liens vers 4 plateformes |

## Démarrage rapide

```bash
python collect.py        # 1. collecte les positions du jour  -> <date>/sources.json
python merge.py          # 2. fusionne et classe             -> CLASSEMENT.md/.html + classement.csv
```

Pour la liste de liens :

```bash
python build_playlist.py --youtube --html --html-update-source
```

Aucune dépendance externe : uniquement la bibliothèque standard.

## Configuration facultative

Copier `.env.example` en `.env` :

| Variable | Usage | Obligatoire ? |
|---|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | ISRC + durée via la Web API (`build_playlist.py --isrc`) | non |
| `YOUTUBE_API_KEY` | Chart YouTube Algérie (vues réelles) | **oui pour un classement certifié** |

## Méthode de classement

Chaque plateforme fournit une position (1..N). Elle est convertie en score :

```
score = 100 × (1 − (position − 1) / (N − 1))
```

Le score final est la moyenne pondérée des sources présentes pour le titre
(une source absente ne pénalise pas : elle sort du dénominateur).

| Source | Poids | Nature | Accès |
|---|---:|---|---|
| Apple Music DZ | 35 % | chart pays (magasin) | RSS public, sans clé |
| Spotify (playlist) | 25 % | playlist éditoriale locale | page embed, sans clé |
| YouTube DZ | 25 % | chart pays, vues réelles | clé API gratuite |
| Deezer | 15 % | indice de popularité global | API publique, sans clé |

Règles :

- **au moins 2 sources concordantes** pour être « confirmé » (`--min-sources`) ;
- les autres titres complètent la liste et sont marqués comme tels ;
- regroupement des versions par **ISRC** quand il est connu, sinon par
  titre normalisé + artiste en commun ;
- `song_id` = SHA-1 tronqué de `titre|artiste` : identifiant stable entre snapshots ;
- l'évolution n'est calculée que contre un snapshot **de même méthode**
  (`pipeline: multi_source`), jamais contre une liste de playlist.

## Limite mesurée le 2026-11-01

Le recouvrement entre l'Apple Music DZ et « Hot Hit Algeria » est de **1 titre
sur 50**. Ces deux sources ne mesurent pas le même univers : Apple reflète
l'écoute globale du pays, la playlist est une sélection éditoriale de musique
algérienne.

Spotify **ne publie pas** de classement « Top 50 – Algérie » (vérifié) et
YouTube Music verrouille ses classements sur le pays de la machine sans clé API.
Conclusion : sans `YOUTUBE_API_KEY`, aucune concordance réelle n'est possible.
Le classement produit est alors explicitement signalé comme provisoire.

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
| `spotify_meta.json` | Cache ISRC/durée |
