"""Word-matching search over a project's tree and its history: no model, milliseconds.

A file is found by its path and its mechanical summary, a directory by the model's summary of it, a note by its text.
Rarer words weigh more. Chinese is matched in two-character pieces, and camelCase and path separators split words.
"""
from memolib.matching import rank, tokens


def search_tree(tree, query, files=8, folders=6):
    file_docs = [(rel, rel + " " + tree.cache.data[rel][3]) for rel in tree.files]
    ids = [d + "/" for d in tree.entries_of] + [i for i in tree.summaries.data if "#" in i]
    folder_docs = [(i, i + " " + tree.text_of(i)) for i in ids if _exists(tree, i)]
    return rank(file_docs, query, files), rank(folder_docs, query, folders)


def _exists(tree, node_id):
    try:
        tree.parse(node_id)
        return True
    except KeyError:
        return False


def search_notes(notes, query, limit=8):
    ranked = rank([(n, n[2]) for n in notes], query, limit * 3)
    best = sorted(ranked, key=lambda item: (-round(item[1], 1), -item[0][0]))[:limit]
    return sorted(best, key=lambda item: item[0][0])
