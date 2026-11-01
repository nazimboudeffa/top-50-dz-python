# 02 — Méthodologie

Le classement est une **fusion de rangs** (*rank fusion*) : chaque plateforme
donne une position, on la convertit en score, puis on moyenne les scores des
plateformes qui connaissent le titre.

## Vue d'ensemble

```
positions brutes              identités                score                sortie
Apple #21  ─┐
Audiomack #1 ─┼─► même morceau ? ─► 59.18, 100, 100 ─► 83.19 ─► rang 2
Spotify #1  ─┘   (ISRC / titre+artiste)
```

Cinq étapes : **identité → score par source → fusion pondérée → certification →
évolution**.

## 1. Résoudre l'identité d'un morceau

Un même titre apparaît avec des libellés différents :

| Plateforme | Titre | Artiste |
|---|---|---|
| Apple | `Sabran` | `Djalil Palermo` |
| Audiomack | `Sabran` | `Djalil Palermo` |
| Spotify | `Sabran` | `Djalil Palermo` |
| YouTube Music | `Sabran` | `Djalil Palermo - Topic` |

`merge.py` regroupe les entrées en deux passes :

1. **Par ISRC** (identifiant international de l'enregistrement) : deux entrées
   avec le même ISRC sont le même morceau, quels que soient les libellés.
2. **Par titre normalisé + artiste en commun** : `title_key()` retire accents,
   casse, ponctuation, parenthèses de featuring ; `artist_tokens()` découpe
   `« A & B »`, `« A, B »`, `« A feat. B »`, `« A | B »` et supprime les espaces
   (`DjalilPalermoVEVO` → `djalilpalermo`). Si les deux ensembles se recoupent
   — ou si l'un des deux est vide — les entrées sont fusionnées.

La fusion est transitive (union-find) : A–B par ISRC et B–C par titre donne un
seul groupe A–B–C.

Pour l'affichage, le libellé retenu vient de la source la plus fiable :
**Apple → Audiomack → Spotify → YouTube → Deezer**.

## 2. Convertir une position en score

Pour une source qui classe `N` titres :

```
score_source = 100 × (1 − (position − 1) / (N − 1))
```

| Position | N = 50 | N = 100 |
|---:|---:|---:|
| 1 | 100,00 | 100,00 |
| 2 | 97,96 | 98,99 |
| 10 | 81,63 | 90,91 |
| 50 | 0,00 | 50,51 |
| 100 | — | 0,00 |

Propriétés : linéaire, borné `[0 ; 100]`, indépendant du nombre de titres
collectés au-delà de `N`. Le premier et le dernier de chaque source sont donc
toujours aux extrêmes, ce qui rend les sources comparables malgré des
longueurs différentes (50 vs 100).

## 3. Fusionner les scores

Pour un titre vu par un ensemble de sources `S` :

```
score = Σ( poids_s × score_s ) / Σ( poids_s ),  s ∈ S
```

La division par la somme des poids est essentielle : **une source absente ne
pénalise pas** le titre. Les poids sont dits *renormalisés* sur les sources
présentes.

| Source | Poids nominal |
|---|---:|
| Apple Music DZ | 0,35 |
| Audiomack DZ | 0,25 |
| Spotify (playlist) | 0,25 |
| YouTube DZ | 0,25 |

Sans `YOUTUBE_API_KEY`, la somme vaut 0,85 et chaque score est divisé par 0,85
(équivalent à 41,2 % / 29,4 % / 29,4 %). Le poids affiché dans `CLASSEMENT.md`
est le poids nominal, pas le poids renormalisé.

### Exemple 1 — Sahran lil (2 sources)

| Source | Position | N | Score | Poids | Produit |
|---|---:|---:|---:|---:|---:|
| Audiomack | 5 | 100 | 95,96 | 0,25 | 23,99 |
| Spotify | 8 | 50 | 85,71 | 0,25 | 21,43 |

`(23,99 + 21,43) / 0,50 = 90,84` → **rang 1**.

### Exemple 2 — Sabran (3 sources)

| Source | Position | N | Score | Poids | Produit |
|---|---:|---:|---:|---:|---:|
| Apple | 21 | 50 | 59,18 | 0,35 | 20,71 |
| Audiomack | 1 | 100 | 100,00 | 0,25 | 25,00 |
| Spotify | 1 | 50 | 100,00 | 0,25 | 25,00 |

`(20,71 + 25,00 + 25,00) / 0,85 = 83,19` → **rang 2**.

Un troisième passage, même très mauvais, peut donc **descendre** un titre : la
couverture n'est pas une prime, c'est une moyenne. C'est un choix explicite
(voir « Questions ouvertes »).

### Exemple 3 — titre à source unique

`BbY WOW` n'est vu que par Apple, en position 1 : `score = 100 / 1 = 100,00`.
Il n'est pourtant pas rang 1, car la certification passe avant le score
(voir §4).

## 4. Certifier, puis compléter

`source_count` = nombre de **sources de découverte** où le titre apparaît.
Les enrichissements (Deezer, lien YouTube Music) ne comptent pas.

| Condition | Statut | Rendu |
|---|---|---|
| `source_count ≥ min_sources` (2) | **certifié** | numéroté 1..n |
| sinon | **complément** | rang « — », section séparée |

Ordre final :

1. tous les titres certifiés, triés par score décroissant, puis meilleur rang,
   puis titre ;
2. puis les compléments, avec le même tri, jusqu'à 50 lignes (`--no-fill` les
   exclut).

Conséquences visibles :

- un titre certifié à 70 passe devant un titre Apple n°1 à 100 ;
- `classement.csv` conserve le numéro d'ordre réel (16, 17, …) mais la colonne
  `certified` vaut `0` pour les compléments ;
- le JSON conserve `fill: true/false` et le `rank` global.

Ce choix évite qu'un titre n°1 d'une seule plateforme prenne la tête d'un
classement présenté comme « multi-source ».

## 5. Calculer l'évolution

À chaque fusion, `merge.py` cherche le snapshot précédent **de même méthode** :

- dossier `AAAA-MM-JJ` strictement antérieur ;
- contenant un `DZ_TOP_50_*.json` avec `"pipeline": "multi_source"`.

Il compare les `song_id`.

```
song_id = SHA1( title_key(titre) | normalize(artiste) )[:12]
```

Cet identifiant est stable tant que le libellé ne change pas de façon
irréversible : accents, casse, ponctuation et featuring sont neutralisés. Il
sert de clé de continuité, pas de clé de vérité (un renommage profond crée un
nouvel identifiant).

| Situation | `movement` | `movement_label` |
|---|---|---|
| absent du snapshot précédent | `null` | `NEW` |
| même rang | `0` | `=` |
| gagne 3 places | `+3` | `+3` |
| perd 2 places | `-2` | `-2` |
| aucun snapshot comparable | `null` | `NEW` |

`movement = previous_rank − rank` : un nombre positif signifie une progression.

Le premier classement du dépôt (2026-10-30) provenait d'un notebook et n'a pas
de `sources.json` : il est volontairement exclu de la comparaison.

## 6. Enrichissements (hors score)

| Donnée | Rôle | Impact sur le rang |
|---|---|---|
| ISRC (Deezer) | fusion fiable des versions | indirect (via l'identité) |
| `deezer_rank` | indice de popularité Deezer | **aucun** |
| `deezer_url` | lien d'écoute | aucun |
| `youtube_music_url` | lien d'écoute | aucun |
| `view_count` | vues YouTube (si clé) | aucun (affichage) |

Deezer est exclu du score car son API publique n'expose pas de chart par pays :
seul un `rank` global, sans signification « Algérie ».

## Limites assumées

1. **Deux univers, un seul classement.** Apple reflète une écoute ouverte sur
   le rap français et international ; Audiomack et la playlist éditoriale
   décrivent la scène locale. Recouvrement mesuré : 3, 1 et 13 titres sur 50.
   La règle des 2 sources certifie donc surtout la **scène locale** ; les
   titres Apple seuls remplissent le reste.
2. **Audiomack est hebdomadaire.** Ses positions ne bougent pas en cours de
   semaine : un pipeline quotidien produira beaucoup de `=` sur cette source.
   C'est une propriété de la donnée, pas un bug.
3. **Spotify est une playlist éditoriale**, pas un chart d'usage. Elle mesure
   une curation, ce qui la rend complémentaire d'Apple (usage) mais non
   équivalente.
4. **Pas de YouTube DZ sans clé.** Aucune source publique ne fournit ce chart
   (voir [04 — Sources](04-sources.md)). Le poids YouTube n'est appliqué que si
   la clé est fournie.
5. **Fusion imparfaite.** L'ISRC dépend d'un appariement Deezer approximatif
   (seuil 0,82) ; le repli titre + artiste peut fusionner une reprise ou un
   remix crédité pareil. Les cas ambigus sont rares mais existent.
6. **Pas de filtrage éditorial.** Les récitations coraniques présentes dans le
   chart Apple sont conservées : elles reflètent l'écoute réelle du pays.
7. **Libellés multi-artistes.** `« A & B »` côté Apple et `« A »` seul côté
   Spotify restent deux entrées distinctes si aucun ISRC ne les relie.
8. **Score non comparable entre certifiés et compléments.** Un complément à
   100 n'est pas « meilleur » qu'un certifié à 70 : il manque une confirmation.

## Questions ouvertes

| Sujet | État | Piste |
|---|---|---|
| Prime de couverture | non implémentée | `+5` points par source supplémentaire, pour départager 2 vs 3 sources |
| YouTube | absent | fournir `YOUTUBE_API_KEY`, poids 25 % |
| Mouvement | indisponible | produire un deuxième snapshot `multi_source` |
| Titres arabes | 10 liens YT Music en recherche | requêtes de translittération dédiées |
| Deezer | exclu du score | aucune API de chart pays publique ; à revoir si elle ouvre |
