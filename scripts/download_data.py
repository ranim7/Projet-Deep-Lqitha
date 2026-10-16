"""
Step 1 - Download all REAL datasets used by the Lqitha project.

Run from the project root:  python scripts/download_data.py
Every source is public and free. Each block is independent: if one source
fails, the others still download and the failure is written to the log.

Outputs (data/raw/):
  sncf/            SNCF found-items + loss declarations (France open data)  -> item taxonomy + realistic stats
  abo/             Amazon Berkeley Objects: real product photos + attributes  -> perception + matching benchmark
  osm/             OpenStreetMap: real Tunisian airports, train/bus/metro stations -> place gazetteer
  audio_tn/        Tunisian Derja speech clips + transcripts (Hugging Face)     -> speech-to-text evaluation
  rag_corpus/      Tunisian procedures (idaraty.tn, laws, Tunisair, ICAO)      -> RAG knowledge base
"""
import gzip
import io
import json
import os
import random
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
LOG = RAW / "download_log.txt"
random.seed(42)
UA = {"User-Agent": "Mozilla/5.0 (Lqitha student project; Esprit 5DS)"}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get(url, **kw):
    kw.setdefault("timeout", 120)
    kw.setdefault("headers", UA)
    r = requests.get(url, **kw)
    r.raise_for_status()
    return r


def save(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    log(f"  saved {path.relative_to(ROOT)} ({len(content)/1e6:.2f} MB)")


# ---------------------------------------------------------------- 1. SNCF
def sncf():
    log("== SNCF found items (ressources.data.sncf.com) ==")
    if (RAW / "sncf" / "sncf_found_items.csv").exists():
        log("  already downloaded - skip"); return
    out = RAW / "sncf"
    out.mkdir(parents=True, exist_ok=True)
    targets = {"sncf_found_items.csv": ["objets-trouves-restitution", "objets-trouves-gares-restitution"],
               "sncf_loss_declarations.csv": ["objets-trouves-gares", "objets-trouves-declarations-de-perte"]}
    portals = ["https://data.sncf.com", "https://ressources.data.sncf.com"]
    # 1) discover ids whose name contains "objet" on each portal
    for portal in portals:
        try:
            cat = get(f"{portal}/api/explore/v2.1/catalog/datasets", params={"limit": 100, "where": 'search("objets")'}).json()
            ids = [d["dataset_id"] for d in cat.get("results", []) if "objet" in d["dataset_id"]]
            log(f"  {portal}: datasets with 'objet' -> {ids}")
            for fname, cands in targets.items():
                for i in ids:
                    if i not in cands and (("restitution" in i) == (fname == "sncf_found_items.csv")):
                        cands.append(i)
        except Exception as e:
            log(f"  catalog {portal} failed: {e}")
    # 2) try every (portal, id) until one export works
    for fname, cands in targets.items():
        done = False
        for portal in portals:
            for ds in cands:
                url = f"{portal}/api/explore/v2.1/catalog/datasets/{ds}/exports/csv"
                for params in ({"delimiter": ";", "where": "date >= date'2025-01-01'"}, {"delimiter": ";"}):
                    try:
                        r = get(url, params=params, timeout=900)
                        if len(r.content) > 1000:
                            save(out / fname, r.content)
                            log(f"  ok {fname} from {portal} / {ds} {'(2025+)' if 'where' in params else '(full)'}")
                            done = True
                            break
                    except Exception as e:
                        log(f"  {portal}/{ds}: {str(e)[:120]}")
                if done:
                    break
            if done:
                break
        # 3) fallback: every CSV resource / community resource of the data.gouv.fr dataset
        if not done:
            try:
                meta = get("https://www.data.gouv.fr/api/1/datasets/objets-trouves/").json()
                res = meta.get("resources", [])
                try:
                    res += get("https://www.data.gouv.fr/api/1/datasets/community_resources/",
                               params={"dataset": meta["id"], "page_size": 50}).json().get("data", [])
                except Exception:
                    pass
                for x in res:
                    url = x.get("latest") or x.get("url")
                    if "csv" not in (x.get("format") or "").lower() and not url.endswith(".csv"):
                        continue
                    try:
                        r = get(url, timeout=900)
                        if len(r.content) > 1000:
                            save(out / fname, r.content)
                            log(f"  ok {fname} from data.gouv.fr resource {x.get('title')}")
                            done = True
                            break
                    except Exception as e:
                        log(f"  data.gouv resource {x.get('title')}: {str(e)[:100]}")
            except Exception as e:
                log(f"  data.gouv.fr fallback failed: {e}")


# ---------------------------------------------------------------- 2. ABO
ABO = "https://amazon-berkeley-objects.s3.amazonaws.com"
# product types that people actually lose in stations/airports/streets
KEEP_TYPES = {
    "CELLULAR_PHONE_CASE": "phone", "CELLULAR_PHONE": "phone",
    "HANDBAG": "bag", "BACKPACK": "bag", "LUGGAGE": "luggage", "SUITCASE": "luggage",
    "WALLET": "wallet", "WATCH": "watch", "SUNGLASSES": "glasses", "EYEWEAR": "glasses",
    "KEYCHAIN": "keys", "HEADPHONES": "headphones", "EARPHONE": "headphones",
    "UMBRELLA": "umbrella", "COAT": "clothing", "JACKET": "clothing", "HAT": "clothing",
    "SCARF": "clothing", "TABLET_COMPUTER": "electronics",
    "PORTABLE_ELECTRONIC_DEVICE_COVER": "electronics", "NOTEBOOK_COMPUTER": "laptop",
    "COMPUTER_ADD_ON": "electronics", "SHOES": "shoes", "BOOT": "shoes",
    "SANDAL": "shoes", "WATER_BOTTLE": "bottle", "BOTTLE": "bottle",
}
PER_TYPE = 30


def _en(field):
    if not field:
        return None
    for v in field:
        if v.get("language_tag", "en_US").startswith("en"):
            return v.get("value")
    return field[0].get("value")


def abo():
    log("== Amazon Berkeley Objects (listings + small images) ==")
    out = RAW / "abo"
    if (out / "abo_items.csv").exists():
        log("  already downloaded - skip"); return
    (out / "images").mkdir(parents=True, exist_ok=True)
    rows, type_counts = [], {}
    for h in "0123456789abcdef":
        url = f"{ABO}/listings/metadata/listings_{h}.json.gz"
        try:
            data = gzip.decompress(get(url).content).decode("utf-8")
        except Exception as e:
            log(f"  could not get listings_{h}: {e}")
            continue
        for line in data.splitlines():
            it = json.loads(line)
            pt = _en(it.get("product_type")) or ""
            type_counts[pt] = type_counts.get(pt, 0) + 1
            if pt in KEEP_TYPES and it.get("main_image_id") and _en(it.get("item_name")):
                color = it.get("color") or []
                std_color = None
                if color and color[0].get("standardized_values"):
                    std_color = color[0]["standardized_values"][0]
                rows.append({
                    "item_id": it["item_id"],
                    "product_type": pt,
                    "category": KEEP_TYPES[pt],
                    "item_name": _en(it.get("item_name")),
                    "brand": _en(it.get("brand")),
                    "color": _en(color),
                    "color_std": std_color,
                    "material": _en(it.get("material")),
                    "model_name": _en(it.get("model_name")),
                    "keywords": "; ".join(k.get("value", "") for k in (it.get("item_keywords") or [])[:8]),
                    "main_image_id": it["main_image_id"],
                })
        log(f"  listings_{h}: kept {len(rows)} so far")
    (out / "abo_product_type_counts.json").write_text(
        json.dumps(dict(sorted(type_counts.items(), key=lambda x: -x[1])), indent=1), encoding="utf-8")

    # balanced sample per type
    by_type = {}
    for r in rows:
        by_type.setdefault(r["product_type"], []).append(r)
    sample = []
    for pt, lst in by_type.items():
        random.shuffle(lst)
        lst.sort(key=lambda r: (r["color"] is None, r["brand"] is None))  # prefer complete rows
        sample += lst[:PER_TYPE]
    log(f"  sampled {len(sample)} items over {len(by_type)} product types")

    # image paths
    img_meta = gzip.decompress(get(f"{ABO}/images/metadata/images.csv.gz").content).decode("utf-8")
    wanted = {r["main_image_id"] for r in sample}
    paths = {}
    for line in img_meta.splitlines()[1:]:
        iid, h, w, p = line.split(",")
        if iid in wanted:
            paths[iid] = p

    def dl(r):
        p = paths.get(r["main_image_id"])
        if not p:
            return None
        dst = out / "images" / f"{r['item_id']}.jpg"
        if not dst.exists():
            try:
                dst.write_bytes(get(f"{ABO}/images/small/{p}", timeout=60).content)
            except Exception:
                return None
        r["image_file"] = f"images/{r['item_id']}.jpg"
        return r

    with ThreadPoolExecutor(8) as ex:
        done = [r for r in ex.map(dl, sample) if r]
    import csv
    with open(out / "abo_items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(done[0].keys()))
        w.writeheader()
        w.writerows(done)
    log(f"  abo_items.csv: {len(done)} items with images")


# ---------------------------------------------------------------- 3. OSM places
def osm():
    log("== OpenStreetMap: Tunisian transport places (Overpass API) ==")
    f = RAW / "osm" / "tunisia_places_osm.json"
    if f.exists() and json.loads(f.read_text(encoding="utf-8")).get("elements"):
        log("  already downloaded - skip"); return
    q = """
    [out:json][timeout:180];
    area["ISO3166-1"="TN"][admin_level=2]->.tn;
    (
      nwr["aeroway"="aerodrome"]["iata"](area.tn);
      nwr["railway"="station"](area.tn);
      nwr["railway"="halt"](area.tn);
      nwr["amenity"="bus_station"](area.tn);
      nwr["public_transport"="station"](area.tn);
      nwr["amenity"="ferry_terminal"](area.tn);
      nwr["shop"="mall"](area.tn);
      nwr["tourism"="museum"](area.tn);
    );
    out center tags;
    """
    hdr = {"User-Agent": "Lqitha-student-project/1.0 (Esprit 5DS, educational)", "Accept": "*/*"}
    for ep in ["https://overpass-api.de/api/interpreter",
               "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
               "https://overpass.private.coffee/api/interpreter"]:
        try:
            r = requests.post(ep, data={"data": q}, headers=hdr, timeout=300)
            r.raise_for_status()
            n = len(r.json().get("elements", []))
            log(f"  {ep}: {n} elements")
            if n == 0:
                continue
            save(RAW / "osm" / "tunisia_places_osm.json", r.content)
            return
        except Exception as e:
            log(f"  overpass {ep} failed: {e}")


# ---------------------------------------------------------------- 4. audio
AUDIO_CANDIDATES = [
    "linagora/linto-dataset-audio-ar-tn",
    "Elyadata/SLURP-TN",
    "FARUKxAUTO/tunisian-asr-cleaned",
    "linagora/linto-dataset-audio-ar-tn-augmented",
]
N_AUDIO = 40


def audio():
    log("== Tunisian Derja speech (Hugging Face) ==")
    from huggingface_hub import HfApi, hf_hub_download
    import pyarrow.parquet as pq
    out = RAW / "audio_tn"
    if (out / "audio_tn_samples.json").exists() and len(json.loads((out / "audio_tn_samples.json").read_text(encoding="utf-8"))) >= 10:
        log("  already downloaded - skip"); return
    api = HfApi()
    for repo in AUDIO_CANDIDATES:
        try:
            files = api.list_repo_files(repo, repo_type="dataset")
            pqs = [f for f in files if f.endswith(".parquet")]
            revision = None
            if not pqs:
                files = api.list_repo_files(repo, repo_type="dataset", revision="refs/convert/parquet")
                pqs = [f for f in files if f.endswith(".parquet")]
                revision = "refs/convert/parquet"
            if not pqs:
                log(f"  {repo}: no parquet files")
                continue
            test = [f for f in pqs if "test" in f.lower()] or [f for f in pqs if "valid" in f.lower()] or pqs
            infos = {s.rfilename: s.size for s in api.dataset_info(repo, revision=revision, files_metadata=True).siblings}
            test = [f for f in test if (infos.get(f) or 0) < 150e6] or test
            test.sort(key=lambda f: infos.get(f) or 1e12)
            meta, k = [], 0
            (out / "clips").mkdir(parents=True, exist_ok=True)
            for fpath in test:
                if len(meta) >= N_AUDIO:
                    break
                path = hf_hub_download(repo, fpath, repo_type="dataset", revision=revision)
                table = pq.read_table(path)
                cols = table.column_names
                acol = next(c for c in cols if c.lower() in ("audio", "speech", "wav"))
                tcol = next(c for c in cols if c.lower() in ("transcript", "text", "sentence", "transcription", "normalized_text"))
                n = min(N_AUDIO - len(meta), table.num_rows, 10)   # max 10 clips per file -> speaker diversity
                for i in sorted(random.sample(range(table.num_rows), n)):
                    a = table.column(acol)[i].as_py()
                    b = a.get("bytes") if isinstance(a, dict) else a
                    ext = Path((a.get("path") or "x.wav") if isinstance(a, dict) else "x.wav").suffix or ".wav"
                    if not b or len(b) > 2_000_000:      # skip very long clips (> ~1 min)
                        continue
                    fn = f"clip_{k:03d}{ext}"
                    k += 1
                    (out / "clips" / fn).write_bytes(b)
                    meta.append({"file": f"clips/{fn}", "reference": table.column(tcol)[i].as_py(), "source": f"{repo}/{fpath}"})
                log(f"  {repo}: {len(meta)} clips so far ({fpath})")
            (out / "audio_tn_samples.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            if len(meta) >= 10:
                return
        except Exception as e:
            log(f"  {repo} failed: {type(e).__name__}: {e}")
    log("  !! no audio dataset could be downloaded (gated?). Record clips manually.")


# ---------------------------------------------------------------- 5. RAG corpus
RAG_SOURCES = [
    # (id, url, authority, item_type, lang)
    ("idaraty_passport_duplicate_tn", "https://idaraty.tn/fr/procedures/obtention-dun-duplicata-du-passport-tunisiens-residents-sur-le-territoire-tunisien", "Ministère de l'Intérieur", "passport", "fr"),
    ("idaraty_passport_duplicate_abroad", "https://idaraty.tn/fr/procedures/obtention-dun-duplicata-du-passport-tunisiens-residents-a-l-etranger", "Ministère de l'Intérieur", "passport", "fr"),
    ("idaraty_driving_licence_duplicate", "https://idaraty.tn/fr/procedures/duplicata-du-permis-de-conduire-en-cas-de-perte-ou-vol", "Agence Technique des Transports Terrestres", "driving_licence", "fr"),
    ("idaraty_driving_licence_news_derja", "https://idaraty.tn/fr/news/2020/06/dha3t-rokhset-siye9tik-idaraty-t9ollik-kifech-tjaddidha-fi-3-khotwet", "idaraty", "driving_licence", "derja"),
    ("idaraty_family_booklet_duplicate", "https://idaraty.tn/fr/procedures/duplicata-dun-livret-de-famille-perte", "Municipalité", "family_booklet", "fr"),
    ("idaraty_passport_duplicate_tn_ar", "https://idaraty.tn/ar/procedures/obtention-dun-duplicata-du-passport-tunisiens-residents-sur-le-territoire-tunisien", "Ministère de l'Intérieur", "passport", "ar"),
    ("idaraty_driving_licence_duplicate_ar", "https://idaraty.tn/ar/procedures/duplicata-du-permis-de-conduire-en-cas-de-perte-ou-vol", "Agence Technique des Transports Terrestres", "driving_licence", "ar"),
    ("law_2024_22_national_id_card", "https://legislation-securite.tn/wp-content/uploads/sites/5/2024/03/Loi-organique-n%C2%B0-2024-22-du-11-mars-2024.pdf", "Loi organique 2024-22", "national_id", "fr"),
    ("tunisair_passenger_protection_2026", "https://www.tunisair.com/sites/default/files/2026-07/protection-des-passagers_v1f_2026-30.06.2026.pdf", "Tunisair", "baggage", "fr"),
    ("montreal_convention_1999_text_fr", "https://www.admin.ch/opc/fr/classified-compilation/20031862/201109010000/0.748.411.pdf", "Convention de Montréal 1999 (texte officiel, recueil systématique suisse)", "baggage", "fr"),
    ("icao_montreal_convention_1999_fr", "https://www.icao.int/sites/default/files/secretariat/legal/Administrative%20Packages/mtl99_fr.pdf", "OACI / ICAO", "baggage", "fr"),
    ("guide_lost_bank_card_tunisia", "https://www.lemagvoyage.fr/tunisie/perdre-carte-bancaire-tunisie-opposition-retrait-urgence-solutions/", "Guide voyage", "bank_card", "fr"),
    ("netherlands_emergency_document_tunisia", "https://www.netherlandsworldwide.nl/passport-id-card/emergency-document/apply-tunisia.fr", "Ambassade des Pays-Bas", "passport", "fr"),
]


def rag_corpus():
    log("== RAG corpus: Tunisian procedures ==")
    from bs4 import BeautifulSoup
    out = RAW / "rag_corpus"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    old = {m["id"]: m for m in json.loads((out / "manifest.json").read_text(encoding="utf-8"))} if (out / "manifest.json").exists() else {}
    for sid, url, authority, item_type, lang in RAG_SOURCES:
        if sid in old and (out / old[sid]["file"]).exists():
            manifest.append(old[sid]); log(f"  have {sid}"); continue
        try:
            try:
                r = get(url, timeout=60)
            except Exception as e1:
                log(f"  live site failed for {sid} ({str(e1)[:80]}); trying Internet Archive copies")
                r = None
                for ts in ("2026", "2025", "2024", "2023"):
                    try:
                        cand = get(f"https://web.archive.org/web/{ts}id_/{url}", timeout=120)
                        if len(cand.content) > 3000:
                            r = cand
                            break
                    except Exception:
                        pass
                if r is None:
                    raise RuntimeError("no usable archive copy")
            ctype = r.headers.get("content-type", "")
            if url.lower().endswith(".pdf") or "pdf" in ctype:
                fn = f"{sid}.pdf"
                (out / fn).write_bytes(r.content)
            else:
                soup = BeautifulSoup(r.text, "html.parser")
                for t in soup(["script", "style", "nav", "footer", "header", "noscript", "form", "aside"]):
                    t.decompose()
                main = soup.find("main") or soup.find("article") or soup.body or soup
                # keep headings so we can chunk by structure later
                lines = []
                for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "td"]):
                    txt = " ".join(el.get_text(" ", strip=True).split())
                    if not txt:
                        continue
                    if el.name in ("h1", "h2", "h3", "h4"):
                        lines.append("\n" + "#" * int(el.name[1]) + " " + txt)
                    elif el.name == "li":
                        lines.append("- " + txt)
                    else:
                        lines.append(txt)
                text = "\n".join(lines).strip()
                if len(text) < 200:
                    raise ValueError(f"page text too short ({len(text)} chars)")
                fn = f"{sid}.md"
                (out / fn).write_text(f"# SOURCE: {url}\n\n{text}\n", encoding="utf-8")
            manifest.append({"id": sid, "file": fn, "url": url, "authority": authority,
                             "item_type": item_type, "lang": lang, "bytes": len(r.content)})
            log(f"  ok  {sid}")
        except Exception as e:
            log(f"  FAIL {sid}: {e}")
        time.sleep(1)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  corpus: {len(manifest)}/{len(RAG_SOURCES)} documents")


# ---------------------------------------------------------------- 6. Kaggle (uses your ~/.kaggle/kaggle.json)
def kaggle():
    log("== Kaggle: real lost-and-found datasets ==")
    out = RAW / "kaggle"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "terminal-security-agency__tsa-claims-database").exists() and (out / "forgetabhi__delhi-metro-lost-and-found-dataset").exists():
        log("  already downloaded - skip"); return
    from kaggle.api.kaggle_api_extended import KaggleApi
    api = KaggleApi()
    api.authenticate()
    found = {}
    for term in ["objets trouves", "sncf objets", "lost and found", "lost property", "lost items"]:
        try:
            for d in api.dataset_list(search=term)[:20]:
                found[d.ref] = {"ref": d.ref, "title": d.title, "size": getattr(d, "total_bytes", None) or getattr(d, "totalBytes", None), "term": term}
        except Exception as e:
            log(f"  search '{term}' failed: {e}")
    (out / "kaggle_search_results.json").write_text(json.dumps(list(found.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  {len(found)} candidate datasets: " + ", ".join(list(found)[:25]))
    def score(d):
        t = (d["ref"] + " " + d["title"]).lower()
        return (("sncf" in t or "objets" in t or "trouv" in t) * 3 + ("lost" in t and ("found" in t or "property" in t)) * 2
                - ("road" in t or "hazard" in t or "cityscapes" in t) * 5)
    # explicit, reviewed choices (the search also returns irrelevant datasets, e.g. pet shelters)
    chosen = ["forgetabhi/delhi-metro-lost-and-found-dataset", "terminal-security-agency/tsa-claims-database"]
    ranked = [found.get(r, {"ref": r, "title": r, "size": None}) for r in chosen]
    got = 0
    for d in ranked:
        dst = out / d["ref"].replace("/", "__")
        if dst.exists():
            got += 1
            continue
        try:
            api.dataset_download_files(d["ref"], path=str(dst), unzip=True, quiet=True)
            files = [str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file()]
            log(f"  downloaded {d['ref']} ({d['title']}): {files[:10]}")
            got += 1
        except Exception as e:
            log(f"  download {d['ref']} failed: {e}")


STEPS = {"sncf": sncf, "kaggle": kaggle, "abo": abo, "osm": osm, "audio": audio, "rag": rag_corpus}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(STEPS)
    log(f"##### download start: {wanted}")
    for name in wanted:
        try:
            STEPS[name]()
        except Exception as e:
            log(f"!! step {name} crashed: {e}\n{traceback.format_exc()}")
    log("##### download finished")
