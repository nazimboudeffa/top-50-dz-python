# 06 — Feuille de route

Ce document fixe l'état du projet au **2026-11-22** et ce qui est prévu ensuite.
Il est volontairement indicatif : une version n'est taguée qu'une fois son
contenu vérifié, et aucun changement de score n'est adopté sans backtest.

## Où on en est

| Tag | Date | Contenu |
|---|---|---|
| `v0.1.0` | 2026-11-22 | Base figée : pipeline multi-source, snapshot du 27 collecté le 22, cache YouTube Music. |
| `v0.2.0` | 2026-11-22 | Retry HTTP centralisé (transitoires seulement), sortie UTF-8 sur console Windows, tests de non-régression. |
| `v0.3.0` | 2026-11-22 | Cache ISRC sur disque, `collected_at_utc` distinct de `chart_date`, option `--date-dir-only`. |

Le classement ciblé sur le **2026-11-27** est ici un snapshot complet :
50 rangs, 19 titres certifiés par au moins deux sources, trois sources actives.
Ses données ont été collectées le **2026-11-22** : `chart_date` et
`collected_at_utc` sont
donc deux champs distincts, et c'est normal. Le 27, une collecte réelle devra
être relancée avant toute publication définitive.

Le projet tourne toujours **sans environnement virtuel** et sans dépendance
externe (Python 3.9+). Les consoles Windows en cp1252 sont désormais gérées
directement par `collect.py` et `check_sources.py` : plus besoin de poser
`PYTHONIOENCODING` à la main.

## Principes qui guident la suite

1. **Ne jamais mélanger robustesse et score dans le même tag.** Les correctifs
   ne doivent pas pouvoir modifier silencieusement un classement.
2. **Backtester avant d'adopter.** Toute nouvelle formule est comparée à
   l'actuelle sur les snapshots existants, et les deux versions sont conservées
   côte à côte.
3. **Un snapshot publié est immuable.** On ne réécrit pas `2026-11-01/` ni
   `2026-11-27/` ; on produit une nouvelle version à côté.
4. **Pas de données inventées.** Une source absente est signalée, pas
   remplacée ; une correspondance douteuse devient un lien de recherche.
5. **Pas de venv imposé.** Si des outils de développement sont ajoutés un jour,
   ils restent optionnels et isolés (`.venv/` est déjà ignoré par Git).

## Fait depuis la dernière revue

- Centralisation du retry HTTP dans `collect.py` : 3 tentatives avec backoff
  pour les erreurs `408/425/429/5xx` et les timeouts ; les `4xx` définitifs
  échouent immédiatement. Vérifié en réel : Apple a échoué deux fois puis
  répondu à la troisième tentative.
- Cache ISRC persistant (`isrc_cache.json`) : les correspondances Deezer,
  y compris les absences, sont réutilisées d'une collecte à l'autre.
  `--deezer-cache-refresh` force un recalcul.
- `collected_at_utc` ajouté au classement, avec repli sur `generated_at_utc`
  pour les anciens snapshots.
- `--date-dir-only` sur `merge.py` : écriture du seul JSON daté, sans
  toucher aux livrables racine `CLASSEMENT.md`, `CLASSEMENT.html`,
  `classement.csv`.
- Suites de tests hors ligne : `test_collect` 30, `test_merge` 26,
  `test_spotify_api` 14, `verify_20261127` 16 — toutes au vert.

## Prochaines versions

### `v0.4.0` — identité des titres et qualité des liens

Aucun changement de formule de score prévu ; l'objectif est d'arrêter les
faux mouvements et les faux doublons.

- **`song_id` dérivé de l'ISRC** quand il existe, avec une table d'alias pour
  les titres sans ISRC. Aujourd'hui l'identifiant dépend du libellé du
  représentant : un simple changement de titre crée un faux `NEW`.
- **Garde sur le regroupement** : ne fusionner deux entrées sans artiste que
  si leurs ISRC concordent. Le code actuel peut regrouper deux chanteurs
  différents portant le même titre.
- **Appariement YouTube Music amélioré** : score par tokens en plus de
  `SequenceMatcher`, durée comme critère quand elle est disponible,
  translittération pour les titres arabes. Alimenter un fichier de
  vérification dédié au classement pour les correspondances sous le seuil.
- **Validation du schéma JSON** des snapshots avant fusion, pour détecter
  un `sources.json` tronqué ou incompatible.
- **`algorithm_version`** dans chaque snapshot généré, pour tracer la version
  du code à l'origine du classement.

Critère de réussite : aucun changement de rang inattendu sur les deux
snapshots existants, hors doublons explicitement corrigés.

### `v1.0.0` — premier changement de score (version majeure)

À ne lancer qu'après `v0.4.0`, et seulement si le backtest est concluant.

- Écrire un script de backtest hors ligne sur `2026-11-01` et `2026-11-27` :
  formule actuelle `100 × (1 − (rang−1)/(N−1))` contre un RRF
  `Σ w/(k + rang)` avec `k ≈ 60`.
- Mesurer : churn du top 50, nombre de titres certifiés, stabilité des
  rangs, impact des listes de longueurs différentes (50 contre 100).
- Tester séparément un bonus de couverture (quelques points par source
  supplémentaire) et un indicateur de confiance basé sur la dispersion des
  rangs, plutôt qu'un simple booléen « certifié ».
- Publier les deux classements côte à côte avant de choisir. Les snapshots
  `v0.x` restent intacts ; un champ `algorithm_version` sera ajouté aux
  snapshots à partir de `v0.4.0` pour savoir quel code a produit quel rang.

### `v1.1.0` — exploitation et lisibilité

- Sous-classements « global » (Apple) et « local » (Audiomack + Spotify) :
  leur recouvrement est faible (6 / 1 / 12 titres), forcer un classement
  unique mélange deux univers. Les publier à côté du top fusionné.
- Lissage temporel exponentiel sur 3 à 7 snapshots, en conservant le score
  brut à côté du score lissé.
- CI GitHub Actions : le README montre un exemple mais aucun
  `.github/workflows/` n'existe encore. Les tests sont déjà tous hors ligne,
  donc la CI peut être ajoutée sans clé API.

## Ce qu'on ne prévoit pas

- Repasser en `v2.0.0` immédiatement : la documentation parle de « v2 » comme
  d'un nom de génération interne, pas comme d'un tag SemVer. Le premier tag
  est donc `v0.1.0`, et `v1.0.0` sera réservé au score stabilisé.
- Rétro-dater la collecte du 22 novembre au 27 : la date réelle reste écrite
  dans les fichiers.
- Ajouter une dépendance obligatoire pour le fonctionnement quotidien.

## Décisions en attente

- **Le 27 novembre** : relancer `python collect.py --date 2026-11-27` puis
  `python merge.py --date 2026-11-27` (ou `--date-dir-only` si l'on veut
  d'abord examiner le résultat), et vérifier avant publication.
- **Feu vert pour `v0.4.0`** : identité des titres et liens, sans changement
  de classement.
- **Backtest** : lancer la comparaison de scores seulement après `v0.4.0`.
- **Publication Git** : les tags `v0.1.0` à `v0.3.0` sont poussés sur `origin`
  avec `main`.

## Commandes utiles

```bash
python check_sources.py                                   # santé des sources, aucun fichier écrit
python tests/test_collect.py && python tests/test_merge.py && python tests/test_spotify_api.py
python 2026-11-27/verify_20261127.py                      # cohérence du snapshot ciblé
git tag -l && git log --oneline -5
```
