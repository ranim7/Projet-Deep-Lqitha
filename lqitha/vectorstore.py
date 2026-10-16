"""Minimal persistent vector store (NumPy, exact cosine search).

Why not ChromaDB? We started with ChromaDB 1.5.9, but its Rust bindings crash natively on Windows
("access violation" in Collection.add). At our scale (hundreds of chunks, a few thousand reports) exact
brute-force cosine search takes ~1 ms, needs no extra dependency and gives exact (not approximate) neighbours.

The query() result format mimics Chroma's, and `where` supports the same small filter language:
  {"field": value}, {"field": {"$in": [...]}}, {"field": {"$gte": x}}, {"field": {"$lte": x}}, {"$and": [...]}
"""
import json
import threading
from pathlib import Path

import numpy as np


def _match(meta, where):
    if not where:
        return True
    if "$and" in where:
        return all(_match(meta, w) for w in where["$and"])
    for k, cond in where.items():
        v = meta.get(k)
        if isinstance(cond, dict):
            for op, x in cond.items():
                if op == "$in" and v not in x:
                    return False
                if op == "$gte" and not (v is not None and v >= x):
                    return False
                if op == "$lte" and not (v is not None and v <= x):
                    return False
                if op == "$ne" and v == x:
                    return False
        elif v != cond:
            return False
    return True


class VectorStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.ids, self.docs, self.metas, self.vecs = [], [], [], None
        if self.path and (self.path / "meta.json").exists():
            d = json.loads((self.path / "meta.json").read_text(encoding="utf-8"))
            self.ids, self.docs, self.metas = d["ids"], d["documents"], d["metadatas"]
            self.vecs = np.load(self.path / "vectors.npy")

    # ------------------------------------------------------------------ write
    def _save(self):
        if not self.path:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        np.save(self.path / "vectors.npy", self.vecs if self.vecs is not None else np.zeros((0, 1)))
        (self.path / "meta.json").write_text(json.dumps({"ids": self.ids, "documents": self.docs, "metadatas": self.metas},
                                                        ensure_ascii=False), encoding="utf-8")

    def upsert(self, ids, embeddings, documents, metadatas):
        with self.lock:
            E = np.asarray(embeddings, dtype=np.float32)
            E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
            for i, emb, doc, meta in zip(ids, E, documents, metadatas):
                if i in self.ids:
                    k = self.ids.index(i)
                    self.vecs[k], self.docs[k], self.metas[k] = emb, doc, meta
                else:
                    self.ids.append(i)
                    self.docs.append(doc)
                    self.metas.append(meta)
                    self.vecs = emb[None, :] if self.vecs is None or len(self.vecs) == 0 else np.vstack([self.vecs, emb])
            self._save()

    add = upsert

    def update(self, ids, metadatas):
        with self.lock:
            for i, m in zip(ids, metadatas):
                if i in self.ids:
                    self.metas[self.ids.index(i)].update(m)
            self._save()

    def reset(self):
        with self.lock:
            self.ids, self.docs, self.metas, self.vecs = [], [], [], None
            self._save()

    def count(self):
        return len(self.ids)

    # ------------------------------------------------------------------ read
    def query(self, query_embeddings, n_results=10, where=None, include=None):
        out = {"ids": [], "documents": [], "metadatas": [], "distances": []}
        for q in query_embeddings:
            if self.vecs is None or not len(self.ids):
                for k in out:
                    out[k].append([])
                continue
            q = np.asarray(q, dtype=np.float32)
            q = q / (np.linalg.norm(q) + 1e-9)
            sims = self.vecs @ q
            idx = [i for i in np.argsort(-sims) if _match(self.metas[i], where)][:n_results]
            out["ids"].append([self.ids[i] for i in idx])
            out["documents"].append([self.docs[i] for i in idx])
            out["metadatas"].append([dict(self.metas[i]) for i in idx])
            out["distances"].append([float(1 - sims[i]) for i in idx])
        return out
