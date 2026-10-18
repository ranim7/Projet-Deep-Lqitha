"""Live test of the two RAG pipelines through the real n8n workflow (web app -> /chat -> n8n -> agents).
Writes logs/smoke_rag.json. Usage: python scripts/smoke_rag.py"""
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
CASES = [
    ("plan-derja", "dhaya3li portefeuille fih carte d'identité w carte bancaire fi louage Sousse lbera7, chnowa na3mel?"),
    ("search-derja", "fama chkoun l9a sac à dos k7el fi Sousse?"),
    ("plan-fr-baggage", "Ma valise n'est pas arrivée à Tunis-Carthage après mon vol Tunisair d'hier, que faire ?"),
    ("search-en", "Has anyone found black sunglasses? I lost them in Tunis a few days ago"),
]

if __name__ == "__main__":
    only = sys.argv[1:] or [c[0] for c in CASES]
    out = []
    for name, msg in CASES:
        if name not in only:
            continue
        t0 = time.time()
        try:
            r = requests.post(f"{API}/chat", json={"action": "message", "message": msg, "session": {}}, timeout=300).json()
        except Exception as e:
            r = {"error": str(e)}
        r.pop("session", None)
        out.append({"case": name, "message": msg, "seconds": round(time.time() - t0, 1), "response": r})
        print(name, round(time.time() - t0, 1), "s", r.get("kind"), (r.get("reply") or r.get("error") or "")[:300].replace("\n", " "), flush=True)
        time.sleep(20)   # stay under the per-minute token limits
    (ROOT / "logs" / "smoke_rag.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
