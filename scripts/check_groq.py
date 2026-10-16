"""Checks the free Groq key: lists available models and tests text-JSON, vision and speech-to-text."""
import base64
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config  # noqa: E402

H = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
out = {}
models = requests.get(f"{config.GROQ_BASE}/models", headers=H, timeout=30).json()
ids = sorted(m["id"] for m in models.get("data", []))
out["models"] = ids
print("available models:", ids)
for role, m in [("TEXT_MODEL", config.TEXT_MODEL), ("VISION_MODEL", config.VISION_MODEL), ("STT_MODEL", config.STT_MODEL),
                ("DATA_GEN_MODEL", config.DATA_GEN_MODEL)]:
    out[role] = {"model": m, "available": m in ids}
    print(f"{role:15s} {m:50s} {'OK' if m in ids else 'NOT AVAILABLE'}")
r = requests.post(f"{config.GROQ_BASE}/chat/completions", headers=H, timeout=60, json={
    "model": config.TEXT_MODEL, "response_format": {"type": "json_object"},
    "messages": [{"role": "user", "content": 'Return {"ok": true} as JSON'}]})
out["text_test"] = {"status": r.status_code, "body": r.text[:300], "ratelimit": {k: v for k, v in r.headers.items() if "ratelimit" in k}}
print("text test:", r.status_code, r.text[:120])
img = next((config.RAW / "abo" / "images").glob("*.jpg"), None)
if img:
    b64 = base64.b64encode(img.read_bytes()).decode()
    r = requests.post(f"{config.GROQ_BASE}/chat/completions", headers=H, timeout=60, json={
        "model": config.VISION_MODEL, "max_tokens": 80,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "What object is this? 10 words."},
                                                  {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})
    out["vision_test"] = {"status": r.status_code, "body": r.text[:300], "ratelimit": {k: v for k, v in r.headers.items() if "ratelimit" in k}}
    print("vision test:", r.status_code, r.text[:160])
(config.ROOT / "logs").mkdir(exist_ok=True)
(config.ROOT / "logs" / "groq_check.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
