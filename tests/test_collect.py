# -*- coding: utf-8 -*-
"""Verifications finales du collecteur (sans consommer de quota)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collect  # noqa: E402

ok = 0
ko = 0


def check(label, condition, detail=""):
    global ok, ko
    if condition:
        ok += 1
        print(f"  OK    {label}")
    else:
        ko += 1
        print(f"  ECHEC {label} {detail}")


print("[1] env_value : environnement prioritaire")
import os

os.environ["YOUTUBE_API_KEY"] = "depuis-env"
check("lit os.environ", collect.env_value("YOUTUBE_API_KEY") == "depuis-env")
del os.environ["YOUTUBE_API_KEY"]

print("[2] env_value : lecture du .env local")
env_path = ROOT / ".env"
created = False
if not env_path.exists():
    env_path.write_text("YOUTUBE_API_KEY=depuis-fichier\n", encoding="utf-8")
    created = True
try:
    check("lit .env", collect.env_value("YOUTUBE_API_KEY") == "depuis-fichier")
    check("absent -> None", collect.env_value("VARIABLE_INEXISTANTE") is None)
finally:
    if created:
        env_path.unlink()

print("[3] collect_youtube : erreur HTTP -> RuntimeError (simule)")
real_http_json = collect.http_json
collect.http_json = lambda url: (_ for _ in ()).throw(RuntimeError("HTTP 403 : cle invalide"))
try:
    collect.collect_youtube("cle-invalide", 2)
    check("leve RuntimeError", False, "aucune erreur")
except RuntimeError as exc:
    check("leve RuntimeError", "HTTP" in str(exc), str(exc))
except Exception as exc:  # noqa: BLE001
    check("leve RuntimeError", False, f"type inattendu: {type(exc).__name__}: {exc}")
finally:
    collect.http_json = real_http_json

print("[3b] collect_youtube : mapping des champs (simule)")
collect.http_json = lambda url: {
    "items": [
        {
            "id": "vid00000001",
            "snippet": {"title": "TIF - MINUIT D'APRÈS (Official Video)",
                        "channelTitle": "TIF - Topic", "publishedAt": "2026-10-31"},
            "statistics": {"viewCount": "12345"},
        }
    ]
}
try:
    entries = collect.collect_youtube("cle", 2)
    check("1 entree", len(entries) == 1, entries)
    check("titre nettoye", entries[0]["title"] == "TIF - MINUIT D'APRÈS", entries[0]["title"])
    check("artiste nettoye", entries[0]["artist"] == "TIF", entries[0]["artist"])
    check("vues converties", entries[0]["view_count"] == 12345, entries[0]["view_count"])
finally:
    collect.http_json = real_http_json

print("[4] clean_youtube_artist")
cases = {
    "TIF - Topic": "TIF",
    "DjalilPalermoVEVO": "DjalilPalermo",
    "Soolking Official": "Soolking",
    "Cheb Momo": "Cheb Momo",
}
for raw, expected in cases.items():
    check(f"{raw!r} -> {expected!r}", collect.clean_youtube_artist(raw) == expected,
          collect.clean_youtube_artist(raw))

print("[5] extract_audiomack_tracks (flux RSC echappe)")
payload = (
    '{"title":"Weekly 100","tracks":['
    '{"id":"1","title":"Sabran","artistName":"Djalil Palermo",'
    '"href":"/djalilpalermo/song/sabran","isrc":"FRX762674605","duration":198},'
    '{"id":"2","title":"KILOWAT","artistName":"Stormy",'
    '"href":"/stormy-music/song/kilowat","isrc":null,"duration":180}'
    "]}"
)
escaped = 'self.__next_f.push([1,"' + payload.replace('"', '\\"') + '"])'
tracks = collect.extract_audiomack_tracks(escaped)
check("2 morceaux extraits", len(tracks) == 2, tracks)
check("titre + artiste", tracks and tracks[0].get("title") == "Sabran"
      and tracks[0].get("artistName") == "Djalil Palermo", tracks[:1])
check("ISRC present", tracks and tracks[0].get("isrc") == "FRX762674605")
check("page inattendue -> liste vide",
      collect.extract_audiomack_tracks("<html>rien</html>") == [])

print("[6] enrich_with_youtube_music (recherche simulee)")
import tempfile

calls = {"n": 0}


def fake_search(query):
    calls["n"] += 1
    return [
        {
            "video_id": "abc12345678",
            "title": "Sabran",
            "artist": "Djalil Palermo",
            "video_type": "MUSIC_VIDEO_TYPE_ATV",
        }
    ]


real_search = collect.build_playlist.ytm_search
real_sleep = collect.time.sleep
collect.build_playlist.ytm_search = fake_search
collect.time.sleep = lambda seconds: None
try:
    sources = {
        "apple_music": {
            "entries": [{"position": 1, "title": "Sabran", "artist": "Djalil Palermo"}]
        },
        "spotify": {
            "entries": [{"position": 1, "title": "sabran", "artist": "Djalil Palermo"}]
        },
    }
    cache_path = Path(tempfile.mkdtemp()) / "youtube_music_links.json"
    found, unique, searches = collect.enrich_with_youtube_music(
        sources, cache_path, threshold=85.0
    )
    check("1 titre unique (casse ignoree)", unique == 1, unique)
    check("les 2 entrees sont liees", found == 2, found)
    check(
        "lien watch YouTube Music",
        sources["apple_music"]["entries"][0]["youtube_music_url"]
        == "https://music.youtube.com/watch?v=abc12345678",
        sources["apple_music"]["entries"][0]["youtube_music_url"],
    )
    check("cache ecrit", cache_path.exists())
    again = collect.enrich_with_youtube_music(sources, cache_path, threshold=85.0)
    check("2e passage sans reseau", again[2] == 0, again)
    collect.enrich_with_youtube_music(sources, cache_path, threshold=200.0)
    check(
        "seuil eleve -> pas de lien",
        sources["spotify"]["entries"][0]["youtube_music_url"] is None,
    )
finally:
    collect.build_playlist.ytm_search = real_search
    collect.time.sleep = real_sleep

print(f"\n{ok} verifications OK, {ko} echec(s)")
sys.exit(1 if ko else 0)
