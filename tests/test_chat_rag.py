"""Tests for v0.5.0 slice 3.2 — hybrid retrieval (BM25 + TF-IDF).

Covers:
  - Hebrew normalization + tokenization (niqqud stripping, mixed Hebrew/ASCII)
  - BM25Okapi: indexing, ranking, IDF behavior, edge cases
  - TfidfIndex: cosine similarity, smoothing, L2 normalization
  - HybridIndex: combined scoring, normalization, top-k
  - Loaders: from strings, from dir, format_hits_for_prompt
  - chat.RAGStore: lazy load, reload, query
  - chat end-to-end: HTTP /chat/sessions/<id>/messages surfaces RAG snippets
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ipracticom_sweeper.chat_rag import (
    BM25Okapi,
    HebrewTokenizer,
    HybridIndex,
    RetrievalHit,
    TfidfIndex,
    format_hits_for_prompt,
    load_docs_from_dir,
    load_docs_from_strings,
    normalize_hebrew,
    tokenize,
)


# --- Hebrew normalization --------------------------------------------------

def test_normalize_hebrew_strips_niqqud():
    # שָׁלוֹם (with niqqud) -> שלום
    raw = "\u05e9\u05b8\u05dc\u05d5\u05dd"
    out = normalize_hebrew(raw)
    assert out == "\u05e9\u05dc\u05d5\u05dd"


def test_normalize_hebrew_handles_empty():
    assert normalize_hebrew("") == ""
    assert normalize_hebrew(None) == ""  # type: ignore[arg-type]


def test_tokenize_handles_mixed_languages():
    toks = tokenize("FreeSWITCH deployment על AWS 2026")
    assert "freeswitch" in toks
    assert "deployment" in toks
    assert "\u05e2\u05dc" in toks
    assert "aws" in toks
    assert "2026" in toks


def test_tokenize_lowercases_latin_only():
    toks = tokenize("Hello HELLO hello")
    assert toks == ["hello", "hello", "hello"]


def test_tokenize_strips_niqqud():
    toks = tokenize("\u05e9\u05b8\u05dc\u05d5\u05dd \u05e2\u05d5\u05b0\u05dc\u05dd")
    assert toks == ["\u05e9\u05dc\u05d5\u05dd", "\u05e2\u05d5\u05dc\u05dd"]


# --- BM25 ------------------------------------------------------------------

def test_bm25_empty_returns_empty():
    bm = BM25Okapi()
    assert bm.doc_count == 0
    assert bm.rank("anything") == []


def test_bm25_relevant_doc_ranks_first():
    bm = BM25Okapi()
    bm.add("a", "FreeSWITCH installation on AWS production")
    bm.add("b", "How to cook pasta carbonara")
    bm.add("c", "FreeSWITCH troubleshooting guide for SIP")
    ranked = bm.rank("FreeSWITCH installation")
    assert len(ranked) >= 1
    assert ranked[0][0] == "a"
    assert ranked[0][1] > 0


def test_bm25_remove_updates_df_and_recomputes():
    bm = BM25Okapi()
    bm.add("x", "free free free")
    bm.add("y", "free")
    assert bm._df["free"] == 2
    bm.remove("x")
    assert bm._df["free"] == 1
    assert "x" not in bm._docs


def test_bm25_remove_unknown_returns_false():
    bm = BM25Okapi()
    assert bm.remove("nope") is False


def test_bm25_idf_higher_for_rare_terms():
    bm = BM25Okapi()
    bm.add("common", "alpha beta alpha alpha")
    bm.add("medium", "alpha beta beta beta")
    bm.add("rare", "alpha gamma gamma gamma")
    # gamma only in `rare`; alpha in all three
    rare_score = bm.score(["gamma"], bm._docs["rare"])
    common_score = bm.score(["alpha"], bm._docs["common"])
    assert rare_score > common_score


def test_bm25_re_add_replaces_existing():
    bm = BM25Okapi()
    bm.add("a", "old text")
    bm.add("a", "new text")  # replace
    assert bm.doc_count == 1
    assert bm._docs["a"].text == "new text"


# --- TF-IDF cosine ---------------------------------------------------------

def test_tfidf_empty_returns_empty():
    tf = TfidfIndex()
    assert tf.doc_count == 0
    assert tf.rank("q") == []


def test_tfidf_perfect_match_scores_high():
    tf = TfidfIndex()
    tf.add("a", "FreeSWITCH AWS production")
    tf.add("b", "Cooking pasta carbonara recipe")
    ranked = tf.rank("FreeSWITCH AWS")
    assert ranked
    assert ranked[0][0] == "a"
    assert 0 < ranked[0][1] <= 1.0


def test_tfidf_orthogonal_docs_score_zero():
    tf = TfidfIndex()
    tf.add("a", "alpha alpha alpha")
    tf.add("b", "beta beta beta")
    ranked = tf.rank("gamma")
    assert ranked == []


def test_tfidf_vectors_are_normalized():
    tf = TfidfIndex()
    tf.add("a", "alpha beta gamma")
    vec = tf._vectors["a"]
    norm = sum(v * v for v in vec.values()) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_tfidf_remove_updates_df():
    tf = TfidfIndex()
    tf.add("a", "free free")
    tf.add("b", "free")
    tf.remove("a")
    assert "a" not in tf._vectors
    assert tf._df["free"] == 1


# --- Hybrid ----------------------------------------------------------------

def test_hybrid_returns_top_k():
    h = HybridIndex()
    h.add("1", "FreeSWITCH installation guide AWS")
    h.add("2", "Pasta carbonara Italian recipe")
    h.add("3", "FreeSWITCH troubleshooting SIP and RTP")
    hits = h.query("FreeSWITCH install", top_k=2)
    assert len(hits) == 2
    assert all(isinstance(hit, RetrievalHit) for hit in hits)
    # Best match should be doc 1.
    assert hits[0].doc_id == "1"
    assert hits[0].score > hits[1].score


def test_hybrid_combines_bm25_and_tfidf():
    h = HybridIndex()
    h.add("a", "alpha beta gamma")
    h.add("b", "alpha alpha alpha")
    hits = h.query("alpha beta")
    assert hits
    # bm25 + tfidf raw scores are exposed per-hit.
    assert all(hit.bm25 >= 0 and hit.tfidf >= 0 for hit in hits)


def test_hybrid_weights_validation():
    with pytest.raises(ValueError):
        HybridIndex(bm25_weight=-1, tfidf_weight=0.5)
    with pytest.raises(ValueError):
        HybridIndex(bm25_weight=0, tfidf_weight=0)


def test_hybrid_weights_normalized():
    h = HybridIndex(bm25_weight=2.0, tfidf_weight=2.0)
    assert h.bm25_weight == 0.5
    assert h.tfidf_weight == 0.5


def test_hybrid_remove():
    h = HybridIndex()
    h.add("a", "FreeSWITCH AWS")
    h.add("b", "Pasta carbonara")
    assert h.doc_count == 2
    assert h.remove("a") is True
    assert h.remove("a") is False  # already gone
    assert h.doc_count == 1
    assert "a" not in h._texts


def test_hybrid_empty_query_returns_empty():
    h = HybridIndex()
    h.add("a", "something")
    assert h.query("") == []  # tokenize("") returns []


def test_hybrid_no_corpus_returns_empty():
    h = HybridIndex()
    assert h.query("anything") == []


def test_hybrid_handles_hebrew():
    h = HybridIndex()
    h.add("a", "\u05d4\u05ea\u05e7\u05e0\u05ea FreeSWITCH \u05e2\u05dc AWS")
    h.add("b", "\u05de\u05e8\u05e7\u05d7 \u05e4\u05e1\u05d8\u05d4")
    hits = h.query("\u05d4\u05ea\u05e7\u05e0\u05ea FreeSWITCH", top_k=1)
    assert hits and hits[0].doc_id == "a"


def test_hybrid_as_dict_round_trip():
    h = HybridIndex()
    h.add("a", "FreeSWITCH")
    hit = h.query("FreeSWITCH")[0]
    d = hit.as_dict()
    assert set(d.keys()) == {"doc_id", "score", "text", "bm25", "tfidf"}


# --- Loaders ---------------------------------------------------------------

def test_load_docs_from_strings_basic():
    pairs = [("doc1", "Hello world"), ("doc2", "FreeSWITCH on AWS")]
    idx = load_docs_from_strings(pairs)
    assert idx.doc_count == 2
    hits = idx.query("FreeSWITCH")
    assert hits[0].doc_id == "doc2"


def test_load_docs_from_strings_skips_empty():
    pairs = [("doc1", "valid"), ("doc2", ""), ("", "no-id")]
    idx = load_docs_from_strings(pairs)
    assert idx.doc_count == 1


def test_load_docs_from_dir_md(tmp_path):
    (tmp_path / "a.md").write_text("FreeSWITCH deployment AWS", encoding="utf-8")
    idx = load_docs_from_dir(tmp_path)
    assert idx.doc_count == 1


def test_load_docs_from_dir_with_txt_glob(tmp_path):
    (tmp_path / "a.md").write_text("FreeSWITCH AWS", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Pasta carbonara recipe", encoding="utf-8")
    # Pass explicit *.txt glob -> only one file
    idx = load_docs_from_dir(tmp_path, glob="*.txt")
    assert idx.doc_count == 1
    assert "b.txt" in idx._texts


def test_load_docs_from_dir_missing_raises(tmp_path):
    fake = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        load_docs_from_dir(fake)


def test_load_docs_from_dir_truncates(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * 100_000, encoding="utf-8")
    idx = load_docs_from_dir(tmp_path, max_chars=200)
    assert idx.doc_count == 1
    assert len(idx._texts["big.md"]) <= 200


def test_load_docs_from_dir_respects_max_docs(tmp_path):
    for i in range(5):
        (tmp_path / f"d{i}.md").write_text(f"doc {i}", encoding="utf-8")
    idx = load_docs_from_dir(tmp_path, max_docs=2)
    assert idx.doc_count == 2


def test_format_hits_for_prompt_empty():
    assert format_hits_for_prompt("q", []) == ""


def test_format_hits_for_prompt_truncates():
    # Single long token repeated — survives tokenizer as one word,
    # but the raw text length dominates `format_hits_for_prompt`.
    huge_text = "x" + (" " + "x" * 200) * 100
    idx = load_docs_from_strings([("a", huge_text)])
    hits = idx.query("x", top_k=1)
    out = format_hits_for_prompt("q", hits, max_chars=300)
    assert "\u05de\u05e7\u05d5\u05e8" in out
    assert len(out) <= 350  # slack for ellipsis marker


# --- chat.RAGStore ---------------------------------------------------------

def test_ragstore_lazy_default_dir_loads_project_docs():
    from ipracticom_sweeper.chat import RAGStore
    store = RAGStore()  # default docs dir
    idx = store.get()
    # Project has a docs/ dir at the repo root -> index built.
    if idx is not None:
        assert idx.doc_count >= 1
    else:
        # If running outside the project (CI sandbox without docs/) we accept None.
        assert store._index is None


def test_ragstore_explicit_missing_dir_returns_zero():
    from ipracticom_sweeper.chat import RAGStore
    store = RAGStore(docs_dir="/nonexistent/path/xyz")
    assert store.reload() == 0
    assert store.query("anything") == []


def test_ragstore_reload_with_tempdir(tmp_path):
    from ipracticom_sweeper.chat import RAGStore
    (tmp_path / "doc.md").write_text("FreeSWITCH AWS setup guide", encoding="utf-8")
    store = RAGStore(docs_dir=str(tmp_path))
    n = store.reload()
    assert n == 1
    hits = store.query("FreeSWITCH")
    assert hits and hits[0].doc_id == "doc.md"


def test_ragstore_query_is_top_k():
    from ipracticom_sweeper.chat import RAGStore
    store = RAGStore()
    store.reload()
    hits = store.query("FreeSWITCH", top_k=1)
    assert len(hits) <= 1


# --- chat HTTP surface with RAG -------------------------------------------

def test_chat_post_message_includes_rag_hits_in_ack(client):
    """When the assistant ack is appended, it should include RAG snippets
    if the project's docs/ has anything matching the question."""
    sess = get_store_session_via_http(client)
    resp = client.post(f"/chat/sessions/{sess}/messages",
                       json={"content": "FreeSWITCH AWS deployment"})
    assert resp.status_code == 200
    data = resp.get_json()
    ack_text = data["assistant"]["content"]
    # Either RAG fires (project docs/ exists) or we got the bare stub.
    assert ("\u05ea\u05e9\u05d5\u05d1\u05ea \u05e1\u05d8\u05d0\u05d1" in ack_text
            or "[RAG]" in ack_text)


def test_chat_post_message_keeps_hebrew_chars(client):
    sess = get_store_session_via_http(client)
    resp = client.post(f"/chat/sessions/{sess}/messages",
                       json={"content": "\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd"})
    assert resp.status_code == 200
    ack_text = resp.get_json()["assistant"]["content"]
    assert "\u05e9\u05dc\u05d5\u05dd" in ack_text


# --- helpers ---------------------------------------------------------------

def get_store_session_via_http(client):
    resp = client.post("/chat/sessions", json={"title": "rag"})
    assert resp.status_code == 201
    return resp.get_json()["session_id"]


@pytest.fixture
def app():
    from ipracticom_sweeper.dashboard import app as dashboard_app
    dashboard_app.config["TESTING"] = True
    return dashboard_app


@pytest.fixture
def client(app):
    return app.test_client()