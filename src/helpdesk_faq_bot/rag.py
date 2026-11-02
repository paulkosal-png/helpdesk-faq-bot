"""Retrieval-augmented generation (RAG) fallback.

Used only when the deterministic matcher finds nothing.

1. Build a small corpus: every FAQ entry + every Markdown KB article (split by heading).
2. Embed it. Backends:
   * ``hashing``               - offline, zero-download bag-of-words hashing. Good enough for demos/CI.
   * ``sentence-transformers`` - local semantic model (``pip install -r requirements-rag.txt``).
   * ``openai``                - any OpenAI-compatible ``/embeddings`` endpoint.
3. Cosine-similarity search with numpy (the corpus is tiny; FAISS/Chroma would be overkill here).
4. Ask an OpenAI-compatible chat model to answer *only* from the retrieved chunks, with sources.

If no LLM is configured, or the LLM fails, or nothing relevant is retrieved, the caller escalates.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import requests

from .matcher import normalise
from .models import FAQEntry, Source

log = logging.getLogger(__name__)

NO_ANSWER = "NO_ANSWER"


@dataclass(frozen=True)
class Chunk:
    title: str
    location: str
    text: str


# --------------------------------------------------------------------------- corpus


def faq_chunks(entries: Sequence[FAQEntry]) -> list[Chunk]:
    chunks = []
    for e in entries:
        alts = "; ".join(e.alternate_phrases)
        text = f"Q: {e.question}\nAlso asked as: {alts}\nA: {e.answer}" if alts else f"Q: {e.question}\nA: {e.answer}"
        chunks.append(Chunk(title=e.question, location="FAQ", text=text))
    return chunks


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


def markdown_chunks(kb_dir: Path | str, max_chars: int = 1200) -> list[Chunk]:
    """Split each Markdown file into sections at H1-H3 headings."""
    kb_dir = Path(kb_dir)
    if not kb_dir.is_dir():
        return []
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        doc_title = next((m.group(2).strip() for m in _HEADING_RE.finditer(text)), path.stem)
        positions = [m.start() for m in _HEADING_RE.finditer(text)] or [0]
        if positions[0] != 0:
            positions.insert(0, 0)
        positions.append(len(text))
        for start, end in zip(positions, positions[1:], strict=False):
            section = text[start:end].strip()
            if not section:
                continue
            m = _HEADING_RE.match(section)
            heading = m.group(2).strip() if m else doc_title
            title = doc_title if heading == doc_title else f"{doc_title} - {heading}"
            for i in range(0, len(section), max_chars):
                chunks.append(
                    Chunk(title=title, location=f"{kb_dir.name}/{path.name}", text=section[i : i + max_chars])
                )
    return chunks


# --------------------------------------------------------------------------- embedders


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2_normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


_STOPWORDS = frozenset(
    "a an the is are am be to of in on for and or my me i it do does how can what why when where "
    "with at from this that you your we our not no yes please help need get".split()
)


class HashingEmbedder:
    """Offline bag-of-words + bigram feature hashing. Deterministic, no downloads, no GPU."""

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = [w for w in normalise(text).split() if w not in _STOPWORDS]
        # crude stemming so "printer"/"printing"/"prints" overlap
        stems = [re.sub(r"(ing|ers|er|es|s)$", "", w) if len(w) > 4 else w for w in words]
        return stems + [f"{a}_{b}" for a, b in zip(stems, stems[1:], strict=False)]

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                h = int.from_bytes(hashlib.md5(feat.encode()).digest()[:4], "little")
                out[row, h % self.dim] += 1.0
        return _l2_normalise(out)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model = SentenceTransformer(model_name)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self.model.encode(list(texts), normalize_embeddings=True), dtype=np.float32)


class OpenAICompatibleEmbedder:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.url = base_url.rstrip("/") + "/embeddings"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model
        self.timeout = timeout

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        resp = requests.post(
            self.url, headers=self.headers, json={"model": self.model, "input": list(texts)}, timeout=self.timeout
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return _l2_normalise(np.asarray([d["embedding"] for d in data], dtype=np.float32))


def build_embedder(settings) -> Embedder:
    backend = settings.embedding_backend
    if backend == "sentence-transformers":
        return SentenceTransformerEmbedder(settings.embedding_model)
    if backend == "openai":
        if not settings.embedding_base_url:
            raise ValueError("EMBEDDING_BACKEND=openai needs EMBEDDING_BASE_URL (or LLM_BASE_URL)")
        return OpenAICompatibleEmbedder(
            settings.embedding_base_url, settings.embedding_api_key, settings.embedding_model
        )
    return HashingEmbedder()


# --------------------------------------------------------------------------- vector index


class VectorIndex:
    """Brute-force cosine similarity. Fine for thousands of chunks; swap for FAISS/Chroma beyond that."""

    def __init__(self, embedder: Embedder, chunks: Sequence[Chunk]):
        self.embedder = embedder
        self.chunks = list(chunks)
        self.matrix = embedder.embed([c.text for c in self.chunks]) if self.chunks else np.zeros((0, 1))

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        q = self.embedder.embed([query])[0]
        scores = self.matrix @ q
        top = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in top]


# --------------------------------------------------------------------------- LLM


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleLLM:
    """Works with OpenAI, Azure OpenAI-compatible gateways, Ollama (/v1), vLLM, LM Studio, LiteLLM..."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={
                "model": self.model,
                "temperature": 0.1,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


SYSTEM_PROMPT = f"""You are an IT helpdesk assistant answering employees in Slack.
Answer ONLY using the numbered context below. Be brief (at most 5 short sentences or bullet points).
Never invent settings, links, phone numbers, or policies. Never ask for passwords or MFA codes.
If the context does not contain the answer, reply with exactly: {NO_ANSWER}"""


@dataclass
class RagResult:
    answer: str | None  # None means "could not answer - escalate"
    sources: list[Source]
    reason: str  # answered | no_llm | low_score | llm_declined | llm_error | empty_index


class RagAnswerer:
    def __init__(self, index: VectorIndex, llm: LLMClient | None, top_k: int = 4, min_score: float = 0.25):
        self.index = index
        self.llm = llm
        self.top_k = top_k
        self.min_score = min_score

    def answer(self, question: str) -> RagResult:
        if not self.index.chunks:
            return RagResult(None, [], "empty_index")
        hits = [(c, s) for c, s in self.index.search(question, self.top_k) if s >= self.min_score]
        sources = _dedupe_sources(hits)
        if not hits:
            return RagResult(None, [], "low_score")
        if self.llm is None:
            return RagResult(None, sources, "no_llm")
        context = "\n\n".join(f"[{i}] {c.title} ({c.location})\n{c.text}" for i, (c, _) in enumerate(hits, 1))
        try:
            reply = self.llm.complete(SYSTEM_PROMPT, f"Context:\n{context}\n\nQuestion: {question}")
        except Exception:  # noqa: BLE001 - any LLM failure means "escalate", never crash the bot
            log.exception("LLM call failed")
            return RagResult(None, sources, "llm_error")
        if not reply or NO_ANSWER in reply:
            return RagResult(None, sources, "llm_declined")
        return RagResult(reply, sources, "answered")


def _dedupe_sources(hits: Sequence[tuple[Chunk, float]]) -> list[Source]:
    seen: dict[tuple[str, str], Source] = {}
    for chunk, score in hits:
        key = (chunk.title, chunk.location)
        if key not in seen:
            seen[key] = Source(title=chunk.title, location=chunk.location, score=round(score, 3))
    return list(seen.values())
