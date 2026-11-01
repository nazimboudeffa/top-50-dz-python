#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collecte des signaux de classement DZ depuis plusieurs plateformes.

Produit ``<date>/sources.json`` : les positions brutes de chaque plateforme,
plus un enrichissement Deezer (ISRC + indice de popularité) par titre.

Sources de découverte (chacune a un poids dans le score final) :

  - ``apple_music`` : flux RSS public « most played » Algérie (aucune clé)
  - ``audiomack``   : chart « Weekly 100: Algeria » (aucune clé)
  - ``spotify``     : playlist publique lue via sa page embed (aucune clé)
  - ``youtube``     : chart « most popular » Algérie, catégorie Musique
                      (nécessite YOUTUBE_API_KEY, sinon la source est ignorée)

Enrichissement (hors score) :

  - ``deezer``      : pas de chart pays dans l'API publique — fournit l'ISRC,
                      le lien et un indice de popularité à titre informatif
  - ``youtube_music`` : lien d'écoute par titre (recherche InnerTube, sans clé),
                      avec cache ``youtube_music_links.json``

Usage :
    python collect.py
    python collect.py --date 2026-11-01
    python collect.py --spotify-playlist 37i9dQZF1DX7ZqKP81pHlu
    python collect.py --limit 50
    python collect.py --no-youtube-music

Aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from build_playlist import normalize, title_key
import build_playlist

# Configuration du modèle de classement (partagée avec merge.py).
# Poids renormalisés sur les sources réellement disponibles à chaque collecte.
WEIGHTS = {
    "apple_music": 0.35,
    "audiomack": 0.25,
    "spotify": 0.25,
    "youtube": 0.25,
}

APPLE_URL = (
    "https://rss.applemarketingtools.com/api/v2/dz/music/most-played/{limit}/songs.json"
)
AUDIOMACK_URL = "https://audiomack.com/geo-charts/playlist/algeria"
SPOTIFY_EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
YOUTUBE_CHART_URL = "https://www.googleapis.com/youtube/v3/videos"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"

DEFAULT_SPOTIFY_PLAYLIST = "37i9dQZF1DX7ZqKP81pHlu"  # Hot Hit Algeria
DEFAULT_LIMIT = 50
AUDIOMACK_LIMIT = 100
DEEZER_DELAY_SECONDS = 0.15
DEEZER_MATCH_THRESHOLD = 0.82
ENV_FILE = ".env"

# Liens YouTube Music (recherche InnerTube, aucune clé API).
YOUTUBE_MUSIC_URL = "https://music.youtube.com/watch?v={video_id}"
YOUTUBE_MUSIC_THRESHOLD = 85.0
YOUTUBE_MUSIC_CACHE_NAME = "youtube_music_links.json"
YOUTUBE_MUSIC_DELAY_SECONDS = 0.2

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def env_value(name: str) -> str | None:
    """Variable d'environnement, ou à défaut valeur du fichier .env à la racine."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        from spotify_api import load_env_file
    except ImportError:
        return None
    file_values = load_env_file(Path(__file__).resolve().parent / ENV_FILE)
    return file_values.get(name) or None


def http_json(url: str, timeout: int = 25) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} sur {url}") from exc
    except (URLError, OSError) as exc:
        raise RuntimeError(f"injoignable : {url} ({exc})") from exc
    except ValueError as exc:
        raise RuntimeError(f"réponse illisible : {url}") from exc


def http_text(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, OSError) as exc:
        raise RuntimeError(f"injoignable : {url} ({exc})") from exc


# ---------------------------------------------------------------------------
# Sources de découverte
# ---------------------------------------------------------------------------

def collect_apple(limit: int) -> list[dict]:
    payload = http_json(APPLE_URL.format(limit=limit))
    results = (payload.get("feed") or {}).get("results") or []
    return [
        {
            "position": index,
            "title": item.get("name", ""),
            "artist": item.get("artistName", ""),
            "url": item.get("url"),
            "source_id": item.get("id"),
        }
        for index, item in enumerate(results[:limit], start=1)
    ]


NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)

AUDIOMACK_TRACKS_MARKER = '"tracks":'


def balanced_json_array(text: str, start: int) -> str | None:
    """Extrait le tableau JSON équilibré qui commence à ``start`` (un '[')."""
    depth = 0
    in_string = False
    escape = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start: position + 1]
    return None


def extract_audiomack_tracks(html: str) -> list[dict]:
    """Récupère la liste des morceaux depuis la page Audiomack.

    La page est rendue côté serveur par Next.js : les données sont dans un flux
    React où les guillemets sont échappés (``\\"tracks\\":[``). On déséchange
    puis on isole le plus grand tableau ``tracks``.
    """
    text = html.replace('\\"', '"')
    best: list[dict] = []
    start = 0
    while True:
        index = text.find(AUDIOMACK_TRACKS_MARKER + "[", start)
        if index < 0:
            break
        start = index + 1
        array_start = index + len(AUDIOMACK_TRACKS_MARKER)
        chunk = balanced_json_array(text, array_start)
        if not chunk:
            continue
        try:
            tracks = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(tracks, list) and len(tracks) > len(best):
            best = [track for track in tracks if isinstance(track, dict)]
    return best


def collect_audiomack(limit: int = AUDIOMACK_LIMIT) -> list[dict]:
    html = http_text(AUDIOMACK_URL)
    tracks = extract_audiomack_tracks(html)
    if not tracks:
        raise RuntimeError("page Audiomack inattendue (aucun morceau trouvé)")

    entries = []
    for track in tracks:
        title = (track.get("title") or "").strip()
        if not title:
            continue
        href = track.get("href") or ""
        entries.append(
            {
                "position": len(entries) + 1,
                "title": title,
                "artist": (track.get("artistName") or "").strip(),
                "url": f"https://audiomack.com{href}" if href.startswith("/") else href or None,
                "source_id": str(track.get("id") or "") or None,
                "isrc": track.get("isrc") or None,
                "duration_seconds": track.get("duration"),
                "genre": track.get("genre"),
            }
        )
        if len(entries) >= limit:
            break
    return entries


def collect_spotify(playlist_id: str, limit: int) -> list[dict]:
    html = http_text(SPOTIFY_EMBED_URL.format(playlist_id=playlist_id))
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise RuntimeError("page Spotify inattendue (__NEXT_DATA__ absent)")

    payload = json.loads(match.group(1))
    try:
        tracks = payload["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("page Spotify inattendue (trackList absent)") from exc

    entries = []
    for index, track in enumerate(tracks[:limit], start=1):
        track_id = str(track.get("uri", "")).rsplit(":", 1)[-1]
        entries.append(
            {
                "position": index,
                "title": track.get("title", ""),
                "artist": track.get("subtitle", "").replace("\u2009", ", "),
                "url": f"https://open.spotify.com/track/{track_id}" if track_id else None,
                "source_id": track_id,
            }
        )
    return entries


def collect_youtube(api_key: str, limit: int, region: str = "DZ") -> list[dict]:
    url = (
        f"{YOUTUBE_CHART_URL}?part=snippet,statistics&chart=mostPopular"
        f"&regionCode={region}&videoCategoryId=10&maxResults={limit}"
        f"&key={quote(api_key)}"
    )
    payload = http_json(url)
    items = payload.get("items") or []

    entries = []
    for index, item in enumerate(items[:limit], start=1):
        snippet = item.get("snippet") or {}
        statistics = item.get("statistics") or {}
        channel = snippet.get("channelTitle", "")
        entries.append(
            {
                "position": index,
                "title": clean_video_title(snippet.get("title", "")),
                "artist": clean_youtube_artist(channel),
                "url": f"https://www.youtube.com/watch?v={item.get('id')}",
                "source_id": item.get("id"),
                "view_count": int(statistics.get("viewCount", 0) or 0),
                "published_at": snippet.get("publishedAt"),
                "channel": channel,
                "video_title": snippet.get("title"),
            }
        )
    return entries


def clean_youtube_artist(channel: str) -> str:
    """Retire les suffixes de chaînes YouTube (VEVO, - Topic, Official…)."""
    artist = re.sub(r"\s*-\s*Topic\s*$", "", channel, flags=re.IGNORECASE)
    artist = re.sub(r"VEVO\s*$", "", artist, flags=re.IGNORECASE)
    artist = re.sub(
        r"\s*(official|officiel|music|channel|records)\s*$",
        "",
        artist,
        flags=re.IGNORECASE,
    )
    return artist.strip(" -–—") or channel


VIDEO_PARENS_RE = re.compile(
    r"[\(\[]\s*(?:official|officiel|clip|video|vidéo|audio|lyrics|paroles|"
    r"visualizer|explicit|hd|4k)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
VIDEO_SUFFIX_RE = re.compile(
    r"\s*[-–—]\s*(?:official|officiel|clip|video|vidéo|audio|lyrics|paroles|"
    r"visualizer).*$",
    re.IGNORECASE,
)


def clean_video_title(title: str) -> str:
    """Retire les mentions de clip d'un titre de vidéo YouTube.

    « TIF - MINUIT D'APRÈS (Official Video) » doit redevenir « MINUIT D'APRÈS »
    pour être regroupé avec la même piste vue sur Apple ou Spotify.
    """
    cleaned = VIDEO_PARENS_RE.sub(" ", title)
    cleaned = VIDEO_SUFFIX_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned or title


# ---------------------------------------------------------------------------
# Enrichissement Deezer (ISRC + popularité)
# ---------------------------------------------------------------------------

def deezer_lookup(title: str, artist: str) -> dict | None:
    """Cherche un titre sur Deezer et renvoie la meilleure correspondance."""
    query = quote(f"{title} {artist}".strip())
    try:
        payload = http_json(f"{DEEZER_SEARCH_URL}?q={query}&limit=5")
    except RuntimeError:
        return None

    wanted_title = title_key(title)
    artist_tokens = [token for token in (normalize(part) for part in artist.split(",")) if token]

    best = None
    best_score = 0.0
    for candidate in payload.get("data") or []:
        candidate_title = candidate.get("title", "")
        similarity = difflib.SequenceMatcher(
            None, wanted_title, title_key(candidate_title)
        ).ratio()
        if similarity < DEEZER_MATCH_THRESHOLD:
            continue

        candidate_artist = (candidate.get("artist") or {}).get("name", "")
        artist_ok = any(token and token in normalize(candidate_artist) for token in artist_tokens)
        score = similarity + (0.1 if artist_ok else 0.0)
        if score > best_score:
            best, best_score = candidate, score

    if not best:
        return None

    rank = best.get("rank")
    return {
        "deezer_id": best.get("id"),
        "deezer_url": best.get("link"),
        "isrc": best.get("isrc") or None,
        "deezer_rank": int(rank) if isinstance(rank, (int, float)) else None,
        "deezer_title": best.get("title"),
        "deezer_artist": (best.get("artist") or {}).get("name"),
        "deezer_match": round(best_score, 3),
    }


def enrich_with_deezer(sources: dict) -> int:
    """Ajoute ISRC et rang Deezer aux entrées de toutes les sources."""
    cache: dict[str, dict | None] = {}
    requests_count = 0

    for source in sources.values():
        for entry in source["entries"]:
            key = f"{title_key(entry['title'])}|{normalize(entry['artist'])}"
            if key not in cache:
                cache[key] = deezer_lookup(entry["title"], entry["artist"])
                requests_count += 1
                time.sleep(DEEZER_DELAY_SECONDS)

            match = cache[key]
            if match:
                entry["isrc"] = match["isrc"] or entry.get("isrc")
                entry["deezer_rank"] = match["deezer_rank"]
                entry["deezer_url"] = match["deezer_url"]
                entry["deezer_id"] = match["deezer_id"]
                entry["deezer_match"] = match["deezer_match"]
            else:
                entry.setdefault("isrc", None)
                entry["deezer_rank"] = None

    return requests_count


# ---------------------------------------------------------------------------
# Enrichissement YouTube Music (lien d'écoute, sans clé API)
# ---------------------------------------------------------------------------

def youtube_music_key(title: str, artist: str) -> str:
    return f"{build_playlist.title_key(title)}|{build_playlist.normalize(artist)}"


def youtube_music_search_url(title: str, artist: str) -> str:
    return "https://music.youtube.com/search?q=" + quote(f"{title} {artist}".strip())


def load_youtube_music_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"  ⚠ cache YouTube Music illisible, il sera reconstruit : {path}")
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_youtube_music_cache(path: Path, entries: dict) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def seed_youtube_music_cache(playlist_cache_path: Path) -> dict:
    """Réutilise les correspondances déjà trouvées par build_playlist.py.

    Le cache de la playlist est daté (``date|titre|artiste``) ; ici la clé est
    stable (``titre|artiste``) pour resservir d'un jour à l'autre.
    """
    if not playlist_cache_path.exists():
        return {}
    try:
        payload = json.loads(playlist_cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    seeded = {}
    for key, value in (payload.get("entries") or {}).items():
        parts = key.split("|")
        if len(parts) < 3 or not isinstance(value, dict) or not value.get("video_id"):
            continue
        seeded["|".join(parts[1:])] = value
    return seeded


def enrich_with_youtube_music(
    sources: dict,
    cache_path: Path,
    threshold: float = YOUTUBE_MUSIC_THRESHOLD,
    refresh: bool = False,
) -> tuple[int, int, int]:
    """Ajoute ``youtube_music_url`` à chaque entrée.

    Retourne ``(liens retenus, entrées uniques, recherches effectuées)``.
    Les correspondances incertaines restent sans lien (le rendu proposera une
    recherche YouTube Music à la place).
    """
    cache = {} if refresh else load_youtube_music_cache(cache_path)
    if not refresh:
        for key, value in seed_youtube_music_cache(
            cache_path.parent / build_playlist.YOUTUBE_CACHE_NAME
        ).items():
            cache.setdefault(key, value)

    entries: dict[str, dict] = {}
    for source in sources.values():
        for entry in source["entries"]:
            key = youtube_music_key(entry["title"], entry["artist"])
            # On privilégie l'entrée dont l'artiste est le plus renseigné.
            current = entries.get(key)
            if current is None or len(entry["artist"]) > len(current["artist"]):
                entries[key] = entry

    searches = 0
    for key, entry in entries.items():
        if not key.split("|")[0]:
            continue
        if key not in cache:
            try:
                results = [
                    build_playlist.ytm_search(query)
                    for query in build_playlist.build_queries(entry)
                ]
                searches += len(results)
                best, score = build_playlist.best_candidate(entry, results)
                cache[key] = {
                    "video_id": best["video_id"] if best else None,
                    "score": round(score, 2) if best else None,
                    "title": best["title"] if best else None,
                    "artist": best["artist"] if best else None,
                }
            except RuntimeError as exc:
                log(f"  ⚠ recherche YouTube Music interrompue : {exc}")
                break
            time.sleep(YOUTUBE_MUSIC_DELAY_SECONDS)

    found = 0
    for source in sources.values():
        for entry in source["entries"]:
            key = youtube_music_key(entry["title"], entry["artist"])
            match = cache.get(key) or {}
            score = match.get("score") or 0
            url = None
            if match.get("video_id") and score >= threshold:
                url = YOUTUBE_MUSIC_URL.format(video_id=match["video_id"])
            entry["youtube_music_url"] = url
            entry["youtube_music_score"] = score or None

    found = sum(
        1
        for source in sources.values()
        for entry in source["entries"]
        if entry.get("youtube_music_url")
    )
    save_youtube_music_cache(cache_path, cache)
    return found, len(entries), searches


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def collect(
    date: str,
    limit: int,
    playlist_id: str,
    youtube_key: str | None,
    youtube_music: bool = True,
    youtube_music_refresh: bool = False,
    youtube_music_threshold: float = YOUTUBE_MUSIC_THRESHOLD,
) -> dict:
    sources: dict[str, dict] = {}
    notes: list[str] = []

    log("Apple Music DZ…")
    try:
        entries = collect_apple(limit)
        sources["apple_music"] = {
            "label": "Apple Music DZ",
            "weight": WEIGHTS["apple_music"],
            "period": "quotidien",
            "entries": entries,
        }
        log(f"  {len(entries)} titres")
    except RuntimeError as exc:
        notes.append(f"apple_music indisponible : {exc}")
        log(f"  ⚠ {exc}")

    log("Audiomack DZ (Weekly 100)…")
    try:
        entries = collect_audiomack()
        sources["audiomack"] = {
            "label": "Audiomack DZ",
            "weight": WEIGHTS["audiomack"],
            "period": "hebdomadaire",
            "chart_url": AUDIOMACK_URL,
            "entries": entries,
        }
        log(f"  {len(entries)} titres")
    except RuntimeError as exc:
        notes.append(f"audiomack indisponible : {exc}")
        log(f"  ⚠ {exc}")

    log("Spotify (playlist)…")
    try:
        entries = collect_spotify(playlist_id, limit)
        sources["spotify"] = {
            "label": "Spotify (playlist)",
            "weight": WEIGHTS["spotify"],
            "period": "éditorial",
            "playlist_id": playlist_id,
            "entries": entries,
        }
        log(f"  {len(entries)} titres")
    except RuntimeError as exc:
        notes.append(f"spotify indisponible : {exc}")
        log(f"  ⚠ {exc}")

    log("YouTube DZ…")
    if not youtube_key:
        notes.append(
            "youtube ignoré : YOUTUBE_API_KEY absente (score renormalisé sur les "
            "sources disponibles)"
        )
        log("  ⚠ YOUTUBE_API_KEY absente — source ignorée")
    else:
        try:
            entries = collect_youtube(youtube_key, limit)
            sources["youtube"] = {
                "label": "YouTube DZ",
                "weight": WEIGHTS["youtube"],
                "period": "quotidien",
                "entries": entries,
            }
            log(f"  {len(entries)} titres")
        except RuntimeError as exc:
            notes.append(f"youtube indisponible : {exc}")
            log(f"  ⚠ {exc}")

    if not sources:
        raise SystemExit("Aucune source disponible : impossible de collecter.")

    log("Enrichissement Deezer (ISRC, popularité)…")
    requests_count = enrich_with_deezer(sources)
    with_isrc = sum(
        1 for source in sources.values() for entry in source["entries"] if entry.get("isrc")
    )
    total = sum(len(source["entries"]) for source in sources.values())
    log(f"  {with_isrc}/{total} entrées avec ISRC ({requests_count} requêtes)")

    if youtube_music:
        log("Enrichissement YouTube Music (liens d'écoute)…")
        cache_path = Path(__file__).resolve().parent / YOUTUBE_MUSIC_CACHE_NAME
        try:
            found, unique, searches = enrich_with_youtube_music(
                sources,
                cache_path,
                threshold=youtube_music_threshold,
                refresh=youtube_music_refresh,
            )
            log(
                f"  {found}/{unique} titres uniques avec lien "
                f"({searches} recherche(s) réseau)"
            )
            if searches:
                notes.append(
                    f"youtube_music : {searches} recherches effectuées "
                    f"(cache {YOUTUBE_MUSIC_CACHE_NAME})"
                )
        except OSError as exc:
            notes.append(f"youtube_music indisponible : {exc}")
            log(f"  ⚠ {exc}")

    return {
        "chart": "DZ TOP 50",
        "country": "DZ",
        "date": date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "weights": {name: source["weight"] for name, source in sources.items()},
        "sources": sources,
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collecte les signaux de classement DZ par plateforme."
    )
    parser.add_argument("--date", help="Date du snapshot (AAAA-MM-JJ). Défaut : aujourd'hui.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--spotify-playlist", default=DEFAULT_SPOTIFY_PLAYLIST)
    parser.add_argument(
        "--youtube-music",
        dest="youtube_music",
        action="store_true",
        default=True,
        help="Ajoute un lien YouTube Music par titre (défaut).",
    )
    parser.add_argument(
        "--no-youtube-music",
        dest="youtube_music",
        action="store_false",
        help="Ne pas chercher de lien YouTube Music.",
    )
    parser.add_argument(
        "--youtube-music-refresh",
        action="store_true",
        help="Ignore le cache YouTube Music et relance les recherches.",
    )
    parser.add_argument(
        "--youtube-music-threshold",
        type=float,
        default=YOUTUBE_MUSIC_THRESHOLD,
        help=f"Score minimal pour retenir un lien (défaut : {YOUTUBE_MUSIC_THRESHOLD:.0f}).",
    )
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args(argv)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = Path(__file__).resolve().parent
    out_dir = (root / args.output_dir).resolve()
    date_dir = out_dir / date
    date_dir.mkdir(parents=True, exist_ok=True)

    youtube_key = env_value("YOUTUBE_API_KEY")

    payload = collect(
        date,
        args.limit,
        args.spotify_playlist,
        youtube_key,
        youtube_music=args.youtube_music,
        youtube_music_refresh=args.youtube_music_refresh,
        youtube_music_threshold=args.youtube_music_threshold,
    )

    target = date_dir / "sources.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Écrit : {target}")

    for note in payload["notes"]:
        log(f"  note : {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
