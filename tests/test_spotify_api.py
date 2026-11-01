# -*- coding: utf-8 -*-
"""Vérifications hors-ligne de spotify_api et du branchement --isrc."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import spotify_api

ok = 0
ko = 0


def check(label, condition, detail=""):
    global ok, ko
    if condition:
        ok += 1
        print(f"  OK   {label}")
    else:
        ko += 1
        print(f"  ECHEC {label} {detail}")


print("[1] load_env_file")
with tempfile.TemporaryDirectory() as tmp:
    env_file = Path(tmp) / ".env"
    env_file.write_text(
        "\ufeff# commentaire\n"
        "\n"
        "SPOTIFY_CLIENT_ID=abc123\n"
        "export SPOTIFY_CLIENT_SECRET='s3cr3t'\n"
        'AUTRE = "valeur espacee" \n'
        "SANS_EGAL\n",
        encoding="utf-8",
    )
    values = spotify_api.load_env_file(env_file)
    check("client id", values.get("SPOTIFY_CLIENT_ID") == "abc123", values)
    check("secret quote + export", values.get("SPOTIFY_CLIENT_SECRET") == "s3cr3t", values)
    check("espaces + guillemets", values.get("AUTRE") == "valeur espacee", values)
    check("ligne invalide ignoree", "SANS_EGAL" not in values, values)

print("[2] resolve_credentials")
with tempfile.TemporaryDirectory() as tmp:
    check(
        "env prioritaire sur .env",
        spotify_api.resolve_credentials(
            Path(tmp), {"SPOTIFY_CLIENT_ID": "envid", "SPOTIFY_CLIENT_SECRET": "envsec"}
        )
        == ("envid", "envsec"),
    )
    try:
        spotify_api.resolve_credentials(Path(tmp), {})
        check("erreur si absents", False)
    except spotify_api.SpotifyAuthError as exc:
        check("erreur si absents", "SPOTIFY_CLIENT_ID" in str(exc))

print("[3] fetch_tracks — batch disponible")
calls = []
original_read = spotify_api._read_json


def fake_batch(request, timeout=30):
    calls.append(("batch", request.full_url))
    return {
        "tracks": [
            {"id": "t1", "name": "N1", "artists": [{"name": "A"}],
             "album": {"name": "Al"}, "duration_ms": 1000,
             "external_ids": {"isrc": "FR001"}, "explicit": False},
            None,
        ]
    }


spotify_api._read_json = fake_batch
result = spotify_api.fetch_tracks("token", ["t1", "t2"])
check("une seule requete batch", len(calls) == 1, calls)
check("titre normalise", result.get("t1", {}).get("isrc") == "FR001", result)
check("track null ignoree", "t2" not in result or result["t2"] is None, result)

print("[4] fetch_tracks — batch supprime (410) -> un appel par titre")
calls.clear()
spotify_api.PER_TRACK_DELAY_SECONDS = 0


def fake_no_batch(request, timeout=30):
    url = request.full_url
    if "/tracks?" in url:
        raise spotify_api.SpotifyError("gone", status=410)
    calls.append(url)
    track_id = url.rsplit("/", 1)[-1]
    return {
        "id": track_id, "name": track_id, "artists": [{"name": "A"}],
        "album": {"name": "Al"}, "duration_ms": 2000,
        "external_ids": {"isrc": f"ISRC{track_id}"}, "explicit": False,
    }


spotify_api._read_json = fake_no_batch
result = spotify_api.fetch_tracks("token", ["x1", "x2", "x3"])
check("3 requetes individuelles", len(calls) == 3, calls)
check("tous recuperes", set(result) == {"x1", "x2", "x3"}, result)
check("isrc par titre", result["x2"]["isrc"] == "ISRCx2", result)

print("[5] fetch_tracks — 404 sur un titre")
calls.clear()


def fake_404(request, timeout=30):
    url = request.full_url
    if "/tracks?" in url:
        raise spotify_api.SpotifyError("gone", status=410)
    if url.endswith("absent"):
        raise spotify_api.SpotifyError("not found", status=404)
    track_id = url.rsplit("/", 1)[-1]
    return {"id": track_id, "name": track_id, "artists": [],
            "album": {}, "duration_ms": 1,
            "external_ids": {}, "explicit": False}


spotify_api._read_json = fake_404
result = spotify_api.fetch_tracks("token", ["ok1", "absent"])
check("titre absent ignore", set(result) == {"ok1"}, result)
check("isrc absent -> None", result["ok1"]["isrc"] is None, result)

spotify_api._read_json = original_read

print(f"\n{ok} verifications OK, {ko} echec(s)")
sys.exit(1 if ko else 0)
