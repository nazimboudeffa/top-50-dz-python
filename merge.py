#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fusion multi-source : produit le classement DZ TOP 50 à partir de sources.json.

Entrée  : ``<date>/sources.json`` (écrit par collect.py)
Sorties : ``<date>/DZ_TOP_50_<AAAAMMJJ>.json``  (schéma du dépôt, ``pipeline: multi_source``)
          ``CLASSEMENT.md``, ``CLASSEMENT.html``, ``classement.csv`` à la racine

Modèle de score
---------------
Chaque plateforme fournit une position (1..N). La position est convertie en
score de 0 à 100 ::

    score = 100 * (1 - (position - 1) / (N - 1))

Deezer ne publie pas de chart pays : il fournit un indice de popularité
(``rank``) converti linéairement sur un plafond de 1 000 000.

Le score final est la moyenne pondérée des sources **présentes pour ce titre**
(les sources absentes ne pénalisent pas, elles sont retirées du dénominateur).
``source_count`` ne compte que les sources de découverte — Deezer est un
enrichissement, pas une découverte.

Règle d'entrée : au moins ``--min-sources`` sources de découverte (défaut 2).
Si moins de 50 titres remplissent la règle, les meilleurs titres à source
unique complètent la liste et sont marqués ``¹`` (complément, non certifié).

Usage :
    python merge.py
    python merge.py --date 2026-11-01
    python merge.py --min-sources 2
    python merge.py --no-fill
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from build_playlist import normalize, title_key

MIN_SOURCES_DEFAULT = 2
TOP_N = 50
DEEZER_RANK_CEILING = 1_000_000


def log(message: str) -> None:
    print(message)


# ---------------------------------------------------------------------------
# Clustering des candidats
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, node: int) -> int:
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def artist_tokens(artist: str) -> set[str]:
    return {
        normalize(part)
        for part in re.split(r"[,&/]| feat\.? | ft\.? | x ", artist, flags=re.IGNORECASE)
        if normalize(part)
    }


def flatten(payload: dict) -> list[dict]:
    records = []
    for source_name, source in payload.get("sources", {}).items():
        for entry in source.get("entries", []):
            title = (entry.get("title") or "").strip()
            artist = (entry.get("artist") or "").strip()
            if not title:
                continue
            records.append(
                {
                    "source": source_name,
                    "position": int(entry.get("position") or 0),
                    "title": title,
                    "artist": artist,
                    "url": entry.get("url"),
                    "isrc": entry.get("isrc"),
                    "deezer_rank": entry.get("deezer_rank"),
                    "deezer_url": entry.get("deezer_url"),
                    "view_count": entry.get("view_count"),
                    "source_id": entry.get("source_id"),
                }
            )
    return records


def cluster(records: list[dict]) -> list[list[int]]:
    """Regroupe les entrées qui désignent le même morceau.

    Deux entrées sont fusionnées si elles partagent un ISRC, ou si elles ont le
    même titre normalisé et au moins un artiste en commun.
    """
    union = UnionFind(len(records))

    by_isrc: dict[str, int] = {}
    for index, record in enumerate(records):
        isrc = record.get("isrc")
        if not isrc:
            continue
        if isrc in by_isrc:
            union.union(by_isrc[isrc], index)
        else:
            by_isrc[isrc] = index

    by_title: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        by_title.setdefault(title_key(record["title"]), []).append(index)

    for indices in by_title.values():
        tokens = {index: artist_tokens(records[index]["artist"]) for index in indices}
        for position, left in enumerate(indices):
            for right in indices[position + 1:]:
                left_tokens, right_tokens = tokens[left], tokens[right]
                if not left_tokens or not right_tokens or (left_tokens & right_tokens):
                    union.union(left, right)

    groups: dict[int, list[int]] = {}
    for index in range(len(records)):
        groups.setdefault(union.find(index), []).append(index)
    return list(groups.values())


def pick_representative(members: list[dict]) -> dict:
    """Choisit le libellé le plus fiable : Apple/Spotify avant YouTube."""
    priority = {"apple_music": 0, "spotify": 1, "youtube": 2, "deezer": 3}

    def sort_key(member: dict) -> tuple:
        return (
            priority.get(member["source"], 9),
            0 if member["artist"] else 1,
            member["position"],
        )

    return sorted(members, key=sort_key)[0]


def best_url(members: list[dict], source: str) -> str | None:
    for member in sorted(members, key=lambda item: item["position"]):
        if member["source"] == source and member["url"]:
            return member["url"]
    return None


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

def position_score(position: int, count: int) -> float:
    if count <= 1:
        return 100.0
    return 100.0 * (1.0 - (position - 1) / (count - 1))


def build_candidates(payload: dict) -> list[dict]:
    records = flatten(payload)
    groups = cluster(records)

    weights = payload.get("weights") or {}
    counts = {
        name: len(source.get("entries", []))
        for name, source in payload.get("sources", {}).items()
    }

    candidates = []
    for indices in groups:
        members = [records[index] for index in indices]
        representative = pick_representative(members)

        positions: dict[str, int] = {}
        for member in members:
            current = positions.get(member["source"])
            if current is None or member["position"] < current:
                positions[member["source"]] = member["position"]

        isrc = next((member["isrc"] for member in members if member["isrc"]), None)
        deezer_rank = next(
            (member["deezer_rank"] for member in members if member["deezer_rank"]), None
        )

        contributions = []
        for source, position in positions.items():
            contributions.append(
                (
                    float(weights.get(source, 0.0)),
                    position_score(position, counts.get(source, 0)),
                    source,
                )
            )
        if deezer_rank is not None and weights.get("deezer"):
            score = 100.0 * min(deezer_rank, DEEZER_RANK_CEILING) / DEEZER_RANK_CEILING
            contributions.append((float(weights["deezer"]), score, "deezer"))

        weight_total = sum(weight for weight, _, _ in contributions)
        if weight_total <= 0:
            continue

        weighted_total = sum(weight * score for weight, score, _ in contributions) / weight_total
        discovery_sources = sorted(
            source for source in positions if source in counts
        )

        candidates.append(
            {
                "title": representative["title"],
                "artist": representative["artist"],
                "isrc": isrc,
                "song_id": song_id(representative["title"], representative["artist"]),
                "score": round(weighted_total, 2),
                "weight_total": round(weight_total, 3),
                "source_count": len(discovery_sources),
                "sources": ", ".join(source_label(source) for source in discovery_sources),
                "positions": positions,
                "best_rank": min(positions.values()) if positions else None,
                "deezer_rank": deezer_rank,
                "view_count": next(
                    (member["view_count"] for member in members if member.get("view_count")),
                    None,
                ),
                "spotify_url": best_url(members, "spotify"),
                "apple_url": best_url(members, "apple_music"),
                "youtube_url": best_url(members, "youtube"),
                "deezer_url": next(
                    (member["deezer_url"] for member in members if member.get("deezer_url")),
                    None,
                ),
                "fill": False,
            }
        )

    return candidates


def song_id(title: str, artist: str) -> str:
    raw = f"{title_key(title)}|{normalize(artist)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def source_label(source: str) -> str:
    return {
        "apple_music": "Apple",
        "spotify": "Spotify",
        "youtube": "YouTube",
        "deezer": "Deezer",
    }.get(source, source)


# ---------------------------------------------------------------------------
# Historique
# ---------------------------------------------------------------------------

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def find_previous_chart(root: Path, date: str) -> dict | None:
    directories = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and DATE_DIR_RE.match(path.name) and path.name < date
        ),
        reverse=True,
    )
    for directory in directories:
        charts = sorted(directory.glob("DZ_TOP_50_*.json"))
        if charts:
            try:
                payload = json.loads(charts[0].read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if payload.get("entries"):
                return payload
    return None


def apply_movement(entries: list[dict], previous: dict | None) -> None:
    previous_ranks: dict[str, int] = {}
    if previous:
        for entry in previous.get("entries", []):
            previous_ranks[entry.get("song_id", "")] = entry.get("rank")

    for entry in entries:
        previous_rank = previous_ranks.get(entry["song_id"])
        entry["previous_rank"] = previous_rank
        if previous_rank is None:
            entry["movement"] = None
            entry["movement_label"] = "NEW"
            continue
        delta = previous_rank - entry["rank"]
        entry["movement"] = delta
        if delta == 0:
            entry["movement_label"] = "="
        elif delta > 0:
            entry["movement_label"] = f"+{delta}"
        else:
            entry["movement_label"] = str(delta)


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------

def render_markdown(chart: dict) -> str:
    entries = chart["entries"]
    date = chart["chart_date"]
    lines = [
        f"# {chart['chart']} — {date}",
        "",
        f"Pays : **{chart['country']}** · {len(entries)} titres · "
        f"période : **7 jours glissants** · généré le "
        f"{chart['generated_at_utc'][:10]}",
        "",
        "Sources : "
        + ", ".join(
            f"{source_label(name)} ({weight:.0%})"
            for name, weight in chart["source_weights"].items()
        ),
        "",
        "| # | Titre | Artiste | Évolution | Sources | Liens |",
        "|---:|---|---|:---:|---|---|",
    ]

    for entry in entries:
        links = []
        for label, url in (
            ("Spotify", entry.get("spotify_url")),
            ("Apple", entry.get("apple_url")),
            ("YouTube", entry.get("youtube_url")),
            ("Deezer", entry.get("deezer_url")),
        ):
            if url:
                links.append(f"[{label}]({url})")
        marker = " ¹" if entry.get("fill") else ""
        lines.append(
            f"| {entry['rank']} | {entry['title']}{marker} | {entry['artist']} | "
            f"{entry['movement_label']} | {entry['sources']} | {' · '.join(links)} |"
        )

    filled = sum(1 for entry in entries if entry.get("fill"))
    lines.append("")
    if filled:
        lines.append(
            f"¹ {filled} titre(s) à source unique : ils complètent la liste mais ne "
            "remplissent pas la règle des 2 sources concordantes."
        )
    if chart["movement_available"]:
        lines.append("")
        lines.append(
            "*Évolution calculée par rapport au snapshot précédent disponible.*"
        )
    else:
        lines.append("")
        lines.append(
            "*Premier snapshot : toutes les évolutions sont « NEW ». Le mouvement "
            "apparaîtra dès la deuxième collecte.*"
        )
    lines.append("")
    return "\n".join(lines)


def render_html(chart: dict) -> str:
    rows = []
    for entry in chart["entries"]:
        links = " · ".join(
            f'<a href="{html.escape(url, quote=True)}">{label}</a>'
            for label, url in (
                ("Spotify", entry.get("spotify_url")),
                ("Apple", entry.get("apple_url")),
                ("YouTube", entry.get("youtube_url")),
                ("Deezer", entry.get("deezer_url")),
            )
            if url
        )
        marker = "&nbsp;¹" if entry.get("fill") else ""
        rows.append(
            "<tr>"
            f'<td class="rank">{entry["rank"]}</td>'
            f"<td><strong>{html.escape(entry['title'])}</strong>{marker}</td>"
            f"<td>{html.escape(entry['artist'])}</td>"
            f'<td class="move">{html.escape(entry["movement_label"])}</td>'
            f"<td>{html.escape(entry['sources'])}</td>"
            f'<td class="links">{links}</td>'
            "</tr>"
        )

    sources = ", ".join(
        f"{source_label(name)} ({weight:.0%})"
        for name, weight in chart["source_weights"].items()
    )
    note = (
        "Évolution calculée par rapport au snapshot précédent."
        if chart["movement_available"]
        else "Premier snapshot : le mouvement apparaîtra à la deuxième collecte."
    )
    filled = sum(1 for entry in chart["entries"] if entry.get("fill"))
    fill_note = (
        f"<p class=\"note\">¹ {filled} titre(s) à source unique (complément, "
        "sous la règle des 2 sources).</p>"
        if filled
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(chart['chart'])} — {chart['chart_date']}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; padding: 2rem 1rem; line-height: 1.4; }}
  main {{ max-width: 68rem; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 .25rem; }}
  .meta {{ color: #777; font-size: .9rem; margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .95rem; }}
  th, td {{ padding: .5rem .6rem; border-bottom: 1px solid #d8d8d8;
            text-align: left; vertical-align: top; }}
  th {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
        color: #666; }}
  td.rank {{ font-variant-numeric: tabular-nums; font-weight: 600; width: 3rem; }}
  td.move {{ font-variant-numeric: tabular-nums; white-space: nowrap; width: 6rem; }}
  td.links {{ white-space: nowrap; font-size: .88rem; }}
  tbody tr:hover {{ background: rgba(127,127,127,.10); }}
  .note {{ color: #777; font-size: .85rem; margin-top: 1rem; }}
</style>
</head>
<body>
<main>
  <h1>{html.escape(chart['chart'])} — {chart['chart_date']}</h1>
  <p class="meta">Pays : {chart['country']} · {len(chart['entries'])} titres ·
     période : 7 jours glissants · généré le {chart['generated_at_utc'][:10]}<br>
     Sources : {html.escape(sources)}</p>
  <table>
    <thead><tr><th>#</th><th>Titre</th><th>Artiste</th><th>Évol.</th>
    <th>Sources</th><th>Liens</th></tr></thead>
    <tbody>
{chr(10).join('      ' + row for row in rows)}
    </tbody>
  </table>
  {fill_note}
  <p class="note">{note}</p>
</main>
</body>
</html>
"""


def write_csv(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rank",
                "title",
                "artist",
                "source_count",
                "sources",
                "score",
                "movement",
                "isrc",
                "spotify_url",
                "apple_url",
                "youtube_url",
                "deezer_url",
            ]
        )
        for entry in entries:
            writer.writerow(
                [
                    entry["rank"],
                    entry["title"],
                    entry["artist"],
                    entry["source_count"],
                    entry["sources"],
                    entry["score"],
                    entry["movement_label"],
                    entry.get("isrc") or "",
                    entry.get("spotify_url") or "",
                    entry.get("apple_url") or "",
                    entry.get("youtube_url") or "",
                    entry.get("deezer_url") or "",
                ]
            )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def merge(
    root: Path,
    date: str,
    min_sources: int,
    fill: bool,
) -> dict:
    sources_path = root / date / "sources.json"
    if not sources_path.exists():
        raise SystemExit(
            f"Snapshot absent : {sources_path}\nLance d'abord : python collect.py --date {date}"
        )

    payload = json.loads(sources_path.read_text(encoding="utf-8"))
    candidates = build_candidates(payload)

    certified = [item for item in candidates if item["source_count"] >= min_sources]
    extras = [item for item in candidates if item["source_count"] < min_sources]

    def sort_key(item: dict) -> tuple:
        return (
            -item["score"],
            item["best_rank"] if item["best_rank"] is not None else 999,
            title_key(item["title"]),
        )

    certified.sort(key=sort_key)
    selected = certified[:TOP_N]

    if fill and len(selected) < TOP_N:
        extras.sort(key=sort_key)
        for item in extras[: TOP_N - len(selected)]:
            item["fill"] = True
            selected.append(item)

    for rank, entry in enumerate(selected, start=1):
        entry["rank"] = rank

    previous = find_previous_chart(root, date)
    apply_movement(selected, previous)

    chart = {
        "chart": payload.get("chart", "DZ TOP 50"),
        "version": "multi_source",
        "chart_date": date,
        "country": payload.get("country", "DZ"),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": "multi_source",
        "sources": ", ".join(
            source_label(name) for name in payload.get("sources", {})
        ),
        "source_weights": {
            name: source["weight"]
            for name, source in payload.get("sources", {}).items()
        },
        "metrics": "rank_fusion",
        "min_sources": min_sources,
        "count": len(selected),
        "movement_available": previous is not None,
        "entries": [
            {
                "rank": entry["rank"],
                "song_id": entry["song_id"],
                "title": entry["title"],
                "artist": entry["artist"],
                "weighted_total": entry["score"],
                "weight_total": entry["weight_total"],
                "source_count": entry["source_count"],
                "sources": entry["sources"],
                "metrics": "rank_fusion",
                "best_rank": entry["best_rank"],
                "score": entry["score"],
                "previous_rank": entry["previous_rank"],
                "movement": entry["movement"],
                "movement_label": entry["movement_label"],
                "isrc": entry["isrc"],
                "deezer_rank": entry["deezer_rank"],
                "view_count": entry["view_count"],
                "positions": entry["positions"],
                "fill": entry["fill"],
                "spotify_url": entry["spotify_url"],
                "apple_url": entry["apple_url"],
                "youtube_url": entry["youtube_url"],
                "deezer_url": entry["deezer_url"],
            }
            for entry in selected
        ],
    }
    return chart


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fusionne les sources et produit le classement DZ TOP 50."
    )
    parser.add_argument("--date", help="Date du snapshot (AAAA-MM-JJ). Défaut : le plus récent.")
    parser.add_argument("--min-sources", type=int, default=MIN_SOURCES_DEFAULT)
    parser.add_argument(
        "--no-fill",
        dest="fill",
        action="store_false",
        help="Ne pas compléter avec des titres à source unique.",
    )
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    out_dir = (root / args.output_dir).resolve()

    date = args.date
    if not date:
        available = sorted(
            path.name
            for path in out_dir.iterdir()
            if path.is_dir()
            and DATE_DIR_RE.match(path.name)
            and (path / "sources.json").exists()
        )
        if not available:
            raise SystemExit("Aucun snapshot : lance d'abord python collect.py")
        date = available[-1]

    chart = merge(out_dir, date, args.min_sources, args.fill)

    date_dir = out_dir / date
    chart_path = date_dir / f"DZ_TOP_50_{date.replace('-', '')}.json"
    chart_path.write_text(
        json.dumps(chart, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    (out_dir / "CLASSEMENT.md").write_text(render_markdown(chart), encoding="utf-8")
    (out_dir / "CLASSEMENT.html").write_text(render_html(chart), encoding="utf-8")
    write_csv(out_dir / "classement.csv", chart["entries"])

    certified = sum(1 for entry in chart["entries"] if not entry["fill"])
    log(f"Classement {date} : {chart['count']} titres")
    log(f"  {certified} avec au moins {args.min_sources} sources concordantes")
    log(f"  mouvement : {'oui' if chart['movement_available'] else 'premier snapshot'}")
    log(f"Écrit : {chart_path}")
    log(f"Écrit : {out_dir / 'CLASSEMENT.md'}")
    log(f"Écrit : {out_dir / 'CLASSEMENT.html'}")
    log(f"Écrit : {out_dir / 'classement.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
