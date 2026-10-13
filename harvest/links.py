"""Pointers from a conversation to the two trees that already exist, found by matching, never by a model.

A memory that touches a paper carries `paper:<PaperTree id>`; one that touches a research node carries
`node:<id>`. `memo recall "paper:12"` then lists everything done about that paper, and the other trees
keep owning the papers' and the research's own content, so nothing is summarised twice.
"""
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter

PAPER_TREE_DB = os.environ.get("PAPER_TREE_DB", "D:/paper-tree/data/index.sqlite")
RESEARCH_ROOT = os.environ.get("RESEARCH_MAP_ROOT", os.path.expanduser("~/Projects/phd-research"))

ARXIV = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")
DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]},;]+", re.I)
NODES_JSON = re.compile(r"const nodes = (\[.*?\]);\n", re.S)


def norm(text):
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


class PaperLinks:
    """PaperTree's own identity table (title, DOI, arXiv-as-DOI), read-only."""

    def __init__(self, db=PAPER_TREE_DB):
        self.titles, self.keys, self.names = {}, {}, {}
        if not os.path.exists(db):
            return
        try:
            with sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True) as conn:
                self.keys = {key: pid for key, pid in conn.execute("select key, paper_id from identities")}
                rows = conn.execute("select id, title from papers where active=1").fetchall()
                self.names = {pid: title for pid, title in rows}
                self.titles = {norm(title): pid for pid, title in rows if len(title) >= 20}
        except sqlite3.Error:
            pass

    def find(self, text, limit=3):
        if not self.keys:
            return []
        counts = Counter()
        for match in ARXIV.finditer(text):
            pid = self.keys.get("doi:10.48550/arxiv." + match.group(1))
            if pid:
                counts[pid] += 2
        for match in DOI.finditer(text):
            pid = self.keys.get("doi:" + match.group(0).lower().rstrip(".)"))
            if pid:
                counts[pid] += 2
        folded = norm(text)
        for title, pid in self.titles.items():
            if title in folded:
                counts[pid] += folded.count(title)
        return [pid for pid, _ in counts.most_common(limit)]


class NodeLinks:
    """The research map's own node ids and titles, read from the built page."""

    def __init__(self, root=RESEARCH_ROOT):
        self.nodes = {}
        page = os.path.join(root, "docs", "RESEARCH-MAP.html")
        if not os.path.exists(page):
            return
        with open(page, encoding="utf-8") as handle:
            match = NODES_JSON.search(handle.read())
        if match:
            self.nodes = {n["id"]: n["title"] for n in json.loads(match.group(1)) if "resource" not in n}

    def find(self, text, limit=2):
        counts = Counter()
        for node_id, title in self.nodes.items():
            if len(title) >= 6:
                counts[node_id] += text.count(title)
            # a bare id like K3 means a node only when the text says so around it
            counts[node_id] += len(re.findall(r"(?:节点|研究节点|node|假设|问题)\s*[:：]?\s*%s\b" % re.escape(node_id), text, re.I))
        return [node for node, n in counts.most_common(limit) if n >= 2]


class Links:
    def __init__(self):
        self.papers, self.nodes = PaperLinks(), NodeLinks()

    def candidates(self, text):
        """[(token, label)] for what the text touches. The summarising model may copy a token into a
        memory it really concerns; it cannot invent one, because tokens are checked against this list."""
        found = [("paper:%s" % pid, self.papers.names.get(pid, "")) for pid in self.papers.find(text)]
        return found + [("node:%s" % node, self.nodes.nodes.get(node, "")) for node in self.nodes.find(text)]
