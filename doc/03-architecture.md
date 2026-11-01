# 03 — Architecture

## Flux de données

```
             ┌──────────────────────────────────────────────┐
             │              collect.py (réseau)             │
             │                                              │
  Apple RSS ─┤ collect_apple ────┐                          │
  Audiomack ─┤ collect_audiomack ┼─► sources dict           │
  Spotify ───┤ collect_spotify ──┤   (positions brutes)     │
  YouTube ───┤ collect_youtube ──┘        │                 │
             │                           ▼                 │
             │   enrich_with_deezer  (ISRC, lien, rank)     │
             │   enrich_with_youtube_music (lien d'écoute)  │
             └───────────────────────────┬──────────────────┘
                                         ▼
                              <date>/sources.json
                                         │
                                         ▼
             ┌──────────────────────────────────────────────┐
             │               merge.py (hors ligne)          │
             │  flatten → cluster → build_candidates        │
             │  → certification → tri → apply_movement      │
             └───────┬──────────────────────┬───────────────┘
                     ▼                      ▼
        <date>/DZ_TOP_50_<date>.json   CLASSEMENT.md
                                       CLASSEMENT.html
                                       classement.csv
                     │
                     ▼
             ┌──────────────────────────────────────────────┐
             │        build_playlist.py (rendu)             │
             │  lit le chart (pipeline multi_source)        │
             │  sinon playlist Spotify + appariement        │
             └──────────────────────────────────────────────┘
                     ▼
        PLAYLIST.md, PLAYLIST.html, playlist_spotify.csv,
        <date>/DZ_TOP_50_<date>.html
```

Séparation stricte :

| Couche | Module | Réseau | État |
|---|---|---|---|
| Collecte | `collect.py` | oui | écrit `sources.json` + caches |
| Calcul / rendu du classement | `merge.py` | non | lit `sources.json`, écrit les livrables |
| Rendu « playlist » | `build_playlist.py` | non (sauf `--isrc` / ancien mode) | lit le chart, écrit les tableaux |
| Client Spotify | `spotify_api.py` | oui | appelé uniquement par `--isrc` |

## Modules

### `collect.py` (~720 lignes)

| Fonction | Rôle |
|---|---|
| `collect_apple(limit)` | RSS Apple Marketing Tools, pays `dz`, `most-played` |
| `collect_audiomack()` | page `audiomack.com/geo-charts/playlist/algeria` |
| `extract_audiomack_tracks(html)` | extrait le payload RSC échappé (100 titres) |
| `collect_spotify(playlist_id, limit)` | page `open.spotify.com/embed/playlist/…`, bloc `__NEXT_DATA__` |
| `collect_youtube(key, limit)` | YouTube Data API v3, `chart=mostPopular&regionCode=DZ` (si clé) |
| `clean_video_title` / `clean_youtube_artist` | normalisent les libellés YouTube |
| `deezer_lookup` / `enrich_with_deezer` | ISRC + lien + `rank` par titre (cache mémoire) |
| `enrich_with_youtube_music` | lien d'écoute, seuil 85, cache persistant |
| `collect()` | orchestration, `notes`, renormalisation implicite |
| `main()` | CLI, écriture de `<date>/sources.json` |

### `merge.py` (~815 lignes)

| Fonction | Rôle |
|---|---|
| `flatten(payload)` | aplatit `sources.json` en une liste d'entrées |
| `UnionFind`, `cluster(records)` | regroupement ISRC + titre/artiste |
| `pick_representative(members)` | choisit le libellé à afficher |
| `position_score(position, count)` | formule `100 × (1 − (p−1)/(N−1))` |
| `build_candidates(payload)` | score pondéré, sources, liens |
| `song_id(title, artist)` | SHA-1 tronqué stable |
| `find_previous_chart(root, date)` | dernier snapshot `multi_source` |
| `apply_movement(entries, previous)` | `NEW`, `=`, `+n`, `-n` |
| `overlap_matrix(candidates)` | diagnostic de recouvrement |
| `merge(root, date, min_sources, fill)` | construit le dict du chart |
| `render_markdown` / `render_html` / `write_csv` | livrables |
| `main()` | CLI, sélection du snapshot |

### `build_playlist.py` (~1 030 lignes)

Historiquement : playlist Spotify → tableau de liens. Aujourd'hui :

- si le chart a `pipeline: multi_source`, il **réutilise** `spotify_url` et
  `youtube_music_url` des entrées (aucun appariement par position) ;
- sinon, il reprend l'ancien chemin : lecture de la playlist, appariement par
  titre puis par position, alertes `⚠`.

| Fonction | Rôle |
|---|---|
| `normalize`, `title_key` | normalisation partagée avec `collect`/`merge` |
| `fetch_playlist_tracks` | playlist Spotify via page embed |
| `resolve_links` | appariement chart ↔ playlist (ancien mode) |
| `ytm_search`, `score_candidate`, `best_candidate` | recherche YouTube Music (InnerTube) |
| `resolve_youtube` | cache + seuil + rapport `youtube_a_verifier.txt` |
| `render_markdown`, `render_html`, `write_csv` | tableaux |
| `spotify_search_url`, `link_cell` | repli « Rechercher » quand le titre n'est pas sur Spotify |

### `spotify_api.py` (~320 lignes)

Client minimal de la Spotify Web API (client credentials) : `search_track`,
`get_track`, `enrich_isrc`, cache `spotify_meta.json`. Utilisé seulement par
`build_playlist.py --isrc`. Sans identifiants, le script continue sans ISRC.

## Schémas de données

### `<date>/sources.json` — snapshot brut

```jsonc
{
  "chart": "DZ TOP 50",
  "country": "DZ",
  "date": "2026-11-01",
  "generated_at_utc": "2026-11-01T09:12:44.318...",
  "weights": { "apple_music": 0.35, "audiomack": 0.25, "spotify": 0.25 },
  "sources": {
    "apple_music": {
      "label": "Apple Music DZ",
      "weight": 0.35,
      "period": "quotidien",
      "entries": [
        {
          "position": 1,
          "title": "BbY WOW",
          "artist": "KAROL G, Judeline & rusowsky",
          "url": "https://music.apple.com/dz/album/…",
          "source_id": "6796864754",
          "isrc": "USUG12607940",
          "deezer_rank": 974722,
          "deezer_url": "https://www.deezer.com/track/4208859922",
          "deezer_id": 4208859922,
          "deezer_match": 0.99,
          "youtube_music_url": "https://music.youtube.com/watch?v=ljAH6jNaApU",
          "youtube_music_score": 101.0
        }
      ]
    },
    "audiomack": { "label": "Audiomack DZ", "weight": 0.25, "period": "hebdomadaire",
                   "chart_url": "https://audiomack.com/geo-charts/playlist/algeria",
                   "entries": [ /* 100 entrées, + duration_seconds, genre */ ] },
    "spotify":   { "label": "Spotify (playlist)", "weight": 0.25, "period": "éditorial",
                   "playlist_id": "37i9dQZF1DX7ZqKP81pHlu",
                   "entries": [ /* 50 entrées */ ] }
  },
  "notes": [
    "youtube ignoré : YOUTUBE_API_KEY absente (score renormalisé sur les sources disponibles)"
  ]
}
```

Champs facultatifs : `duration_seconds`, `genre` (Audiomack), `view_count`
(YouTube), `notes` (chaîne vide possible).

### `<date>/DZ_TOP_50_<date>.json` — classement

```jsonc
{
  "chart": "DZ TOP 50",
  "version": "multi_source",
  "chart_date": "2026-11-01",
  "country": "DZ",
  "generated_at_utc": "2026-11-01T09:31:02Z",
  "pipeline": "multi_source",
  "sources": "Apple, Audiomack, Spotify",
  "source_weights": { "apple_music": 0.35, "audiomack": 0.25, "spotify": 0.25 },
  "source_periods": { "apple_music": "quotidien", "audiomack": "hebdomadaire",
                     "spotify": "éditorial" },
  "metrics": "rank_fusion",
  "min_sources": 2,
  "count": 50,
  "certified": 15,
  "overlap": { "apple_music|audiomack": 3, "apple_music|spotify": 1,
               "audiomack|spotify": 13 },
  "movement_available": false,
  "entries": [
    {
      "rank": 1,
      "song_id": "91cdd470c104",
      "title": "Sahran lil",
      "artist": "Cheb Momo & Djalil Palermo",
      "weighted_total": 90.84,
      "weight_total": 0.5,
      "source_count": 2,
      "sources": "Audiomack, Spotify",
      "metrics": "rank_fusion",
      "best_rank": 5,
      "score": 90.84,
      "previous_rank": null,
      "movement": null,
      "movement_label": "NEW",
      "isrc": "DZA102501685",
      "deezer_rank": 449424,
      "view_count": null,
      "positions": { "audiomack": 5, "spotify": 8 },
      "fill": false,
      "spotify_url": "https://open.spotify.com/track/…",
      "apple_url": null,
      "audiomack_url": "https://audiomack.com/chebmomo/song/sahran-lil",
      "youtube_url": null,
      "youtube_music_url": "https://music.youtube.com/watch?v=pUvGklP36Dw",
      "deezer_url": "https://www.deezer.com/track/3544460971"
    }
  ]
}
```

`version` et `pipeline` valent tous deux `multi_source` : `version` décrit le
format, `pipeline` sert de garde-fou à `find_previous_chart`.

### Caches

| Fichier | Clé | Valeur |
|---|---|---|
| `youtube_links.json` | `date|title_key|artiste` | `video_id`, `score`, titre/artiste du candidat |
| `youtube_music_links.json` | `title_key|artiste` | idem, indépendant de la date |
| `spotify_meta.json` | `track_id` | ISRC, durée, nom |

Les caches sont volontairement versionnés : ils rendent les résultats stables et
évitent des centaines d'appels. En cas de doute, les supprimer est sans danger
(reconstruction réseau).

## Arborescence

```
top-50-dz-playlist-python/
├── collect.py                 # collecte réseau + enrichissements
├── merge.py                   # fusion, score, évolution, rendu
├── build_playlist.py          # tableaux « playlist » / liens
├── spotify_api.py             # client Spotify Web API (optionnel)
├── check_sources.py           # diagnostic des sources (aucune écriture)
├── .env.example               # variables facultatives
├── .gitignore
├── README.md                  # présentation courte
├── doc/                       # cette documentation
├── tests/                     # 3 suites de vérifications autonomes
├── CLASSEMENT.md              # livrables du dernier snapshot
├── CLASSEMENT.html
├── classement.csv
├── PLAYLIST.md / .html
├── playlist_spotify.csv
├── youtube_links.json         # caches
├── youtube_music_links.json
├── spotify_meta.json
├── youtube_a_verifier.txt     # titres à vérifier manuellement
├── 2026-11-01/
│   ├── sources.json
│   ├── DZ_TOP_50_20261101.json
│   └── DZ_TOP_50_20261101.html
├── 2026-10-30/                # ancien pipeline (notebook), non comparable
├── 2026-…/                    # snapshots suivants
├── data/history/              # historique CSV de l'ancien pipeline
└── notebooks/TOP_50_DZ_Notebook.ipynb   # moteur v1.0 (archive)
```

## Conventions

- **Aucune dépendance externe.** Tout repose sur `urllib`, `json`, `csv`,
  `hashlib`, `re`, `unicodedata`.
- **UTF-8 partout.** Lecture `utf-8-sig` tolérante au BOM pour les JSON,
  écriture `utf-8` sans BOM, `ensure_ascii=False`.
- **Dates ISO** `AAAA-MM-JJ` pour les dossiers, UTC pour les horodatages.
- **Noms de fichiers stables** : les outils et l'historique s'appuient dessus
  (`DZ_TOP_50_AAAAMMJJ.json`).
- **Arrondis** : score à 2 décimales, `weight_total` à 3.
- **Échec partiel = note, pas exception.** Une source indisponible est notée
  dans `notes` et retirée du calcul ; seule l'absence totale de sources arrête
  le script.
- **Pas d'écriture hors du dépôt** : snapshots, caches et livrables restent dans
  le dossier du projet.

## Historique des pipelines

| Version | Origine | Sorties | Statut |
|---|---|---|---|
| v1.0 — notebook | `notebooks/TOP_50_DZ_Notebook.ipynb` | `2026-10-30/*.csv/.xlsx/.json/.html`, `data/history/` | archive |
| v1.1 — playlist | `build_playlist.py` + `spotify_api.py` | `PLAYLIST.*`, `playlist_spotify.csv` | actif (rendu) |
| v2 — multi-source | `collect.py` + `merge.py` | `sources.json`, `DZ_TOP_50_*.json`, `CLASSEMENT.*`, `classement.csv` | actif (référence) |

Le notebook reste la référence pour les exports `.xlsx` et les graphiques, mais
n'est plus alimenté.

## Points d'extension

| Besoin | Où intervenir |
|---|---|
| Nouvelle source de découverte | `collect.py` (`collect_<source>`), `WEIGHTS`, `merge.source_label`, `period` |
| Nouvelle règle de score | `merge.position_score` / `build_candidates` |
| Nouveau champ de sortie | `build_candidates` → dict du chart → `render_*` → `write_csv` |
| Nouveau format de sortie | fonction `render_<format>` + appel dans `merge.main` |
| Nouvel enrichissement | fonction `enrich_with_<x>` dans `collect.py`, stockage par entrée |
| Nouveau seuil de correspondance | constante en tête de module + option CLI |
