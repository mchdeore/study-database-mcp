"""Lexical scoring for hybrid retrieval (build step 6.1).

A dependency-free, backend-agnostic **BM25** over chunk text — the lexical half of
hybrid search. It runs in Python over the same candidate rows the vector search
already loads, so it needs no FTS5/tsvector table and **no schema change** (the
rebuild contract is untouched). Fine at personal scale; a real FTS index is the
upgrade path if a vault ever gets huge (same ceiling as the brute-force vector
scan, D2).

BM25 is the ranking function behind most search engines (Elasticsearch/Solr): it
rewards query-term frequency in a chunk, damps it (saturation), and normalizes by
chunk length, weighting rarer terms higher via IDF.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List

# Tokenize to lowercase alphanumeric runs — good enough for matching words, codes,
# and identifiers (e.g. "E1042", "griffiths"). No stemming/stopwords (kept simple;
# IDF already down-weights ubiquitous words).
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


# BM25 score of `query` against each text in `texts` (parallel list of scores).
# A text with none of the query terms scores 0.0. k1 controls term-frequency
# saturation; b controls length normalization (the standard defaults).
def bm25_scores(query: str, texts: List[str], k1: float = 1.5, b: float = 0.75) -> List[float]:
    query_terms = set(tokenize(query))
    if not query_terms or not texts:
        return [0.0] * len(texts)

    doc_tokens = [tokenize(text) for text in texts]
    doc_len = [len(tokens) for tokens in doc_tokens]
    n_docs = len(doc_tokens)
    avg_len = (sum(doc_len) / n_docs) or 1.0
    term_freqs = [Counter(tokens) for tokens in doc_tokens]

    # IDF per query term (BM25's "probabilistic" idf, floored at 0 so a term in
    # every doc doesn't go negative).
    doc_freq = {term: sum(1 for tf in term_freqs if term in tf) for term in query_terms}
    idf = {
        term: max(0.0, math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)))
        for term, df in doc_freq.items()
    }

    scores: List[float] = []
    for index, tf in enumerate(term_freqs):
        length = doc_len[index] or 1
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if freq:
                denom = freq + k1 * (1.0 - b + b * length / avg_len)
                score += idf[term] * (freq * (k1 + 1.0)) / denom
        scores.append(score)
    return scores
