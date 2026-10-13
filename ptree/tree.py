"""A project's files as a binary tree, in the style of PaperTree and OptMem.

Every directory is an interval over its entries (sub-directories first, then files, sorted); an interval of two
or more entries splits in half, down to single entries, which are files or other directories. Ids say where a node
is: `apps/web/` is a directory, `apps/web/#01` is the second half of its first half, `apps/web/main.ts` is a file,
`/` is the project root. A model writes summaries only for directories and for large intervals; a file's summary is
mechanical. A summary records what it was written from, so a change shows up as stale without anyone remembering
to invalidate it.
"""
import hashlib
import json
import os
import re
import tempfile
import time

from . import files as F

AI_DIR_MIN = 3        # a directory with fewer entries is summarised mechanically
AI_INTERVAL_MIN = 16  # an interval of at most this many entries is summarised mechanically...
AI_FILES_MIN = 40     # ...unless it covers this many files: a wake map should not show a thousand files as bare names
RAW_MAX = 16          # as in OptMem: up to 16 entries are summarised from themselves, more from their two halves


def sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class Summaries:
    """node id -> {text, basis, at}: the model's summaries, saved as they are written."""

    def __init__(self, path):
        self.path, self.data = path, {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                self.data = json.load(handle)

    def get(self, node_id):
        return self.data.get(node_id)

    def set(self, node_id, text, basis):
        self.data[node_id] = {"text": " ".join(text.split())[:700], "basis": basis, "at": time.strftime("%Y-%m-%d %H:%M")}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(temporary, self.path)


class ProjectTree:
    def __init__(self, name, root, home):
        self.name, self.root, self.home = name, root, home
        self.cache = F.FileCache(os.path.join(home, "content", "files.json"))
        self.summaries = Summaries(os.path.join(home, "content", "summaries.json"))
        self.files, self.entries_of, self.counts, self._heights = [], {}, {}, {}

    # ------------------------------------------------------------------ the files

    def scan(self):
        """Read the project's files (only those that changed since last time) and index them."""
        files = F.list_files(self.root)
        reread = self.cache.refresh(self.root, files)
        self.cache.save()
        self._index(files)
        return reread

    def load(self):
        self._index(sorted(self.cache.data))
        return self

    def _index(self, files):
        self.files, self._heights = files, {}
        dirs = {"": {"dirs": set(), "files": []}}
        self.counts = {"": 0}
        for rel in files:
            parts = rel.split("/")
            for i in range(len(parts) - 1):
                parent, child = "/".join(parts[:i]), "/".join(parts[:i + 1])
                dirs.setdefault(parent, {"dirs": set(), "files": []})["dirs"].add(child)
                dirs.setdefault(child, {"dirs": set(), "files": []})
                self.counts[child] = self.counts.get(child, 0) + 1
            dirs["/".join(parts[:-1])]["files"].append(rel)
            self.counts[""] += 1
        self.entries_of = {d: [("dir", x) for x in sorted(v["dirs"])] + [("file", x) for x in sorted(v["files"])] for d, v in dirs.items()}

    # ------------------------------------------------------------------ addressing

    def parse(self, node_id):
        node_id = node_id or "/"
        if "#" in node_id:
            path, bits = node_id.split("#", 1)
            if not path.endswith("/") and path != "":
                raise KeyError(node_id)
        elif node_id.endswith("/"):
            path, bits = node_id, ""
        else:
            if node_id not in self.cache.data:
                raise KeyError("No file or folder %r in %s" % (node_id, self.name))
            return "file", node_id, ""
        folder = path.rstrip("/")
        if folder not in self.entries_of or re.search("[^01]", bits):
            raise KeyError("No folder or interval %r in %s" % (node_id, self.name))
        return "slice", folder, bits

    @staticmethod
    def entry_id(entry):
        return entry[1] + "/" if entry[0] == "dir" else entry[1]

    def slice_entries(self, folder, bits):
        entries = self.entries_of[folder]
        for bit in bits:
            if len(entries) <= 1:
                raise KeyError("Already at a single entry")
            mid = (len(entries) + 1) // 2
            entries = entries[:mid] if bit == "0" else entries[mid:]
        return entries

    def _part_id(self, folder, bits, part):
        return self.entry_id(part[0]) if len(part) == 1 else "%s/#%s" % (folder, bits)

    def children(self, node_id):
        kind, folder, bits = self.parse(node_id)
        if kind == "file":
            return []
        entries = self.slice_entries(folder, bits)
        if len(entries) == 1:
            return [self.entry_id(entries[0])]
        mid = (len(entries) + 1) // 2
        return [self._part_id(folder, bits + "0", entries[:mid]), self._part_id(folder, bits + "1", entries[mid:])]

    def count(self, node_id):
        kind, folder, bits = self.parse(node_id)
        if kind == "file":
            return 1
        return sum(1 if kind_ == "file" else self.counts[rel] for kind_, rel in self.slice_entries(folder, bits))

    def title(self, node_id):
        kind, folder, bits = self.parse(node_id)
        base = lambda entry: os.path.basename(entry[1]) + ("/" if entry[0] == "dir" else "")
        if kind == "file":
            return os.path.basename(folder)
        if not bits:
            return (os.path.basename(folder) + "/") if folder else self.name + " (root)"
        entries = self.slice_entries(folder, bits)
        return "%s … %s" % (base(entries[0]), base(entries[-1]))

    # ------------------------------------------------------------------ summaries

    def mechanical(self, node_id):
        kind, folder, bits = self.parse(node_id)
        if kind == "file":
            return (self.cache.data[folder][3] or "").strip()
        entries = self.slice_entries(folder, bits)
        names = [os.path.basename(e[1]) + ("/" if e[0] == "dir" else "") for e in entries]
        shown = ", ".join(names[:4]) + (" … " + names[-1] if len(names) > 4 else "")
        if bits:
            return "%d entries, %d files: %s" % (len(entries), self.count(node_id), shown)
        lead = next((self.cache.data[rel][3] for kind_, rel in entries if kind_ == "file" and os.path.basename(rel).lower() in ("readme.md", "agents.md") and self.cache.data[rel][3]), "")
        return F.clip("%s%d files in %d entries: %s" % (lead + " · " if lead else "", self.count(node_id), len(entries), shown), 220)

    def text_of(self, node_id):
        """The text a parent is written from: the model's summary if there is one, else the mechanical one."""
        stored = self.summaries.get(node_id)
        return stored["text"] if stored else self.mechanical(node_id)

    def is_ai(self, node_id):
        kind, folder, bits = self.parse(node_id)
        if kind == "file":
            return False
        n = len(self.slice_entries(folder, bits))
        return n >= AI_DIR_MIN if not bits else n > AI_INTERVAL_MIN or self.count(node_id) >= AI_FILES_MIN

    def ai_inputs(self, node_id):
        """What a summary of this node is written from (OptMem's rule: up to 16 entries raw, more from two halves)."""
        kind, folder, bits = self.parse(node_id)
        entries = self.slice_entries(folder, bits)
        ids = [self.entry_id(e) for e in entries] if len(entries) <= RAW_MAX else self.children(node_id)
        return [{"id": i, "title": self.title(i), "files": self.count(i), "summary": self.text_of(i)} for i in ids]

    def basis(self, node_id):
        return sha(json.dumps([[i["id"], i["summary"]] for i in self.ai_inputs(node_id)], ensure_ascii=False))

    def source(self, node_id):
        stored = self.summaries.get(node_id)
        if not stored:
            return "mechanical"
        return "ai" if stored["basis"] == self.basis(node_id) else "ai-stale"

    def brief(self, node_id):
        kind = self.parse(node_id)[0]
        return {"id": node_id, "kind": "file" if kind == "file" else "dir" if node_id.endswith("/") else "interval",
                "title": self.title(node_id), "count": self.count(node_id), "summary": self.text_of(node_id), "source": self.source(node_id) if kind != "file" else "mechanical"}

    # ------------------------------------------------------------------ walking

    def height(self, node_id):
        """How many model-written layers lie below this node: the order summaries must be written in."""
        if node_id not in self._heights:
            below = max((self.height(child) for child in self.children(node_id)), default=0)
            self._heights[node_id] = below + (1 if self.is_ai(node_id) else 0)
        return self._heights[node_id]

    def ai_nodes(self):
        """Every node a model writes a summary for, lowest layer first."""
        found, stack = [], ["/"]
        while stack:
            node_id = stack.pop()
            if self.parse(node_id)[0] == "file":
                continue
            if self.is_ai(node_id):
                found.append(node_id)
            stack.extend(self.children(node_id))
        return sorted(found, key=lambda i: (self.height(i), i))

    def stale(self):
        return [i for i in self.ai_nodes() if self.source(i) != "ai"]

    def wake(self, budget=20):
        """A small map to start from: always open the biggest interval next, like PaperTree's and memo's wake."""
        frontier = ["/"]
        while len(frontier) < budget:
            openable = [i for i in frontier if self.parse(i)[0] == "slice" and self.children(i)]
            if not openable:
                break
            biggest = max(openable, key=self.count)
            frontier = [c for i in frontier for c in (self.children(biggest) if i == biggest else [i])]
        return [self.brief(i) for i in frontier]

    def zoom(self, node_id):
        return self.brief(node_id), [self.brief(c) for c in self.children(node_id)]
