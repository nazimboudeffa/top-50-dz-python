#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vérifications hors ligne du classement DZ TOP 50 ciblé sur 2026-11-27."""
from __future__ import annotations
import csv, json, re, sys
from pathlib import Path

D = Path(__file__).resolve().parent
ROOT = D.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import merge

BASE = "DZ_TOP_50_20261127"
STAMP = "20261127"
TARGET = "2026-11-27"
ok = ko = 0


def check(label, condition, detail=""):
    global ok, ko
    if condition:
        ok += 1
        print(f"  OK    {label}")
    else:
        ko += 1
        print(f"  ECHEC {label} {detail}")


chart = json.loads((D / f"{BASE}.json").read_text(encoding="utf-8-sig"))
sources = json.loads((D / "sources.json").read_text(encoding="utf-8-sig"))

check("pipeline multi_source", chart.get("pipeline") == "multi_source")
check("date cible", chart.get("chart_date") == TARGET)
check("date de collecte renseignée", bool(chart.get("collected_at_utc")))
check("50 rangs", chart.get("count") == len(chart["entries"]) == 50)
check("rangs 1..50", [e["rank"] for e in chart["entries"]] == list(range(1, 51)))
check("song_id uniques", len({e["song_id"] for e in chart["entries"]}) == 50)
check("3 sources", set(sources["sources"]) == {"apple_music", "audiomack", "spotify"})

counts = {n: len(s["entries"]) for n, s in sources["sources"].items()}
weights = {n: s["weight"] for n, s in sources["sources"].items()}
errors = []
for e in chart["entries"]:
    parts = [(weights[s], merge.position_score(p, counts[s])) for s, p in e["positions"].items()]
    expected = round(sum(w * x for w, x in parts) / sum(w for w, _ in parts), 2)
    if abs(expected - e["score"]) > 0.011:
        errors.append(e["rank"])
check("scores cohérents", not errors, errors)

rows = list(csv.DictReader((D / f"{BASE}.csv").open(encoding="utf-8", newline="")))
check("CSV 50 lignes", len(rows) == 50)
check("CSV date de collecte", all(r["collected_at_utc"] == chart["collected_at_utc"] for r in rows))
check("CSV aligné", [(int(r["rank"]), r["title"]) for r in rows] == [(e["rank"], e["title"]) for e in chart["entries"]])

md = (D / f"{BASE}.md").read_text(encoding="utf-8")
doc = (D / f"{BASE}.html").read_text(encoding="utf-8")
check("Markdown 50 lignes", len(re.findall(r"^\| (\d+|—) \|", md, flags=re.MULTILINE)) == 50)
check("Markdown collecte", chart["collected_at_utc"] in md and TARGET in md)
check("HTML 50 rangs", doc.count('<td class="rank">') == 50)
check("HTML collecte", chart["collected_at_utc"] in doc and TARGET in doc)

previous = merge.find_previous_chart(ROOT, "2026-11-28")
check("évolution officielle disponible", previous is not None and previous.get("chart_date") == "2026-11-27", previous and previous.get("chart_date"))
print(f"\n{ok} vérifications OK, {ko} échec(s)")
sys.exit(1 if ko else 0)
