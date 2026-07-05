"""Hybrid retrieval for the chat assistant (v0.5.0 slice 3.2).

Two complementary scorers combined linearly:
  - BM25Okapi   — keyword / lexical ranking (good for technical terms)
  - TF-IDF cosine — semantic-ish overlap, document length normalization

Both are stdlib (math, collections, re) so the sweeper stays
deployable without ML deps. If/when the corpus grows beyond a few
hundred docs, swap HybridIndex for a langchain/pgvector-backed one —
the public API (HybridIndex.query / add_document / add_text) is
stable.

Hebrew-friendly tokenization:
  - Unicode word boundaries via regex (covers Hebrew letters, digits,
    ASCII letters)
  - Strip niqqud (Hebrew vowel points U+0591..U+05BD, U+05BF, U+05C1..U+05C5)
    so lexical matching is robust to vocalized text
  - Lowercase Latin, keep Hebrew casing (no uppercase form)

Public surface:
    HebrewTokenizer       -- normalize + tokenize
    BM25Okapi             -- classic BM25 ranker
    TfidfIndex            -- cosine TF-IDF ranker
    HybridIndex           -- combines both, returns top-k (doc_id, score, text)
    load_docs_from_dir    -- walk a directory and ingest *.md / *.txt
    load_docs_from_strings -- (doc_id, text) iterable loader
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ._log import log_suppressed

# Hebrew block: U+05D0..U+05EA (letters). Combined with general letters/digits.
_HEBREW_LETTERS = "\u05d0-\u05ea"
# Niqqud / cantillation marks to strip.
_NIQQUD_RANGES = re.compile(r"[\u0591-\u05bd\u05bf\u05c1-\u05c5\u05c7]+")
# Token boundary: any run of word characters (Unicode-aware via \w with re.UNICODE).
_TOKEN_RE = re.compile(rf"[{_HEBREW_LETTERS}\w]+", re.UNICODE)


def normalize_hebrew(text: str) -> str:
    """Strip niqqud + normalize Unicode (NFKC) so lexical matching is stable."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _NIQQUD_RANGES.sub("", text)
    return text


def tokenize(text: str) -> list[str]:
    """Tokenize for BM25/TF-IDF. Lowercase Latin, preserve Hebrew as-is."""
    text = normalize_hebrew(text)
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass
class _Doc:
    doc_id: str
    text: str
    tokens: list[str] = field(default_factory=list)
    length: int = 0
    tf: Counter = field(default_factory=Counter)


class HebrewTokenizer:
    """Stateless tokenizer with a tiny in-process LRU-ish cache.

    Not thread-safe across processes (no LRU eviction), but safe across
    threads within a single process because we never mutate state.
    """

    def __init__(self) -> None:
        self._cache: dict[str, list[str]] = {}

    def __call__(self, text: str) -> list[str]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        toks = tokenize(text)
        # Keep cache bounded to avoid unbounded memory growth on dynamic docs.
        if len(self._cache) < 4096:
            self._cache[text] = toks
        return toks


# --- BM25 -------------------------------------------------------------------

class BM25Okapi:
    """Classic BM25 ranker (Robertson, Walker, Beaulieu 1995).

    Tuned for short-to-medium technical docs (k1=1.5, b=0.75) which is the
    sweet spot for runbooks / FAQ entries. We re-index eagerly on every
    add/remove; the sweeper's doc corpus is small enough that the
    quadratic-ish rebuild is negligible.
    """

    def __init__(self, tokenizer: HebrewTokenizer | None = None,
                 k1: float = 1.5, b: float = 0.75) -> None:
        self.tokenizer = tokenizer or HebrewTokenizer()
        self.k1 = k1
        self.b = b
        self._docs: dict[str, _Doc] = {}
        self._df: Counter = Counter()
        self._avgdl: float = 0.0

    @property
    def doc_count(self) -> int:
        return len(self._docs)

    def add(self, doc_id: str, text: str) -> None:
        if doc_id in self._docs:
            self.remove(doc_id)
        tokens = self.tokenizer(text)
        tf = Counter(tokens)
        self._docs[doc_id] = _Doc(
            doc_id=doc_id, text=text, tokens=tokens,
            length=len(tokens), tf=tf,
        )
        for term in set(tokens):
            self._df[term] += 1
        self._recompute_avgdl()

    def remove(self, doc_id: str) -> bool:
        doc = self._docs.pop(doc_id, None)
        if doc is None:
            return False
        for term in set(doc.tokens):
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]
        self._recompute_avgdl()
        return True

    def _recompute_avgdl(self) -> None:
        if not self._docs:
            self._avgdl = 0.0
            return
        self._avgdl = sum(d.length for d in self._docs.values()) / len(self._docs)

    def score(self, query_tokens: Iterable[str],
              doc: _Doc) -> float:
        if doc.length == 0 or self._avgdl == 0:
            return 0.0
        score = 0.0
        for term in query_tokens:
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (len(self._docs) - df + 0.5) / (df + 0.5))
            f = doc.tf.get(term, 0)
            denom = f + self.k1 * (1 - self.b +
                                   self.b * doc.length / self._avgdl)
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    def rank(self, query: str,
             top_k: int = 5) -> list[tuple[str, float]]:
        qtoks = self.tokenizer(query)
        if not qtoks or not self._docs:
            return []
        scored = [(d.doc_id, self.score(qtoks, d))
                  for d in self._docs.values()]
        scored = [(d, s) for d, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# --- TF-IDF cosine ---------------------------------------------------------

class TfidfIndex:
    """Cosine-similarity TF-IDF ranker. Inverse-document-frequency weighting
    uses the standard smoothed log formula; document vectors are L2-normalized
    at index time so query-time scoring is just dot products.
    """

    def __init__(self, tokenizer: HebrewTokenizer | None = None) -> None:
        self.tokenizer = tokenizer or HebrewTokenizer()
        self._docs: dict[str, _Doc] = {}
        self._df: Counter = Counter()
        # doc_id -> normalized vector (sparse dict[term] -> weight).
        self._vectors: dict[str, dict[str, float]] = {}

    @property
    def doc_count(self) -> int:
        return len(self._docs)

    def add(self, doc_id: str, text: str) -> None:
        if doc_id in self._docs:
            self.remove(doc_id)
        tokens = self.tokenizer(text)
        tf = Counter(tokens)
        self._docs[doc_id] = _Doc(
            doc_id=doc_id, text=text, tokens=tokens,
            length=len(tokens), tf=tf,
        )
        for term in set(tokens):
            self._df[term] += 1
        self._rebuild_vectors()

    def remove(self, doc_id: str) -> bool:
        doc = self._docs.pop(doc_id, None)
        if doc is None:
            return False
        self._vectors.pop(doc_id, None)
        for term in set(doc.tokens):
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]
        self._rebuild_vectors()
        return True

    def _rebuild_vectors(self) -> None:
        n = len(self._docs)
        vectors: dict[str, dict[str, float]] = {}
        for doc_id, doc in self._docs.items():
            vec: dict[str, float] = {}
            for term, freq in doc.tf.items():
                df = self._df.get(term, 1)
                idf = math.log((1 + n) / (1 + df)) + 1.0  # smoothed
                vec[term] = freq * idf
            # L2 normalize.
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            vectors[doc_id] = {t: v / norm for t, v in vec.items()}
        self._vectors = vectors

    def rank(self, query: str,
             top_k: int = 5) -> list[tuple[str, float]]:
        qtoks = self.tokenizer(query)
        if not qtoks or not self._docs:
            return []
        q_tf = Counter(qtoks)
        n = len(self._docs)
        q_vec: dict[str, float] = {}
        for term, freq in q_tf.items():
            df = self._df.get(term, 1)
            idf = math.log((1 + n) / (1 + df)) + 1.0
            q_vec[term] = freq * idf
        q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
        for term in q_vec:
            q_vec[term] /= q_norm
        scored = []
        for doc_id, doc_vec in self._vectors.items():
            # sparse dot product over keys in q_vec
            s = 0.0
            for term, w in q_vec.items():
                dv = doc_vec.get(term)
                if dv is not None:
                    s += dv * w
            if s > 0:
                scored.append((doc_id, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# --- Hybrid ----------------------------------------------------------------

@dataclass
class RetrievalHit:
    doc_id: str
    score: float
    text: str
    bm25: float
    tfidf: float

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "score": self.score,
            "text": self.text,
            "bm25": self.bm25,
            "tfidf": self.tfidf,
        }


class HybridIndex:
    """Linear combination of BM25 and TF-IDF cosine. Both raw scores are
    min-max normalized per query before weighting, so neither dominates
    when their absolute scales differ.
    """

    def __init__(self,
                 bm25_weight: float = 0.5,
                 tfidf_weight: float = 0.5,
                 tokenizer: HebrewTokenizer | None = None) -> None:
        if bm25_weight < 0 or tfidf_weight < 0:
            raise ValueError("weights must be non-negative")
        s = bm25_weight + tfidf_weight
        if s == 0:
            raise ValueError("at least one weight must be positive")
        self.bm25_weight = bm25_weight / s
        self.tfidf_weight = tfidf_weight / s
        self.tokenizer = tokenizer or HebrewTokenizer()
        self._bm25 = BM25Okapi(self.tokenizer)
        self._tfidf = TfidfIndex(self.tokenizer)
        self._texts: dict[str, str] = {}

    @property
    def doc_count(self) -> int:
        return self._bm25.doc_count

    def add(self, doc_id: str, text: str) -> None:
        self._texts[doc_id] = text
        self._bm25.add(doc_id, text)
        self._tfidf.add(doc_id, text)

    def remove(self, doc_id: str) -> bool:
        self._texts.pop(doc_id, None)
        bm25_removed = self._bm25.remove(doc_id)
        tfidf_removed = self._tfidf.remove(doc_id)
        return bm25_removed and tfidf_removed

    def query(self, question: str,
              top_k: int = 3) -> list[RetrievalHit]:
        if not self._bm25.doc_count:
            return []
        bm25_results = self._bm25.rank(question, top_k=top_k * 2)
        tfidf_results = self._tfidf.rank(question, top_k=top_k * 2)

        def _norm(pairs: list[tuple[str, float]]) -> dict[str, float]:
            if not pairs:
                return {}
            mx = max(s for _, s in pairs)
            mn = min(s for _, s in pairs)
            if mx == mn:
                return {d: 1.0 for d, _ in pairs}
            return {d: (s - mn) / (mx - mn) for d, s in pairs}

        bm25_n = _norm(bm25_results)
        tfidf_n = _norm(tfidf_results)

        # Collect all candidates.
        candidates: dict[str, dict[str, float]] = defaultdict(
            lambda: {"bm25": 0.0, "tfidf": 0.0}
        )
        for d, s in bm25_results:
            candidates[d]["bm25"] = s
        for d, s in tfidf_results:
            candidates[d]["tfidf"] = s

        hits: list[RetrievalHit] = []
        for doc_id, parts in candidates.items():
            combined = (self.bm25_weight * bm25_n.get(doc_id, 0.0)
                        + self.tfidf_weight * tfidf_n.get(doc_id, 0.0))
            hits.append(RetrievalHit(
                doc_id=doc_id,
                score=combined,
                text=self._texts.get(doc_id, ""),
                bm25=parts["bm25"],
                tfidf=parts["tfidf"],
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


# --- Loaders ---------------------------------------------------------------

def load_docs_from_strings(pairs: Iterable[tuple[str, str]]) -> HybridIndex:
    """Ingest (doc_id, text) tuples."""
    idx = HybridIndex()
    for doc_id, text in pairs:
        if doc_id and text:
            idx.add(doc_id, text)
    return idx


def load_docs_from_dir(directory: str | Path,
                       glob: str = "*.md",
                       max_docs: int = 500,
                       max_chars: int = 20_000,
                       ) -> HybridIndex:
    """Walk a directory and ingest *.md / *.txt files (path as doc_id)."""
    p = Path(directory)
    if not p.is_dir():
        raise FileNotFoundError(f"directory not found: {directory}")
    idx = HybridIndex()
    count = 0
    for path in sorted(p.rglob(glob)):
        if count >= max_docs:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log_suppressed("chat_rag_read_doc", e)
            continue
        text = text[:max_chars]
        doc_id = str(path.relative_to(p))
        idx.add(doc_id, text)
        count += 1
    return idx


def format_hits_for_prompt(question: str,
                           hits: list[RetrievalHit],
                           max_chars: int = 1200) -> str:
    """Render retrieved docs as a Hebrew-friendly prompt block."""
    if not hits:
        return ""
    lines = [f"[RAG] שאלה: {question}", ""]
    used = sum(len(line) + 1 for line in lines)
    for i, hit in enumerate(hits, 1):
        block = f"--- מקור {i}: {hit.doc_id} (ציון {hit.score:.2f}) ---\n{hit.text}\n"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining <= 0:
                break
            block = block[:max(0, remaining - 3)] + "..."
        lines.append(block)
        used += len(block) + 1
    return "\n".join(lines)


__all__ = [
    "BM25Okapi",
    "HybridIndex",
    "HebrewTokenizer",
    "RetrievalHit",
    "TfidfIndex",
    "format_hits_for_prompt",
    "load_docs_from_dir",
    "load_docs_from_strings",
    "normalize_hebrew",
    "tokenize",
]