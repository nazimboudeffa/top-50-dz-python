# -*- coding: utf-8 -*-
"""Verifie la fusion : regroupement inter-plateformes, score, certification."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collect  # noqa: E402
import merge  # noqa: E402

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


print("[1] clean_video_title")
cases = {
    "TIF - MINUIT D'APRÈS (Official Video)": "TIF - MINUIT D'APRÈS",
    "MINUIT D'APRÈS [Clip Officiel]": "MINUIT D'APRÈS",
    "Sabran (Audio)": "Sabran",
    "Sabran - Official Music Video": "Sabran",
    "Sabran": "Sabran",
}
for raw, expected in cases.items():
    got = collect.clean_video_title(raw)
    check(f"{raw!r}", got == expected, f"-> {got!r}")

print("[2] regroupement Apple + Spotify + YouTube")
payload = {
    "chart": "DZ TOP 50",
    "country": "DZ",
    "weights": {"apple_music": 0.35, "spotify": 0.25, "youtube": 0.25, "deezer": 0.15},
    "sources": {
        "apple_music": {
            "weight": 0.35,
            "entries": [
                {"position": 1, "title": "MINUIT D'APRÈS", "artist": "TIF",
                 "url": "https://apple/1", "isrc": "DZA126000001", "deezer_rank": 500000,
                 "youtube_music_url": "https://music.youtube.com/watch?v=aaaaaaaaaaa"},
                {"position": 2, "title": "Autre", "artist": "X",
                 "url": "https://apple/2", "isrc": "DZA126000002", "deezer_rank": 100000},
            ],
        },
        "spotify": {
            "weight": 0.25,
            "entries": [
                {"position": 1, "title": "MINUIT D'APRES", "artist": "TIF",
                 "url": "https://spotify/1", "isrc": "DZA126000001", "deezer_rank": 500000},
            ],
        },
        "youtube": {
            "weight": 0.25,
            "entries": [
                {"position": 5,
                 "title": collect.clean_video_title("MINUIT D'APRÈS (Official Video)"),
                 "artist": "TIF - Topic", "url": "https://youtube/1",
                 "isrc": None, "deezer_rank": None, "view_count": 123456},
            ],
        },
    },
}

candidates = merge.build_candidates(payload)
print("   candidats:", len(candidates))
for candidate in candidates:
    print("    ", candidate["title"], "|", candidate["artist"],
          "| sources:", candidate["sources"], "| score:", candidate["score"])

minuit = next((c for c in candidates if "minuit" in merge.title_key(c["title"])), None)
check("un seul candidat MINUIT (3 sources fusionnees)", minuit is not None)
check("3 sources detectees", minuit and minuit["source_count"] == 3,
      minuit and minuit["sources"])
check("positions correctes", minuit and minuit["positions"] ==
      {"apple_music": 1, "spotify": 1, "youtube": 5}, minuit and minuit["positions"])
check("best_rank = 1", minuit and minuit["best_rank"] == 1)
check("isrc conserve", minuit and minuit["isrc"] == "DZA126000001")
check("vues YouTube conservees", minuit and minuit["view_count"] == 123456)
check("lien YouTube recupere", minuit and minuit["youtube_url"] == "https://youtube/1")

# score attendu : apple 100 (p1/2), spotify 100 (p1/1), youtube 100 - 4/49*100
# Deezer est un enrichissement : il ne participe pas au score.
expected = (
    0.35 * 100 + 0.25 * 100 + 0.25 * merge.position_score(5, 1)
) / (0.35 + 0.25 + 0.25)
check("score pondere", minuit and abs(minuit["score"] - round(expected, 2)) < 0.02,
      minuit and (minuit["score"], round(expected, 2)))
check("deezer hors score mais conserve", minuit and minuit["deezer_rank"] == 500000)
check("2 sources de decouverte suffisent a certifier", minuit and minuit["source_count"] == 3)
check(
    "lien YouTube Music propage",
    minuit
    and minuit["youtube_music_url"] == "https://music.youtube.com/watch?v=aaaaaaaaaaa",
    minuit and minuit["youtube_music_url"],
)
check(
    "repli recherche YouTube Music",
    merge.youtube_music_search_url("A B", "C & D")
    == "https://music.youtube.com/search?q=A+B+C+%26+D",
    merge.youtube_music_search_url("A B", "C & D"),
)

print("[3] recouvrement")
overlaps = merge.overlap_matrix(candidates)
check("apple & spotify = 1", overlaps.get("apple_music|spotify") == 1, overlaps)
check("apple & youtube = 1", overlaps.get("apple_music|youtube") == 1, overlaps)

print("[4] song_id stable")
first = merge.song_id("MINUIT D'APRÈS", "TIF")
second = merge.song_id("minuit d'apres", "TIF")
check("insensible aux accents/casse", first == second, (first, second))
check("format 12 hex", len(first) == 12 and all(c in "0123456789abcdef" for c in first))

print(f"\n{ok} verifications OK, {ko} echec(s)")
sys.exit(1 if ko else 0)
