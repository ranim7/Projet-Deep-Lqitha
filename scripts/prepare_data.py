"""Step 2 - Turn the raw downloads into clean, project-ready files (data/processed/).

  places_tn.json          real Tunisian airports / stations / malls from OpenStreetMap, with governorate
  items_catalog.csv       ABO products mapped to our 18 categories and 16 colour families
  delhi_metro_found_items.csv / delhi_metro_stats.json  13.7k real found-item records (metro) -> taxonomy + text benchmark
  tsa_claims_stats.json   US airport property-claim records -> what is lost at airports
"""
import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config  # noqa: E402

RAW, OUT = config.RAW, config.PROCESSED

# 24 governorate capitals (lat, lon) - used to attach a governorate to every OSM place
GOVS = {
    "Tunis": (36.806, 10.181), "Ariana": (36.862, 10.195), "Ben Arous": (36.753, 10.231), "Manouba": (36.808, 10.097),
    "Nabeul": (36.456, 10.737), "Zaghouan": (36.402, 10.143), "Bizerte": (37.274, 9.874), "Beja": (36.725, 9.182),
    "Jendouba": (36.501, 8.780), "Kef": (36.174, 8.704), "Siliana": (36.085, 9.371), "Sousse": (35.826, 10.636),
    "Monastir": (35.778, 10.826), "Mahdia": (35.504, 11.062), "Sfax": (34.740, 10.760), "Kairouan": (35.678, 10.096),
    "Kasserine": (35.168, 8.836), "Sidi Bouzid": (35.038, 9.485), "Gabes": (33.881, 10.098), "Medenine": (33.354, 10.505),
    "Tataouine": (32.929, 10.451), "Gafsa": (34.425, 8.784), "Tozeur": (33.920, 8.134), "Kebili": (33.705, 8.969),
}


def nearest_gov(lat, lon):
    return min(GOVS, key=lambda g: math.hypot(GOVS[g][0] - lat, (GOVS[g][1] - lon) * math.cos(math.radians(lat))))


def places():
    src = RAW / "osm" / "tunisia_places_osm.json"
    out = []
    if src.exists():
        for el in json.loads(src.read_text(encoding="utf-8")).get("elements", []):
            t = el.get("tags", {})
            lat = el.get("lat") or (el.get("center") or {}).get("lat")
            lon = el.get("lon") or (el.get("center") or {}).get("lon")
            name = t.get("name:fr") or t.get("name") or t.get("name:en")
            if not (lat and lon and name):
                continue
            if t.get("aeroway") == "aerodrome":
                kind = "airport"
            elif t.get("railway") in ("station", "halt"):
                kind = "metro" if (t.get("station") in ("light_rail", "subway") or t.get("light_rail") == "yes") else "train_station"
            elif t.get("amenity") == "bus_station":
                kind = "louage" if "louage" in name.lower() else "bus_station"
            elif t.get("amenity") == "ferry_terminal":
                kind = "port"
            elif t.get("shop") == "mall":
                kind = "mall"
            elif t.get("tourism") == "museum":
                kind = "museum"
            elif t.get("public_transport") == "station":
                kind = "bus_station"
            else:
                continue
            out.append({"name": name, "name_ar": t.get("name:ar"), "place_type": kind,
                        "governorate": nearest_gov(lat, lon), "lat": round(lat, 5), "lon": round(lon, 5),
                        "iata": t.get("iata"), "osm_id": f"{el['type']}/{el['id']}"})
    # de-duplicate by (name, type)
    seen, uniq = set(), []
    for p in sorted(out, key=lambda p: (p["place_type"], p["name"])):
        k = (p["name"].lower(), p["place_type"])
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    # always add street-level entries (one per governorate) for street losses
    for g, (lat, lon) in GOVS.items():
        uniq.append({"name": f"Rue / espace public - {g}", "place_type": "street", "governorate": g, "lat": lat, "lon": lon})
    (OUT / "places_tn.json").write_text(json.dumps(uniq, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"places_tn.json: {len(uniq)} places", Counter(p["place_type"] for p in uniq))


COLOR_MAP = {"black": "black", "white": "white", "grey": "grey", "gray": "grey", "silver": "silver", "gold": "gold",
             "brown": "brown", "beige": "beige", "tan": "brown", "khaki": "beige", "ivory": "beige", "cream": "beige",
             "red": "red", "pink": "pink", "orange": "orange", "yellow": "yellow", "green": "green", "blue": "blue",
             "navy": "blue", "turquoise": "blue", "purple": "purple", "multi": "multicolor", "multicolor": "multicolor",
             "multicolour": "multicolor", "clear": "transparent", "transparent": "transparent", "rose gold": "pink",
             "bronze": "brown", "charcoal": "grey", "teal": "green", "burgundy": "red", "maroon": "red", "olive": "green"}


def color_family(*vals):
    for v in vals:
        v = (v or "").lower()
        for k in sorted(COLOR_MAP, key=len, reverse=True):
            if re.search(rf"\b{k}\b", v):
                return COLOR_MAP[k]
    return None


def items():
    src = RAW / "abo" / "abo_items.csv"
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    out = []
    for r in rows:
        r["color_family"] = color_family(r.get("color_std"), r.get("color"), r.get("item_name"))
        r["image_path"] = str(Path("data/raw/abo") / r["image_file"])
        out.append(r)
    with open(OUT / "items_catalog.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"items_catalog.csv: {len(out)} items", Counter(r["category"] for r in out),
          "| with color family:", sum(1 for r in out if r["color_family"]))


# keyword -> Lqitha category (applied to the English item names of the Delhi Metro dataset)
EN_MAP = [("mobile", "phone"), ("phone", "phone"), ("purse", "wallet"), ("wallet", "wallet"), ("pouch", "wallet"),
          ("suitcase", "luggage"), ("trolley", "luggage"), ("luggage", "luggage"), ("bag", "bag"), ("backpack", "bag"),
          ("key", "keys"), ("aadhar", "id_document"), ("aadhaar", "id_document"), ("pan card", "id_document"),
          ("licence", "id_document"), ("license", "id_document"), ("passport", "id_document"), ("id card", "id_document"),
          ("atm", "bank_card"), ("debit", "bank_card"), ("credit", "bank_card"), ("spectacle", "glasses"),
          ("glass", "glasses"), ("goggle", "glasses"), ("watch", "watch"), ("earbud", "headphones"), ("earphone", "headphones"),
          ("headphone", "headphones"), ("airpod", "headphones"), ("laptop", "laptop"), ("charger", "electronics"),
          ("power bank", "electronics"), ("powerbank", "electronics"), ("tablet", "electronics"), ("camera", "electronics"),
          ("jacket", "clothing"), ("shirt", "clothing"), ("sweater", "clothing"), ("shawl", "clothing"), ("cap", "clothing"),
          ("coat", "clothing"), ("scarf", "clothing"), ("chain", "jewelry"), ("ring", "jewelry"), ("bangle", "jewelry"),
          ("earring", "jewelry"), ("umbrella", "umbrella"), ("shoe", "shoes"), ("slipper", "shoes"), ("sandal", "shoes"),
          ("bottle", "bottle")]


def map_en(name):
    n = (name or "").lower()
    return next((c for k, c in EN_MAP if k in n), "other")


def delhi():
    src = RAW / "kaggle" / "forgetabhi__delhi-metro-lost-and-found-dataset" / "delhimetrorail.csv"
    rows = list(csv.DictReader(open(src, encoding="utf-8", errors="ignore")))
    cats = Counter()
    clean = []
    for r in rows:
        name, desc = (r.get("item_name") or "").strip(), (r.get("description") or "").strip()
        if not name:
            continue
        c = map_en(name + " " + desc)
        cats[c] += 1
        clean.append({"item_name": name, "description": desc, "category": c, "station": r.get("station_name", "").strip(),
                      "date": r.get("receiving_date", ""), "time": r.get("receiving_time", "")})
    with open(OUT / "delhi_metro_found_items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(clean[0].keys()))
        w.writeheader()
        w.writerows(clean)
    stats = {"rows": len(clean), "stations": len({r["station"] for r in clean}),
             "category_distribution": cats.most_common(),
             "top_item_names": Counter(r["item_name"].upper() for r in clean).most_common(30),
             "with_description": sum(1 for r in clean if len(r["description"]) > 3),
             "avg_description_words": round(sum(len(r["description"].split()) for r in clean) / len(clean), 1)}
    (OUT / "delhi_metro_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"delhi_metro_found_items.csv: {len(clean)} rows; categories: {cats.most_common(8)}")


def tsa():
    d = RAW / "kaggle" / "terminal-security-agency__tsa-claims-database"
    files = sorted(d.rglob("*.csv"), key=lambda p: -p.stat().st_size) if d.exists() else []
    if not files:
        return
    with open(files[0], encoding="utf-8", errors="ignore") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        icol = next((c for c in cols if c.lower().startswith("item")), None)
        tcol = next((c for c in cols if "claim type" in c.lower()), None)
        items, types, n = Counter(), Counter(), 0
        for r in rd:
            n += 1
            for it in (r.get(icol) or "").split(";"):
                if it.strip():
                    items[it.strip().split("(")[0].strip()] += 1
            types[r.get(tcol, "")] += 1
    out = {"file": files[0].name, "rows": n, "columns": cols, "claim_types": types.most_common(10), "top_items": items.most_common(20)}
    (OUT / "tsa_claims_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"tsa_claims_stats.json: {n} claims; top items {items.most_common(5)}")


if __name__ == "__main__":
    for step in (places, items, delhi, tsa):
        try:
            step()
        except Exception as e:
            print(f"!! {step.__name__} failed: {e}")
