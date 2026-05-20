"""Per-restaurant knowledge base on ChromaDB."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import html
import re as _re

import chromadb
import httpx
from chromadb.utils import embedding_functions
from pypdf import PdfReader

from .config import EMBED_MODEL
from .db import restaurant_dir

log = logging.getLogger(__name__)

_clients: dict[str, chromadb.PersistentClient] = {}
_collections: dict[str, "chromadb.api.models.Collection.Collection"] = {}
_embed_fn = None


def _embed():
    global _embed_fn
    if _embed_fn is None:
        _embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL
        )
    return _embed_fn


def _collection(slug: str):
    if slug in _collections:
        return _collections[slug]
    rdir = restaurant_dir(slug)
    client = chromadb.PersistentClient(path=str(rdir / "chroma"))
    coll = client.get_or_create_collection(name="kb", embedding_function=_embed())
    _clients[slug] = client
    _collections[slug] = coll
    return coll


def _read_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            reader = PdfReader(str(path))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as exc:
            log.warning("PDF read failed %s: %s", path, exc)
            return ""
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def fetch_url_text(url: str, timeout: float = 15.0) -> str:
    """Fetch a web page and return readable text (strips scripts, tags, whitespace)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (VoiceAgent crawler)"
        }) as client:
            r = client.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                return ""
            body = r.text
    except Exception as exc:
        log.warning("URL fetch failed %s: %s", url, exc)
        return ""

    # strip script/style blocks, then all tags
    body = _re.sub(r"<script[\s\S]*?</script>", " ", body, flags=_re.IGNORECASE)
    body = _re.sub(r"<style[\s\S]*?</style>", " ", body, flags=_re.IGNORECASE)
    body = _re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = _re.sub(r"\s+", " ", body).strip()
    return body


def _chunk(text: str, size: int = 800, overlap: int = 120) -> Iterable[str]:
    text = " ".join(text.split())
    if not text:
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        yield text[start:end]
        if end == len(text):
            break
        start = end - overlap


def ingest_for(slug: str) -> int:
    """Rebuild the knowledge index for a restaurant from its knowledge folder."""
    rdir = restaurant_dir(slug)
    kdir = rdir / "knowledge"
    if slug in _clients:
        try:
            _clients[slug].delete_collection("kb")
        except Exception:
            pass
        _collections.pop(slug, None)

    coll = _collection(slug)
    ids, docs, metas = [], [], []
    for path in sorted(kdir.glob("**/*")):
        if not path.is_file():
            continue
        text = _read_file(path)
        if not text.strip():
            continue
        for i, chunk in enumerate(_chunk(text)):
            ids.append(f"{path.name}:{i}")
            docs.append(chunk)
            metas.append({"source": path.name})
    if not docs:
        log.warning("No documents to ingest for %s", slug)
        return 0
    coll.add(ids=ids, documents=docs, metadatas=metas)
    log.info("Ingested %d chunks for %s", len(docs), slug)
    return len(docs)


def retrieve(slug: str, query: str, k: int = 4) -> list[dict]:
    coll = _collection(slug)
    try:
        res = coll.query(query_texts=[query], n_results=k)
    except Exception as exc:
        log.exception("RAG query failed: %s", exc)
        return []
    out: list[dict] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    for doc, meta in zip(docs, metas):
        out.append({"text": doc, "source": (meta or {}).get("source", "")})
    return out


def format_context(chunks: list[dict]) -> str:
    if not chunks:
        return "No matching information found in the restaurant's knowledge base."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(f"[{i}] ({c['source']}) {c['text']}")
    return "\n\n".join(parts)
