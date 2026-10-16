"""Seed the demo database with realistic FOUND reports so the matcher has something to search:
 - photo reports: real product photos (Amazon Berkeley Objects) already analysed by the Perception agent
   during the evaluation (cache), placed in real Tunisian places (OpenStreetMap) over the last 10 days
 - text reports: real found-item records from Delhi Metro, re-located to Tunisian metro / train stations
All seeded reports are marked source='seed'. Run after scripts/eval_matching.py (uses its cache).
"""
import csv
import json
import random
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, store  # noqa: E402

random.seed(3)
PLACES = [p for p in json.loads((config.PROCESSED / "places_tn.json").read_text(encoding="utf-8"))
          if p["place_type"] in ("airport", "train_station", "metro", "bus_station", "louage", "mall")]


def place():
    p = random.choice(PLACES)
    d = date.today() - timedelta(days=random.randint(0, 10))
    return {"place_name": p["name"], "place_type": p["place_type"], "governorate": p["governorate"], "date_iso": d.isoformat()}


if __name__ == "__main__":
    with store.conn() as c:
        if c.execute("SELECT COUNT(*) FROM reports WHERE source='seed'").fetchone()[0]:
            print("already seeded")
            sys.exit(0)
    items = {r["item_id"]: r for r in csv.DictReader(open(config.PROCESSED / "items_catalog.csv", encoding="utf-8"))}
    from lqitha.evalutil import cached_prompt
    n = 0
    for it in items.values():
        img = config.ROOT / it["image_path"]
        r = cached_prompt("perc", "perception", "v2", image_path=img, only_cache=True, report_type="found",
                          text="(photo only)", slots={}, has_photo=True) or \
            cached_prompt("perclocal", "perception", "v2", image_path=img, only_cache=True, report_type="found",
                          text="(photo only)", slots={}, has_photo=True, model_override="local:" + config.LOCAL_VISION_MODEL)
        if not r or not r.get("parsed"):
            continue
        dst = f"seed_{it['item_id']}.jpg"
        shutil.copy(img, config.UPLOADS / dst)
        hidden = f"brand {it['brand']}" if it.get("brand") else (it.get("material") or "")
        store.add_report("found", r["parsed"], place(), session_id="S-seed-staff", language="fr", hidden_detail=hidden,
                         contact={"name": "Bureau objets trouvés"}, image_file=dst, source="seed")
        n += 1
    rows = [r for r in csv.DictReader(open(config.PROCESSED / "delhi_metro_found_items.csv", encoding="utf-8"))
            if r["category"] != "other" and len(r["description"].split()) >= 2]
    random.shuffle(rows)
    for r in rows[:150]:
        txt = f"{r['item_name'].title()}: {r['description'].lower()}"
        store.add_report("found", {"category": r["category"], "canonical_description": txt}, place(),
                         session_id="S-seed-staff", language="en", hidden_detail=r["description"],
                         contact={"name": "Agent de station"}, source="seed")
        n += 1
    print(f"seeded {n} found reports")
