#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collecte des signaux de classement DZ depuis plusieurs plateformes.

Produit ``<date>/sources.json`` : les positions brutes de chaque plateforme,
plus un enrichissement Deezer (ISRC + indice de popularité) par titre.

Sources :

  - ``apple_music`` : flux RSS public « most played » Algérie (aucune clé)
  - ``spotify``     : playlist publique lue via sa page embed (aucune clé)
  - ``youtube``     : chart « most popular » Algérie, catégorie Musique
                      (nécessite YOUTUBE_API_KEY, sinon la source est ignorée)
  - ``deezer``      : pas de chart pays dans l'API publique — sert
                      d'enrichissement (ISRC, ``rank``), pas de découverte

Usage :
    python collect.py
    python collect.py --date 2026-11-01
    python collect.py --spotify-playlist 37i9dQZF1DX7ZqKP81pHlu
    python collect.py --limit 50

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

# Configuration du modèle de classement (partagée avec merge.py).
WEIGHTS = {
    "apple_music": 0.35,
    "spotify": 0.25,
    "youtube": 0.25,
    "deezer": 0.15,
}

APPLE_URL = (
    "https://rss.applemarketingtools.com/api/v2/dz/music/most-played/{limit}/songs.json"
)
SPOTIFY_EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
YOUTUBE_CHART_URL = "https://www.googleapis.com/youtube/v3/videos"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"

DEFAULT_SPOTIFY_PLAYLIST = "37i9dQZF1DX7ZqKP81pHlu"  # Hot Hit Algeria
DEFAULT_LIMIT = 50
DEEZER_DELAY_SECONDS = 0.15
DEEZER_MATCH_THRESHOLD = 0.82
ENV_FILE = ".env"

# Deezer documente ``rank`` comme un indice de popularité ; on suppose un
# plafond de 1 000 000 (vérifié empiriquement sur des titres très écoutés).
DEEZER_RANK_CEILING = 1_000_000

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
                entry["isrc"] = match["isrc"]
                entry["deezer_rank"] = match["deezer_rank"]
                entry["deezer_url"] = match["deezer_url"]
                entry["deezer_id"] = match["deezer_id"]
                entry["deezer_match"] = match["deezer_match"]
            else:
                entry["isrc"] = None
                entry["deezer_rank"] = None

    return requests_count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def collect(
    date: str,
    limit: int,
    playlist_id: str,
    youtube_key: str | None,
) -> dict:
    sources: dict[str, dict] = {}
    notes: list[str] = []

    log("Apple Music DZ…")
    try:
        entries = collect_apple(limit)
        sources["apple_music"] = {
            "label": "Apple Music DZ",
            "weight": WEIGHTS["apple_music"],
            "entries": entries,
        }
        log(f"  {len(entries)} titres")
    except RuntimeError as exc:
        notes.append(f"apple_music indisponible : {exc}")
        log(f"  ⚠ {exc}")

    log("Spotify (playlist)…")
    try:
        entries = collect_spotify(playlist_id, limit)
        sources["spotify"] = {
            "label": "Spotify (playlist)",
            "weight": WEIGHTS["spotify"],
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
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args(argv)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = Path(__file__).resolve().parent
    out_dir = (root / args.output_dir).resolve()
    date_dir = out_dir / date
    date_dir.mkdir(parents=True, exist_ok=True)

    youtube_key = env_value("YOUTUBE_API_KEY")

    payload = collect(date, args.limit, args.spotify_playlist, youtube_key)

    target = date_dir / "sources.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Écrit : {target}")

    for note in payload["notes"]:
        log(f"  note : {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
