"""Step 3 - Build the RAG knowledge base: normalise documents, chunk with BOTH strategies,
embed with bge-m3 (Ollama) and index in ChromaDB. Writes data/knowledge_base/kb_build_stats.json."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, rag  # noqa: E402

if __name__ == "__main__":
    t0 = time.time()
    docs = rag.build_documents()
    print(f"{len(docs)} documents ({sum(d['kind'] == 'official' for d in docs)} official, "
          f"{sum(d['kind'] == 'sop' for d in docs)} team SOPs), {sum(len(d['text']) for d in docs):,} characters")
    stats = {"documents": len(docs), "chars": sum(len(d["text"]) for d in docs), "strategies": []}
    for strat in ("structure", "recursive"):
        s = rag.build_index(strat, docs)
        print(s)
        stats["strategies"].append(s)
    stats["embed_model"] = config.EMBED_MODEL
    stats["seconds"] = round(time.time() - t0, 1)
    (config.KB_DIR / "kb_build_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"done in {stats['seconds']} s")
