"""End-to-end smoke test through the real stack: API /chat -> n8n -> Groq agents -> tools.
Scenario: a finder reports a found phone with a photo (+ hidden detail), then the owner reports it lost in Derja,
receives the ownership question and answers it. Also one procedure question. Writes logs/smoke_test.json.
"""
import base64
import csv
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config  # noqa: E402

API = "http://localhost:8000"
LOG = []


def chat(body, label):
    t = time.time()
    r = requests.post(f"{API}/chat", json=body, timeout=300)
    d = r.json()
    LOG.append({"step": label, "sent": {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 200 else v) for k, v in body.items()},
                "status": r.status_code, "seconds": round(time.time() - t, 1), "response": d})
    print(f"[{label}] {r.status_code} {d.get('kind')} ({round(time.time() - t, 1)}s): {str(d.get('reply'))[:300]}")
    return d


if __name__ == "__main__":
    print(requests.get(f"{API}/health").json())
    items = list(csv.DictReader(open(config.PROCESSED / "items_catalog.csv", encoding="utf-8")))
    phone = next(i for i in items if i["category"] == "phone" and i["color_family"])
    img = base64.b64encode((config.ROOT / phone["image_path"]).read_bytes()).decode()
    loc = {"governorate": "Sousse", "place_name": "Gare de Sousse", "place_type": "train_station"}
    # finder
    f = chat({"action": "message", "message": "J'ai trouvé ce téléphone dans le train à la gare de Sousse ce matin",
              "image_b64": img, "location": loc, "session": {}, "contact": {"name": "Amine (test)"}}, "finder-1")
    if f.get("kind") == "question":
        f = chat({"action": "message", "message": "Le fond d'écran montre un coucher de soleil sur la mer", "image_b64": img,
                  "session": f["session"]}, "finder-2-hidden-detail")
    # owner (Derja)
    desc = f"dha3li telifouni fi train mta3 Sousse lyoum, {phone['color_family']} {phone['item_name'][:60]}"
    o = chat({"action": "message", "message": desc, "session": {}, "location": loc}, "owner-1")
    for k in range(2):
        if o.get("kind") == "question":
            o = chat({"action": "message", "message": "ma na3rafch akther, fih coque", "session": o["session"]}, f"owner-{k + 2}")
    if o.get("kind") == "challenge":
        chat({"action": "claim_answer", "claim_id": o["claim_id"], "answer": "coucher de chams 3al b7ar", "session": o["session"]},
             "owner-claim-answer")
    chat({"action": "message", "message": "Que faire si j'ai perdu ma carte d'identité ?", "session": {}}, "procedure-fr")
    chat({"action": "message", "message": "dha3li el passeport, chnowa el wra9 elli lazmin?", "session": {}}, "procedure-derja")
    (config.ROOT / "logs" / "smoke_test.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=1), encoding="utf-8")
