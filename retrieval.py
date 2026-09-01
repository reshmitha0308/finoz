"""
retrieval.py
------------
This is the "R" in RAG (Retrieval Augmented Generation).

Given a question in plain English, it finds the most relevant documents
in data/docs/ and hands them back so an agent can read them and cite them.

We use TF-IDF, which is a classic and very simple scoring method:
  TF  (term frequency)          = how often a word appears in ONE document
  IDF (inverse document freq.)  = how RARE that word is across ALL documents

Multiply them and common words like "the" score near zero, while
distinctive words like "buyback" score high. We then compare the query
vector to each document vector using cosine similarity.

Written from scratch in ~60 lines so the project needs no extra library.
"""

import glob
import math
import os
import re
from collections import Counter

from config import DOCS_DIR

_CACHE = None


def _tokenize(text):
    """Lowercase the text and split it into plain word tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def load_documents():
    """Read every .md file in data/docs/ once and remember the result."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    documents = []
    for path in sorted(glob.glob(os.path.join(DOCS_DIR, "*.md"))):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        documents.append(
            {
                "filename": os.path.basename(path),
                "text": text,
                "tokens": Counter(_tokenize(text)),
            }
        )

    # Inverse document frequency for every word we have ever seen.
    total_docs = len(documents)
    doc_count = Counter()
    for doc in documents:
        for word in doc["tokens"]:
            doc_count[word] += 1

    idf = {
        word: math.log((total_docs + 1) / (count + 1)) + 1
        for word, count in doc_count.items()
    }

    for doc in documents:
        doc["vector"] = _to_vector(doc["tokens"], idf)

    _CACHE = {"documents": documents, "idf": idf}
    return _CACHE


def _to_vector(token_counts, idf):
    """Turn word counts into a normalised TF-IDF vector."""
    vector = {
        word: (1 + math.log(count)) * idf.get(word, 1.0)
        for word, count in token_counts.items()
    }
    length = math.sqrt(sum(v * v for v in vector.values())) or 1.0
    return {word: v / length for word, v in vector.items()}


def _cosine(a, b):
    """Similarity between two vectors: 0.0 = unrelated, 1.0 = identical."""
    smaller, larger = (a, b) if len(a) < len(b) else (b, a)
    return sum(value * larger.get(word, 0.0) for word, value in smaller.items())


def search(query, top_k=3):
    """
    Return the top_k most relevant documents for a plain-English query.

    Each result includes a snippet, which is what we show the user as the
    citation. Attribution has to be VISIBLE, not just present.
    """
    index = load_documents()
    query_vector = _to_vector(Counter(_tokenize(query)), index["idf"])

    scored = []
    for doc in index["documents"]:
        score = _cosine(query_vector, doc["vector"])
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    results = []
    for score, doc in scored[:top_k]:
        body = doc["text"].strip()
        results.append(
            {
                "filename": doc["filename"],
                "score": round(score, 3),
                "snippet": body[:700] + ("..." if len(body) > 700 else ""),
            }
        )
    return results
