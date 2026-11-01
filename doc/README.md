# Documentation — DZ TOP 50

Cette documentation décrit comment le classement est produit, avec quelles
règles, et comment le maintenir. Elle s'adresse à quelqu'un qui découvre le
dépôt comme à celui qui doit le réparer six mois plus tard.

## Le résultat en trois commandes

```bash
python collect.py                                     # 1. collecte  -> <date>/sources.json
python merge.py                                       # 2. fusion    -> CLASSEMENT.md/.html + classement.csv
python build_playlist.py --youtube --html --html-update-source   # 3. tableaux de liens
```

Aucune dépendance externe, aucune clé API obligatoire (Python 3.9+).

## Par où commencer

| Document | Contenu | À lire si… |
|---|---|---|
| [01 — Démarrage](01-demarrage.md) | prérequis, commandes, sorties attendues, vérifications | tu veux juste obtenir le classement |
| [02 — Méthodologie](02-methodologie.md) | formule de score, fusion, certification, évolution, limites | tu veux comprendre **pourquoi** ce rang |
| [03 — Architecture](03-architecture.md) | flux de données, modules, schémas JSON, conventions | tu veux modifier le code |
| [04 — Sources](04-sources.md) | chaque plateforme, accès, période, sources écartées et preuves | tu veux ajouter/remplacer une source |
| [05 — Exploitation](05-exploitation.md) | automatisation, tests, dépannage, quotas, FAQ | tu veux le faire tourner tous les jours |

## Ce que produit le pipeline

| Livrable | Portée | Contenu |
|---|---|---|
| `<date>/sources.json` | snapshot du jour | positions brutes par plateforme + enrichissements |
| `<date>/DZ_TOP_50_<AAAAMMJJ>.json` | snapshot du jour | classement fusionné, scores, évolution, liens |
| `CLASSEMENT.md` / `CLASSEMENT.html` | dernier snapshot | tableau lisible, certifiés puis compléments |
| `classement.csv` | dernier snapshot | même tableau, une ligne par titre |
| `PLAYLIST.md` / `PLAYLIST.html` / `playlist_spotify.csv` | dernier snapshot | variante « liste de lecture » |

## Trois principes structurants

1. **Séparer le réseau du calcul.** `collect.py` parle aux plateformes et écrit
   un snapshot ; `merge.py` ne fait que lire ce snapshot et calculer. Un
   classement est donc reproductible hors ligne à partir d'un `sources.json`.
2. **Ne jamais inventer une donnée.** Pas de clé YouTube ⇒ la source est
   absente, les poids sont renormalisés, et c'est écrit dans `notes`. Pas de
   correspondance fiable ⇒ lien de **recherche** signalé `↗`, pas un lien faux.
3. **Un classement, une méthode.** L'évolution n'est calculée qu'entre deux
   snapshots `pipeline: multi_source` : comparer à une playlist produirait des
   mouvements qui ne veulent rien dire.

## État au 2026-11-01

- 3 sources actives : Apple Music DZ, Audiomack DZ, Spotify (playlist éditoriale).
- 200 entrées collectées, 185 titres uniques, 196/200 avec ISRC.
- 50 titres classés, **15 certifiés** par ≥ 2 sources, 35 en compléments.
- 40/50 liens YouTube Music directs, 10 liens de recherche.
- Pas encore d'évolution : il n'existe qu'un seul snapshot `multi_source`.
  Le dossier `2026-10-30/` provient de l'ancien notebook et n'est pas comparable.
