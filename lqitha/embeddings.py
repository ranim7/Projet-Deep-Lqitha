"""Local, free embeddings with Ollama (model bge-m3: multilingual FR/AR/EN, 1024-d, 8k context)."""
import time

import numpy as np
import requests

from . import config


def embed(texts: list[str], batch: int = 16) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), batch):
        chunk = [t[:6000] for t in texts[i:i + batch]]
        for attempt in range(5):   # Ollama may answer 500 while it swaps models in GPU memory
            r = requests.post(f"{config.OLLAMA_URL}/api/embed",
                              json={"model": config.EMBED_MODEL, "input": chunk, "keep_alive": "30m"}, timeout=600)
            if r.ok:
                break
            time.sleep(5 * (attempt + 1))
        r.raise_for_status()
        out.extend(r.json()["embeddings"])
    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def cosine(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
