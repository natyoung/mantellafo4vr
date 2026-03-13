"""Relevance scoring for memory retrieval.

Scores memories against a query using BM25-style term frequency matching.
No external dependencies — uses only Python stdlib.
"""
import math
import re
from collections import Counter


# Common English stop words to ignore in scoring
_STOP_WORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'was', 'are', 'were', 'be', 'been',
    'has', 'had', 'have', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'it', 'its', 'i', 'me', 'my',
    'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them', 'their', 'this',
    'that', 'these', 'those', 'what', 'which', 'who', 'whom', 'how', 'when',
    'where', 'why', 'not', 'no', 'so', 'if', 'then', 'than', 'too', 'very',
    'just', 'about', 'up', 'out', 'into', 'over', 'after', 'before', 'also',
    'as', 'some', 'any', 'all', 'each', 'every', 'both', 'few', 'more',
    'most', 'other', 'such', 'only', 'own', 'same', 'like', 'get', 'got',
    'said', 'say', 'told', 'asked', 'went', 'came', 'know', 'think',
    'player', 'remember', 'remembered', 'talked', 'conversation',
})

_WORD_RE = re.compile(r'[a-z]+')


def _tokenize(text: str) -> list[str]:
    """Extract lowercase words, filtering stop words."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS and len(w) > 2]


def score_memories(memories: list[dict], query: str, max_results: int = 10,
                   recent_guaranteed: int = 0) -> list[dict]:
    """Score and select the most relevant memories for a given query.

    Args:
        memories: List of memory dicts, each with a "content" key. Assumed chronological order.
        query: Context string to score against (location, quest names, topics, etc.)
        max_results: Maximum memories to return.
        recent_guaranteed: Number of most recent memories to always include regardless of score.

    Returns:
        Selected memories in their original chronological order.
    """
    if not memories:
        return []

    if len(memories) <= max_results:
        return list(memories)

    query_terms = _tokenize(query)

    # If no query terms, return the most recent memories
    if not query_terms:
        return list(memories[-max_results:])

    # Build IDF from the memory corpus
    n_docs = len(memories)
    doc_tokens = [_tokenize(m["content"]) for m in memories]
    doc_freq: Counter = Counter()
    for tokens in doc_tokens:
        doc_freq.update(set(tokens))

    # Score each memory using BM25-like formula
    query_counts = Counter(query_terms)
    k1 = 1.5
    b = 0.75
    avg_dl = sum(len(t) for t in doc_tokens) / n_docs if n_docs > 0 else 1

    scores: list[float] = []
    for tokens in doc_tokens:
        if not tokens:
            scores.append(0.0)
            continue
        tf_counts = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        for term, qf in query_counts.items():
            if term not in tf_counts:
                continue
            tf = tf_counts[term]
            df = doc_freq.get(term, 0)
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
            tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
            score += idf * tf_norm * qf
        scores.append(score)

    # Guarantee recent memories
    guaranteed_indices = set()
    if recent_guaranteed > 0:
        for i in range(max(0, len(memories) - recent_guaranteed), len(memories)):
            guaranteed_indices.add(i)

    # Select top scoring indices (excluding guaranteed ones from the competition)
    remaining_slots = max_results - len(guaranteed_indices)
    if remaining_slots > 0:
        scored_indices = [(i, scores[i]) for i in range(len(memories)) if i not in guaranteed_indices]
        scored_indices.sort(key=lambda x: x[1], reverse=True)
        selected_indices = guaranteed_indices | {i for i, _ in scored_indices[:remaining_slots]}
    else:
        selected_indices = guaranteed_indices

    # Return in original chronological order
    return [memories[i] for i in sorted(selected_indices)]
