"""Diagnostic des sources — vérifie qu'elles répondent et se parsent.

    python check_sources.py
    python check_sources.py --youtube

N'écrit aucun fichier. Code de sortie 1 si une source attendue est en panne.
"""

from __future__ import annotations

import argparse
import sys

import collect


def first_entry_line(entries: list[dict]) -> str:
    first = entries[0]
    return f"#1 {first.get('title', '?')} — {first.get('artist', '?')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie l'état des sources DZ.")
    parser.add_argument(
        "--youtube",
        action="store_true",
        help="Teste aussi YouTube (nécessite YOUTUBE_API_KEY).",
    )
    args = parser.parse_args(argv)

    checks = [
        ("Apple Music DZ", lambda: collect.collect_apple(5)),
        ("Audiomack DZ", collect.collect_audiomack),
        ("Spotify (playlist)", lambda: collect.collect_spotify(
            collect.DEFAULT_SPOTIFY_PLAYLIST, 50
        )),
    ]

    if args.youtube:
        key = collect.env_value("YOUTUBE_API_KEY")
        if not key:
            print("✗ YouTube DZ            YOUTUBE_API_KEY absente")
        else:
            checks.append(("YouTube DZ", lambda: collect.collect_youtube(key, 5)))

    failures = 0
    for label, fetch in checks:
        try:
            entries = fetch()
        except RuntimeError as exc:
            failures += 1
            print(f"✗ {label:<22} {exc}")
            continue
        if not entries:
            failures += 1
            print(f"✗ {label:<22} 0 entrée (structure de page modifiée ?)")
            continue
        print(f"✓ {label:<22} {len(entries):>3} entrées · {first_entry_line(entries)}")

    if failures:
        print(f"\n{failures} source(s) indisponible(s).")
        return 1
    print("\nToutes les sources testées répondent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
