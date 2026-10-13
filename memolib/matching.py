"""Word-matching search over a project's tree and its history: no model, milliseconds.

A file is found by its path and its mechanical summary, a directory by the model's summary of it, a note by its text.
Rarer words weigh more. Chinese is matched in two-character pieces, and camelCase and path separators split words.
"""
import math
import re

WORDS = re.compile(r"[A-Za-z][A-Za-z0-9]+|\d{2,}|[一-鿿]+")
CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def tokens(text):
    out = []
    for word in WORDS.findall(CAMEL.sub(r"\1 \2", text)):
        if "一" <= word[0] <= "鿿":
            out += [word[i:i + 2] for i in range(max(1, len(word) - 1))]
        else:
            out.append(word.lower())
    return out


def rank(documents, query, limit):
    """documents: [(key, text)] -> [(key, score, shared words)], best first."""
    wanted = set(tokens(query))
    if not wanted:
        return []
    prepared = [(key, set(tokens(text))) for key, text in documents]
    frequency = {}
    for _, words in prepared:
        for word in words:
            frequency[word] = frequency.get(word, 0) + 1
    scored = []
    for key, words in prepared:
        shared = wanted & words
        if shared:
            scored.append((key, sum(math.log(1 + len(prepared) / frequency[w]) for w in shared), sorted(shared)))
    return sorted(scored, key=lambda item: -item[1])[:limit]
