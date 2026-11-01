# 04 — Sources

Trois sources **de découverte** alimentent le score ; deux services servent
d'**enrichissement**. Aucune clé n'est nécessaire pour les trois premières.

| Source | Type | Période | Accès | Entrées | Poids |
|---|---|---|---|---:|---:|
| Apple Music DZ | chart pays (usage) | quotidien | RSS public | 50 | 0,35 |
| Audiomack DZ | chart pays « Weekly 100 » | hebdomadaire | page publique | 100 | 0,25 |
| Spotify « Hot Hit Algeria » | playlist éditoriale | éditorial | page embed | 50 | 0,25 |
| YouTube DZ | chart pays (vues) | quotidien | API + clé | 50 | 0,25 (option) |
| Deezer | recherche / métadonnées | — | API publique | — | 0 |
| YouTube Music | liens d'écoute | — | InnerTube | — | 0 |

## Apple Music DZ

```
GET https://rss.applemarketingtools.com/api/v2/dz/music/most-played/50/songs.json
```

- **Ce que ça mesure** : les titres les plus écoutés sur Apple Music en Algérie
  (chart officiel Apple, mis à jour quotidiennement).
- **Ce que ça donne** : `position`, `title`, `artist`, `url`, `source_id`.
- **Pas d'ISRC** : Deezer le fournit ensuite.
- **Particularité** : ce chart est très ouvert sur le rap français et
  international ; il ne représente pas uniquement la scène algérienne.
- **Panne typique** : structure JSON modifiée → `RuntimeError`, source retirée
  et notée dans `notes`.

## Audiomack DZ

```
GET https://audiomack.com/geo-charts/playlist/algeria
```

- **Ce que ça mesure** : le « Weekly 100: Algeria », chart pays d'Audiomack.
- **Période hebdomadaire** : les positions ne changent pas en cours de semaine.
- **Ce que ça donne** : `position`, `title`, `artist`, `url`, `isrc` (souvent),
  `duration_seconds`, `genre`.
- **Parsing** : la page est rendue côté serveur mais embarque un payload React
  échappé. `extract_audiomack_tracks()` remplace `\"` par `"`, repère
  `"tracks":[` puis extrait le tableau équilibré avec `balanced_json_array()`.
  Il n'y a **aucun appel XHR** à reproduire.
- **Fragilité** : si Audiomack change de framework, le marqueur disparaît et la
  source renvoie 0 entrée (test dédié dans `tests/test_collect.py`).

## Spotify — playlist « Hot Hit Algeria »

```
GET https://open.spotify.com/embed/playlist/37i9dQZF1DX7ZqKP81pHlu
```

- **Ce que ça mesure** : une **curation éditoriale** Spotify pour l'Algérie, pas
  un chart d'écoute. C'est assumé : elle apporte un signal local différent
  d'Apple.
- **Ce que ça donne** : `position` (ordre de la playlist), `title`, `artist`,
  `url`, `source_id` (track id).
- **Sans clé** : lecture du bloc `__NEXT_DATA__` de la page embed.
- **Avec clé** (facultatif) : `build_playlist.py --isrc` interroge la Web API
  pour l'ISRC et la durée.
- **Changer de playlist** : `python collect.py --spotify-playlist <id>`.

## YouTube DZ (optionnel)

```
GET https://www.googleapis.com/youtube/v3/videos
    ?part=snippet,statistics
    &chart=mostPopular&regionCode=DZ&videoCategoryId=10
    &maxResults=50&key=…
```

- **Ce que ça mesure** : les vidéos musicales les plus vues en Algérie sur la
  période du chart.
- **Coût** : 1 unité de quota par appel (quota gratuit : 10 000/jour).
- **Ce que ça donne** : `position`, `title` nettoyé, `artist` nettoyé
  (`TIF - Topic`, `…VEVO`), `url`, `source_id`, `view_count`.
- **Sans clé** : source absente, poids renormalisés (41,2 / 29,4 / 29,4).
- **Fiabilité** : `view_count` est conservé pour l'affichage, pas pour le score.

## Deezer — enrichissement uniquement

```
GET https://api.deezer.com/search?q=<titre artiste>
```

- **Rôle** : ISRC (clé de fusion), lien d'écoute, `rank` de popularité.
- **Pas dans le score** : l'API publique n'expose **aucun** chart pays.
  Le `rank` est un indice global, sans lecture « Algérie » — l'utiliser comme
  poids aurait été arbitraire.
- **Appariement** : similarité titre/artiste, seuil `DEEZER_MATCH_THRESHOLD`
  (0,82), pause de 0,15 s entre requêtes.
- **Économie** : une requête par titre unique (~185 pour un snapshot complet),
  en mémoire seulement (pas de cache persistant).

## YouTube Music — liens d'écoute

Recherche InnerTube `WEB_REMIX` (sans clé), trois requêtes par titre :

1. `titre + artiste`
2. `titre + premier artiste`
3. `titre` seul

Score du meilleur candidat : similarité de titre (60), inclusion (15), artiste
en commun (20), type *art track* (6) ou *official music video* (2), malus pour
les mots parasites. Seuil : **85**.

- Lien retenu : `https://music.youtube.com/watch?v=<video_id>`.
- Sous le seuil : lien de **recherche** dans le rendu, marqué `↗`.
- Cache : `youtube_music_links.json`, alimenté aussi par les correspondances
  déjà trouvées par `build_playlist.py`.
- Résultat au 2026-11-01 : 157/185 entrées liées, soit 40/50 titres du
  classement en lien direct.

## Sources écartées (et pourquoi)

| Piste | Résultat | Preuve / raison |
|---|---|---|
| `charts.youtube.com` pays DZ | retombe sur le chart **mondial** | appel `youtubei/v1/browse` avec `chart_params_country_code=DZ` : la réponse est identique à `global` ; FR et EG fonctionnent |
| YouTube Music Charts | classements verrouillés sur le pays de la machine | impossible de forcer `DZ` de façon fiable |
| Spotify « Top 50 – Algérie » | inexistant | playlists candidates vides, gelées ou hors sujet |
| Anghami | HTTP 403 | blocage des requêtes automatisées depuis cette machine |
| Shazam | HTTP 405 | idem |
| `FEtrending` (ancienne piste) | HTTP 400 | endpoint retiré |
| Deezer comme source de score | pas de chart pays | API limitée à la recherche / métadonnées |

Ces vérifications sont datées du 2026-11-01 ; elles peuvent être refaites avec
les commandes ci-dessous.

## Recouvrement mesuré (2026-11-01)

| Comparaison | Titres communs | Lecture |
|---|---:|---|
| Apple ∩ Audiomack | 3 / 50 | deux univers différents |
| Apple ∩ Spotify | 1 / 50 | presque disjoints |
| Audiomack ∩ Spotify | 13 / 50 | la scène locale se recoupe réellement |

Conséquence : la règle « ≥ 2 sources » certifie la **scène locale**, et les
titres purement Apple (35 le 2026-11-01) remplissent les compléments. Ce n'est
pas un défaut de collecte : c'est le résultat le plus informatif du projet.

## Vérifier qu'une source est vivante

```bash
python check_sources.py            # Apple, Audiomack, Spotify
python check_sources.py --youtube  # + YouTube (si la clé est configurée)
```

Sortie attendue :

```
✓ Apple Music DZ           5 entrées · #1 BbY WOW — KAROL G, Judeline & rusowsky
✓ Audiomack DZ           100 entrées · #1 Sabran — Djalil Palermo
✓ Spotify (playlist)      50 entrées · #1 Sabran — Djalil Palermo
```

Le script n'écrit rien et sort en code 1 si une source est en panne. Si une
seule source échoue, le pipeline continue avec les autres : le classement reste
produit, mais **la couverture baisse** et `notes` le signale.
