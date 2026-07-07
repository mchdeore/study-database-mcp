"""Persistence layer for the knowledge server.

Three pluggable pieces, all chosen by environment variables (.env):

1. Manifest      content-hash record per raw file -> the incremental gate.
2. Embedder      EMBEDDING_PROVIDER = openai | local | hash
3. Vector store  VECTOR_STORE       = numpy | lancedb | chroma

Defaults (VECTOR_STORE=numpy, EMBEDDING_PROVIDER=hash fallback) let the whole
pipeline run and be tested with zero network access or large downloads. Heavy
backends are imported lazily so importing this module is always cheap.

ponytail: the numpy store does an O(n) cosine scan per query. That's fine for a
personal corpus (a few thousand chunks). Upgrade path: set VECTOR_STORE=lancedb.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .chunk import Chunk

# --- Config & paths --------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load .env if python-dotenv is available; otherwise rely on os.environ."""
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass


_load_dotenv()


def data_dir() -> Path:
    d = os.environ.get("DATA_DIR")
    return Path(d) if d else _REPO_ROOT / "data"


def paths() -> Dict[str, Path]:
    d = data_dir()
    return {
        "data": d,
        "raw": d / "raw",
        "corpus": d / "corpus",
        "vector_store": d / "vector_store",
        "graph": d / "graph",
        "manifest": d / "manifest.json",
        "catalog": d / "catalog.db",
        "rename_log": d / "catalog_renames.json",
    }


def config() -> Dict[str, Any]:
    return {
        "embedding_provider": os.environ.get("EMBEDDING_PROVIDER", "hash").lower(),
        "embedding_model": os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        "vector_store": os.environ.get("VECTOR_STORE", "numpy").lower(),
        "pdf_converter": os.environ.get("PDF_CONVERTER", "pymupdf4llm").lower(),
        "chunk_target_tokens": int(os.environ.get("CHUNK_TARGET_TOKENS", "650")),
        "chunk_overlap_ratio": float(os.environ.get("CHUNK_OVERLAP_RATIO", "0.10")),
        "search_top_k": int(os.environ.get("SEARCH_TOP_K", "8")),
        "enable_graphrag": os.environ.get("ENABLE_GRAPHRAG", "false").lower() == "true",
        # The "backend librarian": server-side models that maintain the index
        # when GraphRAG is on. Split by role so the reliability premium is paid
        # only where it buys something (see docs/vision/10-cost.md). API keys
        # are NOT here -- they live in the encrypted credential store, looked up
        # by `api_key_name` (see servers/vault/credentials.py). Both endpoints
        # are OpenAI-compatible.
        "librarian": {
            # Agentic role: drives the MCP tools, dedup/regroup decisions, and
            # query-time context-packing. Reliability matters most -> Kimi K2
            # (leads MCP tool-use benchmarks). Low token volume, so the premium
            # is cheap in absolute terms.
            "agentic": {
                "model": os.environ.get("LLM_AGENTIC_MODEL", "kimi-k2.7-code"),
                "base_url": os.environ.get(
                    "LLM_AGENTIC_BASE_URL", "https://api.moonshot.ai/v1"
                ),
                "api_key_name": os.environ.get(
                    "LLM_AGENTIC_KEY_NAME", "moonshot_api_key"
                ),
            },
            # Extraction role: high-volume, mechanical per-chunk entity/relation
            # extraction -- the cost that scales with database size. Cost-first
            # -> DeepSeek V4 Flash (cheapest capable, tiny cache-hit price).
            "extraction": {
                "model": os.environ.get("LLM_EXTRACTION_MODEL", "deepseek-v4-flash"),
                "base_url": os.environ.get(
                    "LLM_EXTRACTION_BASE_URL", "https://api.deepseek.com/v1"
                ),
                "api_key_name": os.environ.get(
                    "LLM_EXTRACTION_KEY_NAME", "deepseek_api_key"
                ),
            },
        },
        # Catalog: the folder of school material to index, and which file types
        # count as "documents" (everything else -- code, data, venvs -- is skipped).
        "school_dir": os.environ.get(
            "SCHOOL_DIR", str(Path.home() / "Documents" / "SCHOOL")
        ),
        "catalog_doc_exts": os.environ.get(
            "CATALOG_DOC_EXTS", ".pdf,.docx,.pptx,.md,.markdown"
        ),
    }


# --- Manifest (incremental gate) -------------------------------------------
def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class Manifest:
    """file (relative to raw/) -> {hash, status, chunks, corpus}.

    `is_changed` is the incremental gate: unchanged files are skipped so only
    changed/new files are re-processed.
    """

    def __init__(self, path: Path):
        self.path = path
        self.entries: Dict[str, Dict[str, Any]] = {}
        if path.exists():
            try:
                self.entries = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                self.entries = {}

    def is_changed(self, key: str, digest: str) -> bool:
        entry = self.entries.get(key)
        return entry is None or entry.get("hash") != digest

    def update(self, key: str, digest: str, **extra: Any) -> None:
        self.entries[key] = {"hash": digest, **extra}

    def remove(self, key: str) -> None:
        self.entries.pop(key, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True))


# --- Embedders -------------------------------------------------------------
class _HashEmbedder:
    """Deterministic offline embedder (bag-of-hashed-tokens, L2-normalized).

    NOT semantic -- it only captures lexical overlap. Its purpose is to make the
    pipeline runnable and testable with no model download or API key. Use
    EMBEDDING_PROVIDER=local or openai for real retrieval quality.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: List[str]) -> np.ndarray:
        import re

        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                hsh = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[i, hsh % self.dim] += 1.0
            norm = np.linalg.norm(out[i])
            if norm:
                out[i] /= norm
        return out


class _LocalEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # lazy/heavy

        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: List[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )


class _OpenAIEmbedder:
    def __init__(self, model_name: str):
        from openai import OpenAI  # lazy

        self.client = OpenAI()
        self.model_name = model_name
        self.dim = 3072 if "large" in model_name else 1536

    def embed(self, texts: List[str]) -> np.ndarray:
        resp = self.client.embeddings.create(model=self.model_name, input=texts)
        vecs = np.asarray([d.embedding for d in resp.data], dtype=np.float32)
        # normalize for cosine via dot product
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


def get_embedder():
    cfg = config()
    provider = cfg["embedding_provider"]
    model = cfg["embedding_model"]
    if provider == "openai":
        return _OpenAIEmbedder(model)
    if provider == "local":
        return _LocalEmbedder(model)
    if provider == "hash":
        return _HashEmbedder()
    raise ValueError(f"unknown EMBEDDING_PROVIDER={provider!r} (openai|local|hash)")


# --- Vector stores ---------------------------------------------------------
@dataclass
class Hit:
    chunk_id: str
    source: str
    heading_path: str
    page: Optional[int]
    text: str
    score: float

    def citation(self) -> dict:
        return {
            "source": self.source,
            "heading_path": self.heading_path,
            "page": self.page,
            "chunk_id": self.chunk_id,
        }


class NumpyStore:
    """Single-file numpy vector store. O(n) cosine scan per query."""

    def __init__(self, root: Path):
        self.file = root / "numpy_store.pkl"
        self.records: List[dict] = []
        self.vectors: Optional[np.ndarray] = None
        if self.file.exists():
            data = pickle.loads(self.file.read_bytes())
            self.records = data["records"]
            self.vectors = data["vectors"]

    def delete_source(self, source: str) -> None:
        keep = [i for i, r in enumerate(self.records) if r["source"] != source]
        self.records = [self.records[i] for i in keep]
        self.vectors = self.vectors[keep] if self.vectors is not None and keep else (
            None if not keep else self.vectors
        )
        if not self.records:
            self.vectors = None

    def upsert(self, chunks: List[Chunk], vectors: np.ndarray) -> None:
        # caller deletes the source first, so this is pure append
        new_records = [
            {
                "chunk_id": c.chunk_id,
                "source": c.source,
                "heading_path": c.heading_path,
                "page": c.page,
                "text": c.text,
                "headings": c.headings,
            }
            for c in chunks
        ]
        self.records.extend(new_records)
        vectors = np.asarray(vectors, dtype=np.float32)
        self.vectors = vectors if self.vectors is None else np.vstack([self.vectors, vectors])

    def query(self, vector: np.ndarray, k: int) -> List[Hit]:
        if self.vectors is None or not self.records:
            return []
        sims = self.vectors @ np.asarray(vector, dtype=np.float32).ravel()
        idx = np.argsort(-sims)[:k]
        hits = []
        for i in idx:
            r = self.records[int(i)]
            hits.append(Hit(r["chunk_id"], r["source"], r["heading_path"], r["page"],
                            r["text"], float(sims[int(i)])))
        return hits

    def all_records(self) -> List[dict]:
        return list(self.records)

    def sources(self) -> List[str]:
        return sorted({r["source"] for r in self.records})

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(pickle.dumps({"records": self.records, "vectors": self.vectors}))


class LanceStore:
    """LanceDB-backed store (scales better). Install the store-lancedb extra."""

    def __init__(self, root: Path):
        import lancedb  # lazy/heavy

        self.db = lancedb.connect(str(root / "lancedb"))
        self.table_name = "chunks"

    def _table(self):
        return self.db.open_table(self.table_name) if self.table_name in self.db.table_names() else None

    def delete_source(self, source: str) -> None:
        t = self._table()
        if t is not None:
            t.delete(f"source = '{source}'")

    def upsert(self, chunks: List[Chunk], vectors: np.ndarray) -> None:
        rows = [
            {
                "vector": vectors[i].tolist(),
                "chunk_id": c.chunk_id,
                "source": c.source,
                "heading_path": c.heading_path,
                "page": c.page if c.page is not None else -1,
                "text": c.text,
            }
            for i, c in enumerate(chunks)
        ]
        if not rows:
            return
        t = self._table()
        if t is None:
            self.db.create_table(self.table_name, data=rows)
        else:
            t.add(rows)

    def query(self, vector: np.ndarray, k: int) -> List[Hit]:
        t = self._table()
        if t is None:
            return []
        res = t.search(np.asarray(vector).ravel().tolist()).limit(k).to_list()
        hits = []
        for r in res:
            page = r.get("page", -1)
            # lancedb returns _distance (lower is closer); convert to a score
            score = 1.0 - float(r.get("_distance", 0.0))
            hits.append(Hit(r["chunk_id"], r["source"], r["heading_path"],
                            None if page == -1 else page, r["text"], score))
        return hits

    def all_records(self) -> List[dict]:
        t = self._table()
        if t is None:
            return []
        # to_arrow().to_pylist() keeps this on pyarrow only (no pandas/pylance dep)
        return t.to_arrow().to_pylist()

    def sources(self) -> List[str]:
        return sorted({r["source"] for r in self.all_records()})

    def save(self) -> None:
        pass  # lancedb persists on write


class ChromaStore:
    """Chroma-backed store. Install the store-chroma extra."""

    def __init__(self, root: Path):
        import chromadb  # lazy/heavy

        self.client = chromadb.PersistentClient(path=str(root / "chroma"))
        self.col = self.client.get_or_create_collection("chunks", metadata={"hnsw:space": "cosine"})

    def delete_source(self, source: str) -> None:
        self.col.delete(where={"source": source})

    def upsert(self, chunks: List[Chunk], vectors: np.ndarray) -> None:
        if not chunks:
            return
        self.col.add(
            ids=[c.chunk_id for c in chunks],
            embeddings=[vectors[i].tolist() for i in range(len(chunks))],
            documents=[c.text for c in chunks],
            metadatas=[
                {"source": c.source, "heading_path": c.heading_path,
                 "page": c.page if c.page is not None else -1}
                for c in chunks
            ],
        )

    def query(self, vector: np.ndarray, k: int) -> List[Hit]:
        res = self.col.query(query_embeddings=[np.asarray(vector).ravel().tolist()], n_results=k)
        hits = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i, cid in enumerate(ids):
            m = metas[i]
            page = m.get("page", -1)
            hits.append(Hit(cid, m["source"], m["heading_path"],
                            None if page == -1 else page, docs[i], 1.0 - float(dists[i])))
        return hits

    def all_records(self) -> List[dict]:
        got = self.col.get(include=["metadatas", "documents"])
        out = []
        for i, cid in enumerate(got.get("ids", [])):
            m = got["metadatas"][i]
            out.append({"chunk_id": cid, "text": got["documents"][i], **m})
        return out

    def sources(self) -> List[str]:
        return sorted({r["source"] for r in self.all_records()})

    def save(self) -> None:
        pass


def get_store():
    cfg = config()
    root = paths()["vector_store"]
    root.mkdir(parents=True, exist_ok=True)
    # Tolerate common spellings so a user's .env never silently fails to load.
    backend = {"np": "numpy", "lance": "lancedb", "chromadb": "chroma"}.get(
        cfg["vector_store"], cfg["vector_store"]
    )
    if backend == "numpy":
        return NumpyStore(root)
    if backend == "lancedb":
        return LanceStore(root)
    if backend == "chroma":
        return ChromaStore(root)
    raise ValueError(f"unknown VECTOR_STORE={cfg['vector_store']!r} (numpy|lancedb|chroma)")
