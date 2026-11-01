#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DZ TOP 50 — génération de la liste des titres avec liens Spotify / YouTube.

Lit le classement produit dans un dossier de date (ex. ``2026-10-30/``) et
associe à chaque entrée ses URL Spotify et, en option, YouTube Music, puis
écrit :

  - ``PLAYLIST.md``          : tableau lisible (rang, chanson, auteur, liens)
  - ``playlist_spotify.csv`` : mêmes données en CSV
  - ``PLAYLIST.html``        : tableau HTML complet avec liens (si --html)
  - ``spotify_meta.json``    : référentiel Spotify (ISRC, durée) (si --isrc)
  - ``youtube_links.json``   : cache des correspondances YouTube (si --youtube)
  - ``youtube_a_verifier.txt`` : titres dont l'appariement est incertain

Les fichiers de données ne contiennent pas d'identifiant Spotify : les liens
sont résolus depuis la playlist source ``Hot Hit Algeria``. L'appariement se
fait d'abord par titre (normalisé), puis, en secours, par position dans la
playlist. Chaque divergence est signalée sur la sortie standard.

YouTube n'expose pas d'identifiant dérivable depuis un titre : les liens sont
obtenus par recherche sur YouTube Music, puis notés (similarité de titre,
recouvrement d'artiste, type de vidéo). Un lien n'est écrit que si le score
atteint le seuil de confiance ; sinon le titre est listé dans le rapport.

Usage :
    python build_playlist.py                          # dernière date
    python build_playlist.py --date 2026-10-30        # date précise
    python build_playlist.py --youtube                # + liens YouTube Music
    python build_playlist.py --youtube --youtube-refresh
    python build_playlist.py --youtube --html         # + PLAYLIST.html
    python build_playlist.py --youtube --html-update-source  # + HTML du dossier
    python build_playlist.py --isrc                   # + ISRC (Web API Spotify)

Le mode ``--isrc`` lit ``SPOTIFY_CLIENT_ID`` et ``SPOTIFY_CLIENT_SECRET`` dans
l'environnement ou dans un fichier ``.env`` (voir ``.env.example``).
    python build_playlist.py --playlist <id>           # autre playlist source
    python build_playlist.py --output-dir out          # autre dossier de sortie

Aucune dépendance externe : uniquement la bibliothèque standard.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

# Playlist source du classement (Spotify — Hot Hit Algeria).
DEFAULT_PLAYLIST_ID = "37i9dQZF1DX7ZqKP81pHlu"
DEFAULT_PLAYLIST_NAME = "Hot Hit Algeria"

EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
TRACK_URL = "https://open.spotify.com/track/{track_id}"
PLAYLIST_URL = "https://open.spotify.com/playlist/{playlist_id}"

YTM_SEARCH_URL = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
YOUTUBE_URL = "https://music.youtube.com/watch?v={video_id}"
YOUTUBE_CONFIDENCE_THRESHOLD = 85.0
YOUTUBE_CACHE_NAME = "youtube_links.json"
YOUTUBE_REPORT_NAME = "youtube_a_verifier.txt"
YOUTUBE_DELAY_SECONDS = 0.25

# Référentiel Spotify (--isrc)
ISRC_CACHE_NAME = "spotify_meta.json"

# Mots-clés qui trahissent une version non officielle.
BAD_WORDS = (
    "cover", "reprise", "remix", "slowed", "sped up", "speed up", "mashup",
    "reaction", "8d", "nightcore", "instrumental", "karaoke", "parodie",
    "ai cover", "extended", "podcast",
)

HTML_COLUMNS = [
    "rank",
    "song_id",
    "title",
    "artist",
    "weighted_total",
    "weight_total",
    "source_count",
    "sources",
    "metrics",
    "best_rank",
    "score",
    "previous_rank",
    "movement",
    "movement_label",
]

HTML_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem;color:#111}"
    "table{border-collapse:collapse;width:100%;font-size:14px}"
    "th,td{border:1px solid #ddd;padding:6px 10px;text-align:left}"
    "th{background:#0b6b3a;color:#fff}"
    "tr:nth-child(even){background:#f5f5f5}h1{font-size:1.4rem}"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CHART_RE = re.compile(r"^DZ_TOP_50_(\d{8})\.json$")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def normalize(text: str) -> str:
    """Minuscule, sans accents ni ponctuation, pour comparer des titres."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


FEATURE_PARENS_RE = re.compile(
    r"[\(\[]\s*(?:feat|ft|avec|with|prod)\b[^\)\]]*[\)\]]", re.IGNORECASE
)
FEATURE_SUFFIX_RE = re.compile(
    r"\s*[-–—]\s*(?:feat|ft|avec|with)\.?\s+.*$", re.IGNORECASE
)


def title_key(text: str) -> str:
    """Titre comparable : sans mention de featuring (« feat. X » / « avec X »)."""
    cleaned = FEATURE_PARENS_RE.sub(" ", text)
    cleaned = FEATURE_SUFFIX_RE.sub(" ", cleaned)
    return normalize(cleaned) or normalize(text)


# ---------------------------------------------------------------------------
# Sélection des données source
# ---------------------------------------------------------------------------

def find_date_dirs(root: Path) -> list[Path]:
    return sorted(
        p for p in root.iterdir() if p.is_dir() and DATE_DIR_RE.match(p.name)
    )


def pick_date_dir(root: Path, date: str | None) -> Path:
    candidates = find_date_dirs(root)
    if not candidates:
        raise SystemExit(f"Aucun dossier de date (AAAA-MM-JJ) trouvé dans {root}")

    if date is None:
        return candidates[-1]

    target = root / date
    if not target.is_dir():
        available = ", ".join(p.name for p in candidates)
        raise SystemExit(f"Dossier {date} introuvable. Disponibles : {available}")
    return target


def find_chart_file(date_dir: Path) -> Path:
    charts = sorted(date_dir.glob("DZ_TOP_50_*.json"))
    if len(charts) == 1:
        return charts[0]
    if not charts:
        raise SystemExit(f"Aucun fichier DZ_TOP_50_*.json dans {date_dir}")

    expected = date_dir.name.replace("-", "")
    for chart in charts:
        match = CHART_RE.match(chart.name)
        if match and match.group(1) == expected:
            return chart
    return charts[-1]


def load_chart(chart_path: Path) -> dict:
    with chart_path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------

def fetch_playlist_tracks(playlist_id: str) -> list[dict]:
    """Récupère la liste ordonnée des titres d'une playlist via la page embed."""
    request = Request(
        EMBED_URL.format(playlist_id=playlist_id),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (URLError, OSError) as exc:
        raise SystemExit(f"Impossible de récupérer la playlist {playlist_id} : {exc}")

    match = NEXT_DATA_RE.search(html)
    if not match:
        raise SystemExit("Page Spotify inattendue : bloc __NEXT_DATA__ absent.")

    data = json.loads(match.group(1))
    try:
        return data["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]
    except (KeyError, TypeError):
        raise SystemExit("Structure de la page Spotify inattendue (trackList absent).")


def track_id_from_uri(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


def track_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or None


def resolve_links(entries: list[dict], tracks: list[dict]) -> list[dict]:
    """Associe chaque entrée du classement à un titre Spotify."""
    if not tracks:
        raise SystemExit("La playlist Spotify ne contient aucun titre.")

    by_title: dict[str, list[int]] = {}
    for index, track in enumerate(tracks):
        by_title.setdefault(normalize(track.get("title", "")), []).append(index)

    used: set[int] = set()
    resolved: list[dict] = []
    positional_fallbacks = 0
    artist_warnings = 0

    for position, entry in enumerate(entries):
        track_index: int | None = None

        # 1) Appariement exact sur le titre normalisé.
        for index in by_title.get(normalize(entry.get("title", "")), []):
            if index not in used:
                track_index = index
                break

        # 2) Repli sur la position dans le classement (même ordre que la playlist).
        if track_index is None and position < len(tracks) and position not in used:
            track_index = position
            positional_fallbacks += 1
            log(
                f"  ⚠ titre non trouvé « {entry.get('title')} » — "
                f"repli sur la position {position + 1}"
            )

        if track_index is None:
            log(f"  ⚠ aucun titre Spotify pour le rang {entry.get('rank')}")
            resolved.append({**entry, "spotify_url": None, "spotify_id": None})
            continue

        used.add(track_index)
        track = tracks[track_index]
        track_id = track_id_from_uri(track.get("uri", ""))

        if normalize(entry.get("artist", "")) and not any(
            normalize(part) in normalize(track.get("subtitle", ""))
            for part in entry["artist"].split(",")
        ):
            artist_warnings += 1
            log(
                f"  ⚠ artiste divergent au rang {entry.get('rank')} : "
                f"« {entry.get('artist')} » vs « {track.get('subtitle')} »"
            )

        resolved.append(
            {
                **entry,
                "spotify_url": TRACK_URL.format(track_id=track_id),
                "spotify_id": track_id,
            }
        )

    if positional_fallbacks or artist_warnings:
        log(
            f"Appariements : {len(entries) - positional_fallbacks} par titre, "
            f"{positional_fallbacks} par position, {artist_warnings} alerte(s) artiste."
        )

    return resolved


# ---------------------------------------------------------------------------
# Référentiel Spotify (ISRC, durée)
# ---------------------------------------------------------------------------

def enrich_isrc(
    rows: list[dict],
    root: Path,
    cache_path: Path,
    refresh: bool = False,
) -> tuple[list[dict], int]:
    """Complète les lignes avec l'ISRC et la durée issus de la Spotify Web API."""
    try:
        import spotify_api
    except ImportError as exc:
        raise SystemExit(f"Module spotify_api.py introuvable : {exc}")

    cache = {} if refresh else spotify_api.load_cache(cache_path)
    pending = [
        row
        for row in rows
        if row.get("spotify_id")
        and (refresh or row["spotify_id"] not in cache)
    ]

    if pending:
        try:
            client_id, client_secret = spotify_api.resolve_credentials(root)
            token = spotify_api.get_access_token(client_id, client_secret)
            fetched = spotify_api.fetch_tracks(
                token, [row["spotify_id"] for row in pending]
            )
        except spotify_api.SpotifyError as exc:
            raise SystemExit(f"Spotify : {exc}")

        cache.update(fetched)
        spotify_api.save_cache(cache_path, cache)
        log(
            f"Spotify : {len(fetched)} titre(s) récupéré(s), "
            f"{len(pending) - len(fetched)} échec(s), cache {cache_path.name}"
        )

    for row in rows:
        meta = cache.get(row["spotify_id"]) if row.get("spotify_id") else None
        row["spotify_meta"] = meta
        row["isrc"] = (meta or {}).get("isrc")
        row["duration_ms"] = (meta or {}).get("duration_ms")

    return rows, sum(1 for row in rows if row.get("isrc"))


# ---------------------------------------------------------------------------
# YouTube Music
# ---------------------------------------------------------------------------

def ytm_search(query: str) -> list[dict]:
    """Recherche YouTube Music (InnerTube, sans clé API)."""
    body = {
        "context": {
            "client": {
                "clientName": "WEB_REMIX",
                "clientVersion": "1.20240101.01.00",
                "hl": "fr",
                "gl": "FR",
            }
        },
        "query": query,
    }
    request = Request(
        YTM_SEARCH_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Origin": "https://music.youtube.com",
            "Accept-Language": "fr-FR,fr;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"recherche YouTube Music impossible : {exc}") from exc

    renderers: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if "musicResponsiveListItemRenderer" in node:
                renderers.append(node["musicResponsiveListItemRenderer"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)

    results: list[dict] = []
    for renderer in renderers:
        try:
            columns = renderer["flexColumns"]
            title = columns[0]["musicResponsiveListItemFlexColumnRenderer"][
                "text"
            ]["runs"][0]["text"]
            meta_runs = columns[1]["musicResponsiveListItemFlexColumnRenderer"][
                "text"
            ]["runs"]
            artist = meta_runs[-1]["text"] if len(meta_runs) > 1 else ""
        except (KeyError, IndexError, TypeError):
            continue

        video_id = None
        video_type = ""
        try:
            watch = renderer["overlay"]["musicItemThumbnailOverlayRenderer"][
                "content"
            ]["musicPlayButtonRenderer"]["playNavigationEndpoint"]["watchEndpoint"]
            video_id = watch["videoId"]
            video_type = watch["watchEndpointMusicSupportedConfigs"][
                "watchEndpointMusicConfig"
            ]["musicVideoType"]
        except (KeyError, TypeError):
            match = re.search(r'"videoId":"([\w-]{11})"', json.dumps(renderer))
            if match:
                video_id = match.group(1)

        if video_id:
            results.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "artist": artist,
                    "video_type": video_type,
                }
            )
    return results


def score_candidate(entry: dict, candidate: dict) -> float:
    """Note un candidat YouTube pour une entrée du classement (0-100+)."""
    title_n = title_key(entry.get("title", ""))
    candidate_n = title_key(candidate.get("title", ""))
    ratio = difflib.SequenceMatcher(None, title_n, candidate_n).ratio()
    contains = bool(title_n) and (title_n in candidate_n or candidate_n in title_n)

    haystack = normalize(f"{candidate.get('artist', '')} {candidate.get('title', '')}")
    # « Draganov & TIF », « Karol G, Judeline & rusowsky » : on compare chaque
    # artiste séparément, sinon la chaîne complète ne correspond jamais.
    artist_parts = re.split(
        r"[,&/|]|\s+-\s+|\s+feat\.?\s+|\s+ft\.?\s+|\s+x\s+",
        entry.get("artist", ""),
        flags=re.IGNORECASE,
    )
    artist_match = any(
        token and token in haystack
        for token in (normalize(part) for part in artist_parts)
    )

    value = ratio * 60
    if contains:
        value += 15
    if artist_match:
        value += 20
    if candidate.get("video_type") == "MUSIC_VIDEO_TYPE_ATV":
        value += 6
    elif candidate.get("video_type") == "MUSIC_VIDEO_TYPE_OMV":
        value += 2
    if any(word in normalize(candidate.get("title", "")) for word in BAD_WORDS):
        value -= 35
    return value


def load_youtube_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        log(f"  ⚠ cache YouTube illisible, il sera reconstruit : {path}")
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_youtube_cache(path: Path, entries: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def cache_key(date: str, entry: dict) -> str:
    return "|".join(
        [date, normalize(entry.get("title", "")), normalize(entry.get("artist", ""))]
    )


def build_queries(entry: dict) -> list[str]:
    """Requêtes YouTube, de la plus précise à la plus large."""
    title = entry.get("title", "")
    artists = [part.strip() for part in entry.get("artist", "").split(",") if part.strip()]
    primary = artists[0] if artists else ""

    queries = [f"{title} {entry.get('artist', '')}".strip()]
    fallback = f"{title} {primary}".strip()
    if fallback and fallback not in queries:
        queries.append(fallback)
    if title and title not in queries:
        queries.append(title)
    return queries


def best_candidate(entry: dict, searches: list[list[dict]]) -> tuple[dict | None, float]:
    seen: set[str] = set()
    best: dict | None = None
    best_score = float("-inf")
    for candidates in searches:
        for candidate in candidates:
            video_id = candidate["video_id"]
            if video_id in seen:
                continue
            seen.add(video_id)
            value = score_candidate(entry, candidate)
            if value > best_score:
                best, best_score = candidate, value
    return best, best_score


def resolve_youtube(
    rows: list[dict],
    date: str,
    cache_path: Path,
    threshold: float,
    refresh: bool,
) -> tuple[list[dict], list[dict], int, int]:
    """Complète les lignes avec youtube_url. Retourne (rows, à vérifier, cache, appels)."""
    cache = {} if refresh else load_youtube_cache(cache_path)
    pending: list[dict] = []
    live_calls = 0

    for row in rows:
        key = cache_key(date, row)
        cached = cache.get(key)
        if cached:
            row["youtube_url"] = (
                YOUTUBE_URL.format(video_id=cached["video_id"])
                if cached.get("video_id") and cached.get("score", 0) >= threshold
                else None
            )
            row["youtube_score"] = cached.get("score")
            row["youtube_candidate"] = cached
        else:
            row["youtube_url"] = None
            row["youtube_score"] = None
            row["youtube_candidate"] = None
            pending.append(row)

    for row in pending:
        searches: list[list[dict]] = []
        used_queries: list[str] = []
        failed = False
        for attempt, query in enumerate(build_queries(row)):
            if attempt and searches and best_candidate(row, searches)[1] >= threshold:
                break
            try:
                searches.append(ytm_search(query))
                used_queries.append(query)
                live_calls += 1
            except RuntimeError as exc:
                log(f"  ⚠ rang {row.get('rank')} : {exc}")
                failed = True
                break
            time.sleep(YOUTUBE_DELAY_SECONDS)

        if failed or not any(searches):
            log(f"  ⚠ rang {row.get('rank')} : aucun résultat YouTube Music")
            continue

        best, best_score = best_candidate(row, searches)
        if best is None:
            log(f"  ⚠ rang {row.get('rank')} : aucun résultat YouTube Music")
            continue

        best_score = round(best_score, 1)
        cache[cache_key(date, row)] = {
            "video_id": best["video_id"],
            "score": best_score,
            "title": best["title"],
            "artist": best["artist"],
            "video_type": best.get("video_type", ""),
            "query": " | ".join(used_queries),
        }
        row["youtube_score"] = best_score
        row["youtube_candidate"] = cache[cache_key(date, row)]
        row["youtube_url"] = (
            YOUTUBE_URL.format(video_id=best["video_id"])
            if best_score >= threshold
            else None
        )

    to_review = [
        row
        for row in rows
        if row.get("youtube_url") is None or (row.get("youtube_score") or 0) < threshold
    ]
    return rows, to_review, cache, live_calls


# ---------------------------------------------------------------------------
# Rendu / écriture
# ---------------------------------------------------------------------------

def spotify_search_url(title: str, artist: str) -> str:
    return "https://open.spotify.com/search/" + quote_plus(f"{title} {artist}".strip())


def render_markdown(
    rows: list[dict],
    chart: dict,
    date: str,
    playlist_id: str,
    playlist_name: str = DEFAULT_PLAYLIST_NAME,
    with_youtube: bool = False,
) -> str:
    header = "| # | Chanson | Auteur | Spotify |"
    separator = "|---|---------|--------|---------|"
    if with_youtube:
        header += " YouTube |"
        separator += "---------|"

    if chart.get("pipeline") == "multi_source":
        intro = (
            "Classement multi-source ("
            + (chart.get("sources") or "Apple, Audiomack, Spotify")
            + "). Scores et évolution : `CLASSEMENT.md`."
        )
    else:
        intro = (
            "Classement basé sur la playlist Spotify "
            f"[{playlist_name}]({PLAYLIST_URL.format(playlist_id=playlist_id)})."
        )

    lines = [
        f"# DZ TOP 50 — {date}",
        "",
        intro,
        "",
        header,
        separator,
    ]
    for row in rows:
        title = str(row["title"]).replace("|", "\\|")
        artist = str(row["artist"]).replace("|", "\\|")
        spotify = (
            f"[Écouter]({row['spotify_url']})"
            if row.get("spotify_url")
            else f"[Rechercher]({spotify_search_url(row['title'], row['artist'])})"
        )
        cells = [str(row["rank"]), title, artist, spotify]
        if with_youtube:
            youtube = (
                f"[Écouter]({row['youtube_url']})" if row.get("youtube_url") else "—"
            )
            cells.append(youtube)
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        f"*{len(rows)} titres — données du {chart.get('chart_date', date)}.*",
        "",
    ]
    if with_youtube:
        lines += [
            "Liens YouTube issus d'une recherche YouTube Music ; les titres "
            f"incertains restent sans lien et sont listés dans `{YOUTUBE_REPORT_NAME}`.",
            "",
        ]
    return "\n".join(lines)


def format_cell(value) -> str:
    """Formate une valeur comme le HTML source (NaN, flottants à 1 décimale)."""
    if value is None:
        return "NaN"
    if isinstance(value, float):
        rounded = round(value, 1)
        # Neutralise le bruit binaire (56.99999999999999 -> 57.0) sans
        # tronquer une réelle précision supplémentaire.
        if abs(value - rounded) < 1e-9:
            return f"{rounded:.1f}"
        return repr(value)
    return str(value)


def render_html(
    rows: list[dict],
    chart: dict,
    date: str,
    with_youtube: bool = False,
) -> str:
    """Rend le tableau du classement au format HTML (style du fichier source)."""
    suffix = " (bêta)" if chart.get("version") == "beta" else ""
    sources = ", ".join(chart.get("sources", []))

    headers = "".join(f"\n      <th>{escape(column)}</th>" for column in HTML_COLUMNS)
    if with_youtube:
        headers += "\n      <th>Spotify</th>\n      <th>YouTube</th>"

    body: list[str] = []
    for row in rows:
        cells = [
            f"<td>{escape(format_cell(row.get(column)), quote=False)}</td>"
            for column in HTML_COLUMNS
        ]
        if with_youtube:
            cells.append(
                link_cell(
                    row.get("spotify_url"),
                    fallback=spotify_search_url(row["title"], row["artist"]),
                )
            )
            cells.append(link_cell(row.get("youtube_url")))
        body.append("    <tr>\n      " + "\n      ".join(cells) + "\n    </tr>")

    return (
        "<!doctype html>\n"
        '<html lang="fr">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>DZ TOP 50 — {date}</title>\n"
        f"<style>{HTML_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>🇩🇿 DZ TOP 50 — {date}{suffix}</h1>\n"
        f"<p>Sources : {escape(sources)}</p>\n"
        '<table class="dataframe dz-table">\n'
        "  <thead>\n"
        '    <tr style="text-align: right;">'
        f"{headers}\n"
        "    </tr>\n"
        "  </thead>\n"
        "  <tbody>\n"
        + "\n".join(body)
        + "\n"
        "  </tbody>\n"
        "</table>\n"
        "</body>\n"
        "</html>\n"
    )


def link_cell(url: str | None, fallback: str | None = None, label: str = "Écouter") -> str:
    if not url:
        if fallback:
            return (
                f'<td><a href="{escape(fallback, quote=True)}" target="_blank" '
                f'rel="noopener">Rechercher</a></td>'
            )
        return "<td>—</td>"
    return (
        f'<td><a href="{escape(url, quote=True)}" target="_blank" '
        f'rel="noopener">{label}</a></td>'
    )


def write_report(to_review: list[dict], path: Path, threshold: float) -> None:
    if not to_review:
        if path.exists():
            path.unlink()
        return

    lines = [
        "Titres dont l'appariement YouTube est incertain — à vérifier manuellement.",
        f"Seuil de confiance : {threshold:.0f}",
        "",
    ]
    for row in sorted(to_review, key=lambda item: item["rank"]):
        candidate = row.get("youtube_candidate") or {}
        score = row.get("youtube_score")
        lines.append(f"#{row['rank']} {row['title']} — {row['artist']}")
        if candidate:
            lines.append(
                f"    score {score} | candidat : {candidate.get('title')} — "
                f"{candidate.get('artist')} ({candidate.get('video_type')})"
            )
            lines.append(
                f"    {YOUTUBE_URL.format(video_id=candidate.get('video_id'))}"
            )
        else:
            lines.append("    aucun candidat retenu (recherche en échec)")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(
    rows: list[dict],
    chart: dict,
    date: str,
    playlist_id: str,
    out_dir: Path,
    playlist_name: str = DEFAULT_PLAYLIST_NAME,
    with_youtube: bool = False,
    with_isrc: bool = False,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "PLAYLIST.md"
    md_path.write_text(
        render_markdown(
            rows, chart, date, playlist_id, playlist_name, with_youtube
        ),
        encoding="utf-8",
    )

    csv_path = out_dir / "playlist_spotify.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        header = ["rank", "title", "artist", "spotify_url"]
        if with_isrc:
            header.append("isrc")
        if with_youtube:
            header.append("youtube_url")
        writer.writerow(header)
        for row in rows:
            record = [
                row["rank"],
                row["title"],
                row["artist"],
                row["spotify_url"] or "",
            ]
            if with_isrc:
                record.append(row.get("isrc") or "")
            if with_youtube:
                record.append(row.get("youtube_url") or "")
            writer.writerow(record)

    return md_path, csv_path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build(
    root: Path,
    date: str | None,
    playlist_id: str,
    out_dir: Path,
    playlist_name: str = DEFAULT_PLAYLIST_NAME,
    with_youtube: bool = False,
    threshold: float = YOUTUBE_CONFIDENCE_THRESHOLD,
    refresh_youtube: bool = False,
    with_html: bool = False,
    update_source_html: bool = False,
    with_isrc: bool = False,
) -> tuple[Path, Path]:
    date_dir = pick_date_dir(root, date)
    chart_path = find_chart_file(date_dir)
    chart = load_chart(chart_path)

    entries = sorted(chart["entries"], key=lambda entry: entry["rank"])
    log(f"Classement : {chart_path.name} ({len(entries)} entrées)")

    if chart.get("pipeline") == "multi_source":
        # Les liens viennent de la fusion : l'ordre du chart n'est plus celui de
        # la playlist, un appariement par position serait faux.
        log("Classement multi-source : liens Spotify fournis par merge.py")
        rows = [
            {
                **entry,
                "spotify_url": entry.get("spotify_url"),
                "spotify_id": track_id_from_url(entry.get("spotify_url")),
            }
            for entry in entries
        ]
    else:
        tracks = fetch_playlist_tracks(playlist_id)
        log(f"Playlist {playlist_id} : {len(tracks)} titres récupérés")
        rows = resolve_links(entries, tracks)

    missing = sum(1 for row in rows if not row["spotify_url"])
    if missing:
        log(f"⚠ {missing} entrée(s) sans lien Spotify")

    if with_isrc:
        rows, isrc_count = enrich_isrc(rows, root, out_dir / ISRC_CACHE_NAME)
        log(f"ISRC : {isrc_count}/{len(rows)} titre(s) renseigné(s)")

    if with_youtube:
        if any(row.get("youtube_music_url") for row in rows):
            # Les liens ont déjà été résolus par collect.py : on évite 50 recherches.
            for row in rows:
                row["youtube_url"] = row.get("youtube_music_url")
            to_review = [row for row in rows if not row.get("youtube_music_url")]
            log(
                f"YouTube Music : {len(rows) - len(to_review)}/{len(rows)} liens "
                "fournis par la collecte"
            )
            write_report(to_review, out_dir / YOUTUBE_REPORT_NAME, threshold)
        else:
            cache_path = out_dir / YOUTUBE_CACHE_NAME
            rows, to_review, cache, live_calls = resolve_youtube(
                rows, date_dir.name, cache_path, threshold, refresh_youtube
            )
            save_youtube_cache(cache_path, cache)
            log(
                f"YouTube : {len(rows) - len(to_review)}/{len(rows)} liens fiables, "
                f"{len(to_review)} à vérifier, {live_calls} recherche(s) en direct"
            )
            write_report(to_review, out_dir / YOUTUBE_REPORT_NAME, threshold)

    md_path, csv_path = write_outputs(
        rows,
        chart,
        date_dir.name,
        playlist_id,
        out_dir,
        playlist_name,
        with_youtube,
        with_isrc,
    )
    log(f"Écrit : {md_path}")
    log(f"Écrit : {csv_path}")

    if with_html:
        html_path = out_dir / "PLAYLIST.html"
        html_path.write_text(
            render_html(rows, chart, date_dir.name, with_youtube),
            encoding="utf-8",
            newline="\n",
        )
        log(f"Écrit : {html_path}")
        if update_source_html:
            source_html = chart_path.with_suffix(".html")
            source_html.write_text(
                render_html(rows, chart, date_dir.name, with_youtube),
                encoding="utf-8",
                newline="\n",
            )
            log(f"Mis à jour : {source_html}")

    return md_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Génère la liste DZ TOP 50 avec les liens Spotify et YouTube."
    )
    parser.add_argument(
        "--date",
        help="Dossier de date à traiter (AAAA-MM-JJ). Par défaut : le plus récent.",
    )
    parser.add_argument(
        "--playlist",
        default=DEFAULT_PLAYLIST_ID,
        help=f"Identifiant de la playlist Spotify source (défaut : {DEFAULT_PLAYLIST_ID}).",
    )
    parser.add_argument(
        "--playlist-name",
        default=DEFAULT_PLAYLIST_NAME,
        help=f"Nom affiché de la playlist source (défaut : {DEFAULT_PLAYLIST_NAME}).",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Dossier de destination des fichiers générés (défaut : racine du dépôt).",
    )
    parser.add_argument(
        "--youtube",
        action="store_true",
        help="Ajoute une colonne YouTube via une recherche YouTube Music.",
    )
    parser.add_argument(
        "--youtube-threshold",
        type=float,
        default=YOUTUBE_CONFIDENCE_THRESHOLD,
        help=f"Score minimal pour retenir un lien YouTube (défaut : {YOUTUBE_CONFIDENCE_THRESHOLD:.0f}).",
    )
    parser.add_argument(
        "--youtube-refresh",
        action="store_true",
        help="Ignore le cache YouTube et relance toutes les recherches.",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Génère aussi PLAYLIST.html (tableau complet avec liens).",
    )
    parser.add_argument(
        "--html-update-source",
        action="store_true",
        help="Met à jour le HTML du dossier de date (implique --html).",
    )
    parser.add_argument(
        "--isrc",
        action="store_true",
        help="Enrichit avec l'ISRC et la durée via la Spotify Web API (identifiants requis).",
    )
    parser.add_argument(
        "--isrc-refresh",
        action="store_true",
        help="Ignore le cache spotify_meta.json et réinterroge la Web API.",
    )
    args = parser.parse_args(argv)

    if args.youtube_refresh and not args.youtube:
        parser.error("--youtube-refresh nécessite --youtube")
    if args.isrc_refresh and not args.isrc:
        parser.error("--isrc-refresh nécessite --isrc")

    root = Path(__file__).resolve().parent
    out_dir = (root / args.output_dir).resolve()

    build(
        root=root,
        date=args.date,
        playlist_id=args.playlist,
        out_dir=out_dir,
        playlist_name=args.playlist_name,
        with_youtube=args.youtube,
        threshold=args.youtube_threshold,
        refresh_youtube=args.youtube_refresh,
        with_html=args.html or args.html_update_source,
        update_source_html=args.html_update_source,
        with_isrc=args.isrc,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
