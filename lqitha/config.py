"""Central configuration (reads .env). All models/APIs are free:
- Groq free tier: gpt-oss-120b / gpt-oss-20b (text), Qwen3.8-27B (vision), Whisper large-v3 (speech)
- Ollama local (bge-m3 multilingual embeddings)
- local NumPy vector store (lqitha/vectorstore.py) - ChromaDB crashed natively on Windows
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE = "https://api.groq.com/openai/v1"
TEXT_MODEL = os.getenv("TEXT_MODEL", "openai/gpt-oss-120b")       # verifier, notifier (reasoning)
FAST_MODEL = os.getenv("FAST_MODEL", "openai/gpt-oss-20b")         # intake, procedure, claim check
VISION_MODEL = os.getenv("VISION_MODEL", "qwen/qwen3.8-27b")       # perception (image + text)
DATA_GEN_MODEL = os.getenv("DATA_GEN_MODEL", "openai/gpt-oss-120b")  # synthetic owner messages (evaluation only)
MODEL_BY_ENV = {"TEXT_MODEL": TEXT_MODEL, "FAST_MODEL": FAST_MODEL, "VISION_MODEL": VISION_MODEL}
# local fallback (Ollama, OpenAI-compatible API) when the Groq free daily quota of a model is exhausted
LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "gemma3:4b")
LOCAL_TEXT_MODEL = os.getenv("LOCAL_TEXT_MODEL", "gemma3:4b")
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")

DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
KB_DIR = DATA / "knowledge_base"
STORE = ROOT / "store"
UPLOADS = STORE / "uploads"
RESULTS = ROOT / "reports" / "results"
PROMPTS_FILE = ROOT / "prompts" / "prompts.yaml"

for d in (PROCESSED, STORE, UPLOADS, RESULTS):
    d.mkdir(parents=True, exist_ok=True)

CATEGORIES = ["phone", "wallet", "bag", "luggage", "keys", "id_document", "bank_card", "glasses",
              "watch", "headphones", "laptop", "electronics", "clothing", "jewelry", "umbrella",
              "shoes", "bottle", "other"]
COLOR_FAMILIES = ["black", "white", "grey", "silver", "gold", "brown", "beige", "red", "pink", "orange",
                  "yellow", "green", "blue", "purple", "multicolor", "transparent"]
