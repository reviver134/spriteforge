"""In-memory BM25: no embeddings, model calls, or persistent search database."""

import math
import re
from collections import Counter

from .models import Entry


def terms(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def rank(query: str, entries: list[Entry]) -> list[tuple[Entry, float]]:
    documents = [
        Counter(terms(" ".join([e.title] * 3 + e.metadata.tags * 2 + [e.body]))) for e in entries
    ]
    lengths = [sum(d.values()) for d in documents]
    average = sum(lengths) / max(len(entries), 1) or 1
    scores = [0.0] * len(entries)
    for term in set(terms(query)):
        df = sum(term in document for document in documents)
        idf = math.log(1 + (len(entries) - df + 0.5) / (df + 0.5))
        for i, document in enumerate(documents):
            freq = document[term]
            scores[i] += idf * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * lengths[i] / average))
    return sorted(
        [(entry, score) for entry, score in zip(entries, scores, strict=True) if score > 0],
        key=lambda row: (-row[1], row[0].id),
    )


def token_cost(text: str) -> int:
    # Conservative tokenizer-independent ceiling: at most one token per UTF-8 byte.
    # Budgets are strict for this accounting, rather than pretending to know a model tokenizer.
    return len(text.encode("utf-8"))


def sentence_excerpt(text: str, allowance: int) -> str:
    """Keep complete sentences; reserve an explicit marker when anything is omitted."""
    if token_cost(text) <= allowance:
        return text
    marker = "..."
    if allowance < token_cost(marker):
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = []
    for part in parts:
        candidate = "\n".join([*kept, part, marker])
        if token_cost(candidate) > allowance:
            break
        kept.append(part)
    return "\n".join([*kept, marker])
