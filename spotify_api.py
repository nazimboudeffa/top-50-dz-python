#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accès minimal à la Spotify Web API — référentiel des titres (ISRC, durée).

Sert à enrichir le classement avec des identifiants stables, utilisables comme
clé de jointure vers les autres plateformes (Deezer expose
``/track/isrc:{ISRC}``, Apple Music accepte l'ISRC en recherche).

Authentification : *Client Credentials* (server-to-server), sans utilisateur.

Configuration — variables d'environnement ou fichier ``.env`` à la racine :

    SPOTIFY_CLIENT_ID=...
    SPOTIFY_CLIENT_SECRET=...

Application à créer sur https://developer.spotify.com/dashboard

Contraintes de la Web API (changements de février 2026) :

  - les applications en *Development Mode* exigent que le propriétaire ait un
    abonnement Spotify Premium ;
  - ``GET /tracks?ids=`` (batch) est supprimé en Development Mode : il faut un
    appel par titre. Le code tente le batch puis bascule automatiquement ;
  - ``external_ids.isrc`` a été retiré puis rétabli (changelog de mars 2026) ;
  - ``popularity`` et ``available_markets`` sont supprimés : aucune donnée
    d'écoutes n'est exposée par cette API.

Ce module est utilisé par ``build_playlist.py --isrc``.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://accounts.spotify.com/api/token"
TRACKS_URL = "https://api.spotify.com/v1/tracks"
TRACK_URL = "https://api.spotify.com/v1/tracks/{track_id}"

BATCH_SIZE = 50
PER_TRACK_DELAY_SECONDS = 0.12
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 2

ENV_FILE = ".env"
ENV_CLIENT_ID = "SPOTIFY_CLIENT_ID"
ENV_CLIENT_SECRET = "SPOTIFY_CLIENT_SECRET"

SETUP_HELP = (
    "Crée une application sur https://developer.spotify.com/dashboard puis "
    "définis SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET dans l'environnement "
    "ou dans un fichier .env à la racine. "
    "Attention : depuis février 2026, une application en Development Mode "
    "exige que le propriétaire ait un abonnement Spotify Premium."
)


class SpotifyError(RuntimeError):
    """Erreur d'accès à la Web API Spotify."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SpotifyAuthError(SpotifyError):
    """Identifiants absents, invalides ou insuffisants."""


class _BatchUnavailable(Exception):
    """Le batch /tracks?ids= n'est pas disponible pour cette application."""


def log(message: str) -> None:
    print(message, file=os.sys.stderr)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> dict[str, str]:
    """Lit un fichier .env simple (KEY=VALUE, commentaires #, guillemets)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve_credentials(
    root: Path,
    environ: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Retourne (client_id, client_secret) ou lève SpotifyAuthError."""
    environ = os.environ if environ is None else environ
    file_values = load_env_file(Path(root) / ENV_FILE)

    client_id = environ.get(ENV_CLIENT_ID) or file_values.get(ENV_CLIENT_ID) or ""
    client_secret = (
        environ.get(ENV_CLIENT_SECRET) or file_values.get(ENV_CLIENT_SECRET) or ""
    )

    missing = [
        name
        for name, value in ((ENV_CLIENT_ID, client_id), (ENV_CLIENT_SECRET, client_secret))
        if not value
    ]
    if missing:
        raise SpotifyAuthError(
            f"Identifiants Spotify manquants : {', '.join(missing)}. {SETUP_HELP}"
        )
    return client_id.strip(), client_secret.strip()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _read_json(request: Request, timeout: int = DEFAULT_TIMEOUT) -> dict:
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        except HTTPError as exc:
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else 1.0
                except ValueError:
                    delay = 1.0
                time.sleep(min(delay, 5.0))
                continue

            if exc.code in (401, 403):
                raise SpotifyAuthError(
                    f"Accès refusé par Spotify (HTTP {exc.code}). {SETUP_HELP}",
                    status=exc.code,
                ) from exc

            raise SpotifyError(f"Erreur Spotify HTTP {exc.code}.", status=exc.code) from exc

        except URLError as exc:
            raise SpotifyError(f"Spotify injoignable : {exc.reason}") from exc

        except ValueError as exc:
            raise SpotifyError("Réponse Spotify illisible (JSON invalide).") from exc

    raise SpotifyError("Échec Spotify après plusieurs tentatives.")


def get_access_token(
    client_id: str,
    client_secret: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Obtient un jeton via le flux client_credentials (valide ~1 h)."""
    if not client_id or not client_secret:
        raise SpotifyAuthError(f"Identifiants Spotify manquants. {SETUP_HELP}")

    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    request = Request(
        TOKEN_URL,
        data=urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    payload = _read_json(request, timeout)
    token = payload.get("access_token")
    if not token:
        raise SpotifyAuthError(f"Spotify n'a pas renvoyé de jeton. {SETUP_HELP}")
    return token


# ---------------------------------------------------------------------------
# Récupération des titres
# ---------------------------------------------------------------------------

def normalize_track(raw: dict) -> dict:
    """Réduit un objet Track Spotify aux champs utiles au référentiel."""
    album = raw.get("album") or {}
    external_ids = raw.get("external_ids") or {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "artists": [
            artist.get("name")
            for artist in raw.get("artists", [])
            if isinstance(artist, dict) and artist.get("name")
        ],
        "album": album.get("name"),
        "release_date": album.get("release_date"),
        "duration_ms": raw.get("duration_ms"),
        "isrc": external_ids.get("isrc"),
        "explicit": raw.get("explicit"),
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _fetch_batch(token: str, track_ids: list[str], timeout: int) -> list[dict]:
    ids = ",".join(track_ids)
    request = Request(
        f"{TRACKS_URL}?ids={ids}", headers=_auth_headers(token)
    )
    try:
        payload = _read_json(request, timeout)
    except SpotifyError as exc:
        # Dev Mode (février 2026) : le batch est supprimé.
        if exc.status in (400, 404, 405, 410):
            raise _BatchUnavailable(str(exc)) from exc
        raise
    return payload.get("tracks") or []


def _fetch_single(token: str, track_id: str, timeout: int) -> dict | None:
    request = Request(
        TRACK_URL.format(track_id=track_id), headers=_auth_headers(token)
    )
    try:
        return _read_json(request, timeout)
    except SpotifyError as exc:
        if exc.status == 404:
            log(f"  ⚠ titre Spotify introuvable : {track_id}")
            return None
        raise


def fetch_tracks(
    token: str,
    track_ids: list[str],
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, dict]:
    """Récupère les métadonnées de plusieurs titres, indexées par identifiant.

    Tente le batch (applications en quota étendu) puis bascule sur un appel par
    titre (Development Mode), avec une petite pause entre les appels.
    """
    unique_ids = [track_id for track_id in dict.fromkeys(track_ids) if track_id]
    if not unique_ids:
        return {}

    result: dict[str, dict] = {}

    for start in range(0, len(unique_ids), BATCH_SIZE):
        chunk = unique_ids[start:start + BATCH_SIZE]
        try:
            raw_tracks = _fetch_batch(token, chunk, timeout)
        except _BatchUnavailable:
            log(
                "  ℹ batch /tracks?ids= indisponible (Development Mode) : "
                f"un appel par titre ({len(unique_ids)} requêtes)."
            )
            raw_tracks = []
            for track_id in chunk:
                raw = _fetch_single(token, track_id, timeout)
                if raw:
                    result[track_id] = normalize_track(raw)
                time.sleep(PER_TRACK_DELAY_SECONDS)
            continue

        for raw in raw_tracks:
            if raw and raw.get("id"):
                result[raw["id"]] = normalize_track(raw)

    return result


# ---------------------------------------------------------------------------
# Cache disque
# ---------------------------------------------------------------------------

def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"  ⚠ cache Spotify illisible, il sera reconstruit : {path}")
        return {}
    entries = payload.get("tracks")
    return entries if isinstance(entries, dict) else {}


def save_cache(path: Path, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(entries),
        "tracks": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
