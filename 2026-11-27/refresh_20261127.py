#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Actualiser le classement DZ TOP 50 ciblé sur 2026-11-27."""
from __future__ import annotations
import csv, io, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

D = Path(__file__).resolve().parent
ROOT = D.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import collect, merge

TARGET = "2026-11-27"
STAMP = "20261127"
BASE = f"DZ_TOP_50_{STAMP}"


def retries(func, label, attempts=3, delay=1.0):
    def wrapper(*args, **kwargs):
        error = None
        for try_no in range(1, attempts + 1):
            try:
                return func(*args, **kwargs)
            except RuntimeError as exc:
                error = exc
                if try_no < attempts:
                    print(f"  ↻ {label} : essai {try_no}/{attempts} ({exc})", file=sys.stderr)
                    time.sleep(delay)
        raise error
    return wrapper


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def render_md(chart):
    lines = merge.render_markdown(chart).split("\n")
    lines.insert(2, f"Données collectées le {chart['collected_at_utc']} (UTC) · date cible : {TARGET}")
    return "\n".join(lines)


def render_html(chart):
    text = merge.render_html(chart)
    banner = f'  <p class="meta">Données collectées le {chart["collected_at_utc"]} (UTC) · date cible : {TARGET}</p>\n'
    return text.replace("<main>\n", "<main>\n" + banner, 1)


def render_csv(chart):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["rank", "title", "artist", "certified", "source_count", "sources",
                     "score", "movement", "isrc", "spotify_url", "apple_url",
                     "audiomack_url", "youtube_url", "youtube_music_url", "deezer_url",
                     "collected_at_utc"])
    for e in chart["entries"]:
        writer.writerow([e["rank"], e["title"], e["artist"],
                         "0" if e.get("fill") else "1", e["source_count"], e["sources"],
                         e["score"], e["movement_label"], e.get("isrc") or "",
                         e.get("spotify_url") or "", e.get("apple_url") or "",
                         e.get("audiomack_url") or "", e.get("youtube_url") or "",
                         e.get("youtube_music_url") or "", e.get("deezer_url") or "",
                         chart["collected_at_utc"]])
    return out.getvalue()


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    names = ("collect_apple", "collect_audiomack", "collect_spotify")
    originals = {name: getattr(collect, name) for name in names}
    for name in names:
        setattr(collect, name, retries(originals[name], name))
    try:
        collect.main(["--date", TARGET, "--output-dir", "."])
    finally:
        for name, original in originals.items():
            setattr(collect, name, original)

    path = D / "sources.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    collected = payload["generated_at_utc"]
    payload["target_date"] = TARGET
    payload["collected_at_utc"] = collected
    payload["note"] = f"Données collectées le {collected} (UTC) ; date cible {TARGET}."
    write_json(path, payload)

    chart = merge.merge(ROOT, TARGET, min_sources=2, fill=True)
    if chart["count"] != 50:
        raise SystemExit(f"Attendu 50 rangs, obtenu {chart['count']}")
    chart["chart"] = "DZ TOP 50"
    chart["collected_at_utc"] = collected
    chart["collected_on"] = collected[:10]
    chart["note"] = payload["note"]
    write_json(D / f"{BASE}.json", chart)
    (D / f"{BASE}.md").write_text(render_md(chart), encoding="utf-8")
    (D / f"{BASE}.html").write_text(render_html(chart), encoding="utf-8")
    (D / f"{BASE}.csv").write_text(render_csv(chart), encoding="utf-8")
    print(f"Classement {TARGET} actualisé : {chart['count']} rangs")
    print(f"Collecte réelle : {collected}")
    print("Livrables racine : non modifiés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
