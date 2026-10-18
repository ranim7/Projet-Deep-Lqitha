"""Knowledge base: loading, two chunking strategies, indexing (ChromaDB) and retrieval.

Strategy A - "structure": split on the document's own structure (markdown headings of web
             pages, "Article N" / numbered titles of legal PDFs), then split long sections on
             paragraphs (max ~1200 chars). Each chunk is prefixed with "doc title > section".
Strategy B - "recursive": classic recursive character splitter, 1000 chars, 150 overlap,
             separators [blank line, newline, sentence, space]. Baseline.

Retrieval modes: "dense" (bge-m3 cosine) and "hybrid" (dense + BM25 fused with Reciprocal
Rank Fusion). Vectors live in a small exact-search NumPy store (lqitha/vectorstore.py). Optional metadata filter on item_type (+ always the "general" docs).
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

from . import config
from .embeddings import embed
from .vectorstore import VectorStore

DOCS_DIR = config.KB_DIR / "docs"
SOPS_DIR = config.KB_DIR / "sops"
_cols = {}


def collection(strategy):
    if strategy not in _cols:
        _cols[strategy] = VectorStore(config.STORE / "vectors" / f"kb_{strategy}")
    return _cols[strategy]


# ----------------------------------------------------------------------------- loading
def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    pages = [p.extract_text() or "" for p in PdfReader(str(path)).pages]
    txt = "\n".join(pages)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    # promote legal structure to markdown headings so strategy A can use it
    txt = re.sub(r"(?m)^\s*(Article\s+(premier|\d+)[^\n]{0,80})$", r"\n## \1", txt)
    txt = re.sub(r"(?m)^\s*((CHAPITRE|Chapitre|TITRE|Titre)\s+[IVXLC\d]+[^\n]{0,80})$", r"\n# \1", txt)
    txt = re.sub(r"(?m)^\s*(\d{1,2}\s*[.\-)]\s*[A-ZÉÈ][^\n]{3,80})$", r"\n## \1", txt)
    # short ALL-CAPS lines (e.g. "BAGAGES PERDUS, RETARDES OU ENDOMMAGES") are section titles too
    txt = re.sub(r"(?m)^\s*([A-ZÉÈÀÂÎÔÛÇ][A-ZÉÈÀÂÎÔÛÇ ,'’\-]{12,80})\s*$", r"\n## \1", txt)
    return txt.strip()


BOILERPLATE = ["Ces informations vous-ont elles été utiles", "Faites parvenir votre voix", "Articles similaires",
               "Laisser un commentaire", "Partager cet article", "À lire aussi", "Vous aimerez aussi"]


def _clean_web(text):
    """Cut site navigation / footer that follows the useful content of web pages."""
    cut = min([i for i in (text.find(m) for m in BOILERPLATE) if i > 0] or [len(text)])
    text = text[:cut]
    text = re.sub(r"(?m)^\s*-\s*(English|Français|العربية)\s*$", "", text)
    text = re.sub(r"(?m)^# SOURCE: .*$", "", text)
    return text.strip()


def build_documents():
    """Normalise raw sources + team SOPs into data/knowledge_base/docs/*.md with a meta header."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = config.RAW / "rag_corpus" / "manifest.json"
    docs = []
    if manifest_path.exists():
        for m in json.loads(manifest_path.read_text(encoding="utf-8")):
            src = config.RAW / "rag_corpus" / m["file"]
            text = _pdf_text(src) if src.suffix == ".pdf" else _clean_web(src.read_text(encoding="utf-8"))
            docs.append({**m, "text": text, "kind": "official"})
    for p in sorted(SOPS_DIR.glob("*.md")):
        head, _, body = p.read_text(encoding="utf-8").partition("\n---\n")
        meta = dict(re.findall(r"(?m)^(\w+):\s*(.+)$", head))
        docs.append({"id": p.stem, "file": p.name, "url": meta.get("source", "team-authored"),
                     "authority": meta.get("authority", "Lqitha team (SOP)"),
                     "item_type": meta.get("item_type", "general"), "lang": meta.get("lang", "fr"),
                     "text": body.strip(), "kind": "sop"})
    for d in docs:
        title = d["id"].replace("_", " ")
        (DOCS_DIR / f"{d['id']}.md").write_text(
            f"<!-- id={d['id']} item_type={d['item_type']} lang={d['lang']} url={d['url']} -->\n# {title}\n\n{d['text']}\n",
            encoding="utf-8")
    stats = [{"id": d["id"], "kind": d["kind"], "item_type": d["item_type"], "lang": d["lang"],
              "chars": len(d["text"]), "authority": d["authority"], "url": d["url"]} for d in docs]
    (config.KB_DIR / "corpus_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return docs


# ----------------------------------------------------------------------------- chunking
def _split_long(text: str, max_chars: int):
    """Split on paragraphs, then sentences, keeping pieces <= max_chars."""
    if len(text) <= max_chars:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n|\n(?=- )", text):
        units = [para] if len(para) <= max_chars else re.split(r"(?<=[.!?;:])\s+", para)
        for u in units:
            if len(cur) + len(u) + 1 > max_chars and cur:
                parts.append(cur.strip())
                cur = ""
            cur += u + "\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def chunk_structure(doc, max_chars=1200, min_chars=120):
    sections, heading, buf = [], doc["id"].replace("_", " "), []
    for line in doc["text"].splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if "\n".join(buf).strip():
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = m.group(2).strip()[:120], []
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        sections.append((heading, "\n".join(buf).strip()))
    # merge tiny sections into the next one (e.g. a heading followed by 1 line)
    merged, carry = [], ""
    for h, body in sections:
        body = (carry + "\n" + body).strip() if carry else body
        if len(body) < min_chars:
            carry = f"{h}: {body}"
            continue
        merged.append((h, body))
        carry = ""
    if carry:
        merged.append(("end", carry))
    chunks = []
    for h, body in merged:
        for piece in _split_long(body, max_chars):
            chunks.append(f"[{doc['id'].replace('_', ' ')} > {h}]\n{piece}")
    return chunks


def chunk_recursive(doc, size=1000, overlap=150, seps=("\n\n", "\n", ". ", " ")):
    text = doc["text"]

    def split(t, level):
        if len(t) <= size or level >= len(seps):
            return [t]
        out, cur = [], ""
        for piece in t.split(seps[level]):
            piece = piece + seps[level]
            if len(cur) + len(piece) > size and cur:
                out.append(cur)
                cur = ""
            if len(piece) > size:
                out.extend(split(piece, level + 1))
            else:
                cur += piece
        if cur:
            out.append(cur)
        return out
    raw = [p for p in split(text, 0) if p.strip()]
    chunks = []
    for i, p in enumerate(raw):
        prefix = raw[i - 1][-overlap:] if i > 0 and overlap else ""
        chunks.append((prefix + p).strip())
    return chunks


STRATEGIES = {"structure": chunk_structure, "recursive": chunk_recursive}


# ----------------------------------------------------------------------------- BM25
_TOKEN = re.compile(r"[\w؀-ۿ]+", re.U)
_STOP = set("le la les de des du un une et en au aux pour par sur dans est sont avec que qui à a l d ce cette il "
            "the of and to in a is for on with be by or an".split())


def tokenize(t):
    return [w for w in _TOKEN.findall(t.lower()) if w not in _STOP and len(w) > 1]


class BM25:
    def __init__(self, texts, k1=1.5, b=0.75):
        self.docs = [tokenize(t) for t in texts]
        self.avg = sum(map(len, self.docs)) / max(1, len(self.docs))
        self.df = Counter(w for d in self.docs for w in set(d))
        self.N, self.k1, self.b = len(self.docs), k1, b
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query):
        q = tokenize(query)
        out = []
        for i, tf in enumerate(self.tf):
            s, L = 0.0, len(self.docs[i])
            for w in q:
                if w in tf:
                    idf = math.log(1 + (self.N - self.df[w] + 0.5) / (self.df[w] + 0.5))
                    s += idf * tf[w] * (self.k1 + 1) / (tf[w] + self.k1 * (1 - self.b + self.b * L / self.avg))
            out.append(s)
        return out


# ----------------------------------------------------------------------------- index
def build_index(strategy: str, docs=None):
    docs = docs or build_documents()
    fn = STRATEGIES[strategy]
    ids, texts, metas = [], [], []
    for d in docs:
        for j, c in enumerate(fn(d)):
            ids.append(f"{d['id']}::{j}")
            texts.append(c)
            metas.append({"doc_id": d["id"], "item_type": d["item_type"], "lang": d["lang"],
                          "url": d["url"], "authority": d["authority"], "kind": d["kind"]})
    col = collection(strategy)
    col.reset()
    vecs = embed(texts)
    col.upsert(ids=ids, embeddings=vecs, documents=texts, metadatas=metas)
    (config.KB_DIR / f"chunks_{strategy}.json").write_text(
        json.dumps([{"id": a, "text": t, **m} for a, t, m in zip(ids, texts, metas)], ensure_ascii=False, indent=1),
        encoding="utf-8")
    lens = [len(t) for t in texts]
    return {"strategy": strategy, "n_chunks": len(texts), "avg_chars": round(sum(lens) / len(lens)),
            "min_chars": min(lens), "max_chars": max(lens)}


_bm25_cache = {}


def _bm25(strategy):
    if strategy not in _bm25_cache:
        chunks = json.loads((config.KB_DIR / f"chunks_{strategy}.json").read_text(encoding="utf-8"))
        _bm25_cache[strategy] = (chunks, BM25([c["text"] for c in chunks]))
    return _bm25_cache[strategy]


# item category (from the intake / perception agents) -> knowledge-base item_type tags
KB_TYPE_MAP = {
    "phone": ["phone"], "id_document": ["national_id", "passport", "driving_licence", "family_booklet"],
    "national_id": ["national_id"], "passport": ["passport"], "driving_licence": ["driving_licence"],
    "bank_card": ["bank_card"], "wallet": ["bank_card", "national_id", "driving_licence"],
    "luggage": ["baggage"], "bag": ["baggage"], "baggage": ["baggage"],
}


# source families of the knowledge base (used by the agentic RAG to route each sub-question)
SOURCE_FAMILIES = {
    "administration": ["idaraty_passport_duplicate_tn", "idaraty_passport_duplicate_abroad", "idaraty_family_booklet_duplicate",
                       "netherlands_emergency_document_tunisia"],
    "law": ["law_2024_22_national_id_card", "montreal_convention_1999_text_fr", "icao_montreal_convention_1999_fr"],
    "operator": ["tunisair_passenger_protection_2026", "guide_lost_bank_card_tunisia"],
    "sop": ["sop_airport_lost_item", "sop_found_item_what_to_do", "sop_lost_bank_card", "sop_lost_identity_documents",
            "sop_lost_phone", "sop_lqitha_service", "sop_public_transport_lost_item"],
}
DOC_SOURCE = {d: fam for fam, docs in SOURCE_FAMILIES.items() for d in docs}


def source_of(doc_id):
    return DOC_SOURCE.get(doc_id, "sop" if str(doc_id).startswith("sop_") else "administration")


def search(query, strategy="recursive", mode="dense", top_k=5, item_type=None, fetch_k=20, sources=None):
    col = collection(strategy)
    allowed_types = KB_TYPE_MAP.get(item_type or "", None)
    conds = []
    if allowed_types:
        conds.append({"item_type": {"$in": allowed_types + ["general"]}})
    if sources:
        docs = [d for s in sources for d in SOURCE_FAMILIES.get(s, [])]
        if docs:
            conds.append({"doc_id": {"$in": docs}})
    where = None if not conds else (conds[0] if len(conds) == 1 else {"$and": conds})
    if mode != "dense" and sources:
        mode = "dense"   # source routing is only used with dense retrieval (best configuration)
    qv = embed([query])[0]
    res = col.query(query_embeddings=[qv], n_results=fetch_k, where=where,
                    include=["documents", "metadatas", "distances"])
    dense = [{"id": i, "text": d, **m, "dense": 1 - dist}
             for i, d, m, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])]
    if mode == "dense":
        ranked = dense
    else:
        chunks, bm = _bm25(strategy)
        sc = bm.scores(query)
        allowed = [k for k, c in enumerate(chunks) if not where or c["item_type"] in allowed_types + ["general"]]
        lex = sorted(allowed, key=lambda k: -sc[k])[:fetch_k]
        rrf = {}
        for r, c in enumerate(dense):
            rrf[c["id"]] = rrf.get(c["id"], 0) + 1 / (60 + r)
        for r, k in enumerate(lex):
            rrf[chunks[k]["id"]] = rrf.get(chunks[k]["id"], 0) + 1 / (60 + r)
        pool = {c["id"]: c for c in dense}
        for k in lex:
            pool.setdefault(chunks[k]["id"], {**chunks[k]})
        ranked = sorted(pool.values(), key=lambda c: -rrf[c["id"]])
        for c in ranked:
            c["rrf"] = round(rrf[c["id"]], 5)
    return ranked[:top_k]


def format_context(hits):
    return "\n\n".join(f"[{i + 1}] ({h['authority']} - {h['url']})\n{h['text']}" for i, h in enumerate(hits))


# ----------------------------------------------------------------------------- agentic RAG (recovery plan)
KB_ITEM_TAGS = {"national_id", "passport", "family_booklet", "driving_licence", "bank_card", "baggage", "phone", "general"}


def multi_search(sub_questions, per_question=3, max_passages=15, strategy="recursive"):
    """Retrieval step of the agentic RAG: one dense search per sub-question, routed to its source families and
    item type (from the planner agent). If routing is too narrow (< 2 hits) the sub-question falls back to the whole
    knowledge base. Passages are de-duplicated and keep the list of sub-questions they were retrieved for."""
    passages, order = {}, []
    for qi, sq in enumerate(sub_questions[:5], start=1):
        q = (sq.get("q") or sq.get("query") or "").strip()
        if not q:
            continue
        tag = sq.get("item_type") if sq.get("item_type") in KB_ITEM_TAGS else None
        srcs = [s for s in (sq.get("sources") or []) if s in SOURCE_FAMILIES] or None
        hits = search(q, strategy, "dense", top_k=per_question, item_type=None if tag == "general" else tag, sources=srcs)
        routed = True
        if len(hits) < 2:
            hits, routed = search(q, strategy, "dense", top_k=per_question), False
        for h in hits:
            p = passages.get(h["id"])
            if p is None:
                p = {"id": h["id"], "doc_id": h["doc_id"], "source": source_of(h["doc_id"]), "authority": h["authority"],
                     "url": h["url"], "text": h["text"], "for": [], "score": 0.0, "routed": routed}
                passages[h["id"]] = p
                order.append(h["id"])
            p["for"].append(qi)
            p["score"] = round(max(p["score"], h.get("dense", 0)), 4)
    out = [passages[i] for i in order][:max_passages]
    for k, p in enumerate(out, start=1):
        p["pid"] = k
    return out


def candidates_text(passages, chars=320):
    """Compact view of the candidate passages for the re-ranking agent (token budget)."""
    return "\n".join(f"P{p['pid']} [{p['source']} | {p['doc_id']} | for Q{','.join(map(str, p['for']))}] "
                     f"{' '.join(p['text'].split())[:chars]}" for p in passages)
