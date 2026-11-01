# 01 — Démarrage

Objectif : obtenir `CLASSEMENT.md`, `CLASSEMENT.html` et `classement.csv` à
partir de zéro.

## Prérequis

| Élément | Version / note |
|---|---|
| Python | 3.9 ou plus (testé sur 3.13) |
| Réseau | accès HTTPS à Apple, Audiomack, Spotify, Deezer, YouTube Music |
| Dépendances | **aucune** : bibliothèque standard uniquement |
| Clé API | **aucune** pour le classement de base (voir plus bas) |

```bash
git clone <url-du-depot> top-50-dz-playlist-python
cd top-50-dz-playlist-python
python --version
```

## Étape 1 — Collecter les positions du jour

```bash
python collect.py
```

Ce que fait le script :

1. lit le chart **Apple Music DZ** (50 titres) ;
2. lit le chart **Audiomack « Weekly 100: Algeria »** (100 titres) ;
3. lit la playlist **Spotify « Hot Hit Algeria »** (50 titres) ;
4. ignore YouTube si `YOUTUBE_API_KEY` est absente (et l'écrit dans `notes`) ;
5. enrichit chaque entrée via **Deezer** : ISRC, lien, indice de popularité ;
6. cherche un **lien YouTube Music** par titre (cache + seuil de confiance).

Sortie : `<date>/sources.json`.

Log attendu (les chiffres bougent d'un jour à l'autre) :

```
Apple Music DZ…            50 titres
Audiomack DZ (Weekly 100)… 100 titres
Spotify (playlist)…        50 titres
YouTube DZ…                ⚠ YOUTUBE_API_KEY absente — source ignorée
Enrichissement Deezer (ISRC, popularité)…  196/200 entrées avec ISRC (185 requêtes)
Enrichissement YouTube Music (liens d'écoute)…  157/185 titres uniques avec lien
Écrit : …/2026-11-01/sources.json
```

Durée : ~30 s sans recherche YouTube Music, 1 à 3 min au premier passage
(ensuite le cache évite le réseau).

Options utiles :

| Option | Effet |
|---|---|
| `--date AAAA-MM-JJ` | force la date du snapshot (défaut : aujourd'hui, UTC) |
| `--limit 50` | nombre d'entrées Apple / Spotify |
| `--spotify-playlist <id>` | change la playlist éditoriale |
| `--no-youtube-music` | saute l'enrichissement YouTube Music |
| `--youtube-music-refresh` | ignore le cache et relance les recherches |
| `--youtube-music-threshold 85` | seuil de confiance des correspondances |
| `--output-dir .` | dossier racine des snapshots |

## Étape 2 — Fusionner et classer

```bash
python merge.py
```

Le script ne fait **aucun appel réseau** : il lit le `sources.json` le plus
récent (ou `--date`), regroupe les versions d'un même morceau, calcule les
scores, applique l'évolution, puis écrit :

- `<date>/DZ_TOP_50_<AAAAMMJJ>.json` — le snapshot de classement (schéma de référence) ;
- `CLASSEMENT.md` — tableau Markdown ;
- `CLASSEMENT.html` — même tableau, autonome (CSS inclus) ;
- `classement.csv` — une ligne par titre, colonne `youtube_music_url` incluse.

Log attendu :

```
Classement 2026-11-01 : 50 titres
  15 avec au moins 2 sources concordantes
  recouvrement des sources :
    apple_music & audiomack : 3 titre(s) commun(s)
    apple_music & spotify : 1 titre(s) commun(s)
    audiomack & spotify : 13 titre(s) commun(s)
  mouvement : premier snapshot
```

Options :

| Option | Effet |
|---|---|
| `--date AAAA-MM-JJ` | classe un snapshot précis |
| `--min-sources 2` | seuil de certification (défaut 2) |
| `--no-fill` | ne garde que les titres certifiés (< 50 lignes possibles) |
| `--output-dir .` | racine des snapshots |

## Étape 3 — Générer les tableaux de liens

```bash
python build_playlist.py --youtube --html --html-update-source
```

Depuis que le classement est multi-source, ce script **lit directement les liens
du chart** (`spotify_url`, `youtube_music_url`) : il ne refait pas de recherche
et n'apparie plus les titres par position. Il produit :

- `PLAYLIST.md`, `PLAYLIST.html` ;
- `playlist_spotify.csv` ;
- met à jour `<date>/DZ_TOP_50_<AAAAMMJJ>.html`.

Pour un ancien snapshot sans pipeline multi-source, il retombe sur son
comportement d'origine : lecture de la playlist Spotify et appariement des
titres avec des alertes en cas de divergence.

## Vérifier le résultat

Checklist rapide :

```bash
python -c "import json,pathlib; c=json.loads(pathlib.Path('2026-11-01/DZ_TOP_50_20261101.json').read_text(encoding='utf-8')); print('titres', c['count']); print('certifies', c['certified']); print('mouvement', c['movement_available']); print('liens YT Music', sum(1 for e in c['entries'] if e['youtube_music_url'])); print('top 3', [(e['rank'], e['title']) for e in c['entries'][:3]])"
```

Attendu :

- `count` = 50 (ou moins avec `--no-fill`) ;
- `certified` > 0 (15 le 2026-11-01) ;
- au moins un lien par titre : Spotify/Apple/Audiomack/Deezer selon présence,
  et toujours une entrée YouTube Music (directe ou recherche `↗`) ;
- `CLASSEMENT.html` s'ouvre sans ressource externe (CSS embarqué) ;
- les liens s'ouvrent sans authentification.

## Configuration facultative

Copier `.env.example` vers `.env` puis remplir :

| Variable | Usage | Obligatoire |
|---|---|---|
| `YOUTUBE_API_KEY` | ajoute le chart YouTube DZ au score (25 %) | non |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | ISRC + durée via la Web API (pipeline playlist) | non |

`.env` est ignoré par Git. Sans ces clés, le classement reste complet : les
poids sont renormalisés sur les sources disponibles.

## Rejouer un classement sans réseau

```bash
python merge.py --date 2026-11-01
```

`merge.py` ne dépend que de `<date>/sources.json`. C'est le moyen de vérifier
qu'une modification du calcul ne casse rien, et de régénérer les livrables
après un changement de rendu.

## Windows : encodage de la console

Les titres contiennent de l'arabe et des accents. Si la console affiche des
caractères cassés ou lève `UnicodeEncodeError` :

```powershell
$env:PYTHONIOENCODING = "utf-8"
python collect.py
```

Les fichiers écrits sont **toujours** en UTF-8, indépendamment de la console.
Pour Excel, importer `classement.csv` en précisant l'encodage UTF-8.

## Ce qu'il faut versionner

| À committer | Pourquoi |
|---|---|
| `<date>/sources.json` | permet de rejouer/recalculer un classement |
| `<date>/DZ_TOP_50_*.json` | historique officiel, support de l'évolution |
| `CLASSEMENT.md` / `.html` / `classement.csv` | livrables publiés |
| `youtube_links.json`, `youtube_music_links.json`, `spotify_meta.json` | caches : moins d'appels réseau, résultats stables |

À ne pas committer : `.env`, `__pycache__/`, environnements virtuels.
