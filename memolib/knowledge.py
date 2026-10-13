"""Optional relationships and duplicate observations; the OptMem log remains authoritative.

SQLite ships with Python. This sidecar has no server or embeddings. Relations name
the complete original text, so reordering/re-dating the log cannot attach an edge
to a different memory. Log positions are refreshed under the existing memory lock.
"""
import contextlib
import re
import sqlite3

from . import store as S

REF = re.compile(r"\s*\[(?:cx|cc|om):[0-9a-f]{8}\]\s*$", re.I)
TAG = re.compile(r"^\[([^\]\n]{1,40})\]")
KINDS = {"duplicate", "replaces", "enriches", "challenges"}


def fields(text):
    match = TAG.match(text)
    project = match.group(1).casefold() if match else "general"
    body = REF.sub("", text[match.end():] if match else text)
    # Retain numbers, paths, punctuation, dates and case: they can change meaning.
    return project, " ".join(body.split())


class Catalog:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory.rstrip("/\\") + ".knowledge.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self._edge_cache = None
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
          text TEXT PRIMARY KEY, project TEXT NOT NULL, core TEXT NOT NULL,
          reviewed INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS content_key ON notes(project, core);
        CREATE TABLE IF NOT EXISTS locations (
          id INTEGER PRIMARY KEY, date TEXT NOT NULL,
          text TEXT NOT NULL REFERENCES notes(text));
        CREATE INDEX IF NOT EXISTS location_text ON locations(text);
        CREATE TABLE IF NOT EXISTS observations (
          date TEXT NOT NULL, text TEXT NOT NULL REFERENCES notes(text),
          canonical TEXT NOT NULL REFERENCES notes(text),
          PRIMARY KEY(date,text,canonical));
        CREATE TABLE IF NOT EXISTS relations (
          newer TEXT NOT NULL REFERENCES notes(text),
          older TEXT NOT NULL REFERENCES notes(text), kind TEXT NOT NULL,
          reason TEXT NOT NULL, origin TEXT NOT NULL,
          PRIMARY KEY(newer,older,kind));
        """)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        with S.Lock(self.directory), self.db:
            self.sync()
            yield self

    def register(self, text, reviewed=1):
        project, core = fields(text)
        self.db.execute("INSERT OR IGNORE INTO notes VALUES (?,?,?,?)",
                        (text, project, core, reviewed))

    def sync(self):
        """Reconcile positions from raw text, including after a legacy log rebuild.

        Reading the small fixed-width log avoids relying on a digest or an assumed
        unchanged prefix. No log or tree bytes are rewritten here.
        """
        self._edge_cache = None
        rows = list(S.log_scan(self.directory))
        self.db.execute("DELETE FROM locations")
        self.db.executemany("INSERT OR IGNORE INTO notes(text,project,core) VALUES (?,?,?)",
                            [(text, *fields(text)) for _, _, text in rows])
        self.db.executemany("INSERT INTO locations VALUES (?,?,?)", rows)
        groups = {}
        for _, _, text in rows:
            key = fields(text)
            canonical = groups.setdefault(key, text)
            if canonical != text:
                self.db.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?,?)",
                                (text, canonical, "duplicate", "Same content; conversation references retained.", "exact"))

    def rows(self, projects=()):
        rows = self.db.execute("""SELECT MIN(l.id) id, MIN(l.date) date, n.text, n.project, n.reviewed
          FROM locations l JOIN notes n ON l.text=n.text GROUP BY n.text ORDER BY id""").fetchall()
        return [dict(r) for r in rows if not projects or r["project"] in projects]

    def note(self, note_id):
        row = self.db.execute("SELECT * FROM locations WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise ValueError("No memory #%d." % note_id)
        return dict(row)

    def position(self, text):
        return self.db.execute("SELECT MIN(id) FROM locations WHERE text=?", (text,)).fetchone()[0]

    def exact(self, text):
        project, core = fields(text)
        row = self.db.execute("""SELECT l.id,n.text FROM notes n JOIN locations l ON n.text=l.text
          WHERE n.project=? AND n.core=? ORDER BY l.id LIMIT 1""", (project, core)).fetchone()
        return dict(row) if row is not None else None

    def append(self, items):
        """Return [(log_id, was_appended)]. Duplicate observations remain inspectable.

        The memory lock covers both the duplicate lookup and durable append. A
        failed sidecar commit can be retried without appending another log record.
        """
        items = [(date, S.check_line(text)) for date, text in items]
        result = []
        with self.transaction():
            total = S.log_len(self.directory)
            floor = S.log_get(self.directory, total - 1)[1] if total else ""
            for date, text in items:
                found = self.exact(text)
                if found:
                    self.register(text)
                    self.db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?)",
                                    (date, text, found["text"]))
                    if text != found["text"]:
                        self.db.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?,?)",
                                        (text, found["text"], "duplicate", "Same content; source preserved.", "exact"))
                    result.append((found["id"], False))
                else:
                    floor = max(date, floor)
                    self.register(text, reviewed=0)
                    note_id = S.log_append_locked(self.directory, [(floor, text)])
                    self.db.execute("INSERT INTO locations VALUES (?,?,?)", (note_id, floor, text))
                    if date != floor:
                        self.db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?)", (date, text, text))
                    result.append((note_id, True))
            self._edge_cache = None
        return result

    def edges(self):
        # Only edges whose endpoints still have evidence participate in current recall.
        if self._edge_cache is None:
            self._edge_cache = [dict(r) for r in self.db.execute("""SELECT r.* FROM relations r
              WHERE EXISTS(SELECT 1 FROM locations WHERE text=r.older)
              AND (EXISTS(SELECT 1 FROM locations WHERE text=r.newer)
                OR EXISTS(SELECT 1 FROM observations WHERE text=r.newer))""")]
            positions = dict(self.db.execute("SELECT text,MIN(id) FROM locations GROUP BY text"))
            parent = {}

            def root(text):
                parent.setdefault(text, text)
                while parent[text] != text:
                    parent[text] = parent[parent[text]]
                    text = parent[text]
                return text

            def order(text):
                return (positions.get(text, float("inf")), text)

            for edge in self._edge_cache:
                if edge["kind"] == "duplicate":
                    a, b = root(edge["newer"]), root(edge["older"])
                    if a != b:
                        first, second = sorted((a, b), key=order)
                        parent[second] = first
            self._canonical = {text: root(text) for text in parent}
            self._replacements = {}
            for edge in self._edge_cache:
                if edge["kind"] == "replaces":
                    older = self._canonical.get(edge["older"], edge["older"])
                    newer = self._canonical.get(edge["newer"], edge["newer"])
                    self._replacements.setdefault(older, []).append(newer)
        return self._edge_cache

    def resolve(self, text, history=False):
        self.edges()
        text = self._canonical.get(text, text)
        seen = set()
        while text not in seen:
            seen.add(text)
            if not history:
                replacements = self._replacements.get(text, [])
                if replacements:
                    text = max(replacements, key=lambda t: self.position(t) if self.position(t) is not None else -1)
                    continue
            return text
        raise ValueError("Relationship cycle: inspect the relations before recalling this memory.")

    def relate(self, newer_id, older_id, kind, reason, origin="manual"):
        if kind not in KINDS or not reason.strip():
            raise ValueError("A relation needs duplicate/replaces/enriches/challenges and a reason.")
        newer, older = self.note(newer_id), self.note(older_id)
        if newer_id <= older_id or newer["text"] == older["text"]:
            raise ValueError("The first memory must be newer than the second.")
        if fields(newer["text"])[0] != fields(older["text"])[0]:
            raise ValueError("Relations must stay within the same project.")
        # A terminal current decision cannot also become its own older duplicate.
        saved = list(self.db.execute("SELECT * FROM relations"))
        self.db.execute("INSERT OR REPLACE INTO relations VALUES (?,?,?,?,?)",
                        (newer["text"], older["text"], kind, reason.strip(), origin))
        self._edge_cache = None
        try:
            self.resolve(newer["text"])
            self.resolve(older["text"])
        except ValueError:
            self.db.execute("DELETE FROM relations")
            self.db.executemany("INSERT INTO relations VALUES (?,?,?,?,?)", [tuple(r) for r in saved])
            self._edge_cache = None
            raise

    def annotations(self, text):
        lines = []
        canonical = self.resolve(text, history=True)
        for edge in self.edges():
            if edge["kind"] != "duplicate" and self.resolve(edge["older"], True) == canonical:
                lines.append("%s by #%s: %s" % (edge["kind"], self.position(edge["newer"]), edge["reason"]))
            elif edge["kind"] != "duplicate" and self.resolve(edge["newer"], True) == canonical:
                lines.append("%s #%s: %s" % (edge["kind"], self.position(edge["older"]), edge["reason"]))
            elif edge["kind"] == "duplicate" and edge["newer"] == text:
                lines.append("%s #%s: %s" % (edge["kind"], self.position(edge["older"]), edge["reason"]))
        return lines

    def sources(self, text):
        canonical = self.resolve(text, history=True)
        texts = {canonical, *(t for t, root in self._canonical.items() if root == canonical)}
        rows = [dict(r) for r in self.db.execute("SELECT * FROM locations") if r["text"] in texts]
        rows += [dict(r) for r in self.db.execute("SELECT date,text,NULL id FROM observations") if r["text"] in texts]
        return sorted(rows, key=lambda r: (r["date"], r["id"] is None, r["text"]))
