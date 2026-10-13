"""What has been read already, kept next to the memory but outside it: the memory directory belongs to
the memo tool alone."""
import hashlib
import json
import os
import tempfile

DIR = os.path.expanduser(os.environ.get("HARVEST_DIR", "~/.optmem/harvest"))


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(temporary, path)


class SyncLock:
    """Only one sync at a time: a scheduled run must not start while the last is still working."""

    def __init__(self, root=DIR):
        os.makedirs(root, exist_ok=True)
        self.handle = open(os.path.join(root, "sync.lock"), "a")

    def acquire(self):
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self):
        self.handle.close()


class Ledger:
    """thread key -> {path, size, turns, status, notes, at}. A thread whose log has grown is read again,
    from the first turn it had not read before."""

    def __init__(self, root=DIR):
        self.path = os.path.join(root, "ledger.json")
        self.data = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as handle:
                self.data = json.load(handle)

    def get(self, key):
        return self.data.get(key)

    def put(self, key, **fields):
        self.data[key] = {**self.data.get(key, {}), **fields}

    def save(self):
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))


class Seen:
    """A fork or a resumed session repeats the turns it started from. Each long turn is remembered with the
    thread that said it first, and later threads skip it: the same event is never noted twice."""

    MIN = 80

    def __init__(self, root=DIR):
        self.path = os.path.join(root, "seen.tsv")
        self.owner = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    digest, _, key = line.rstrip("\n").partition("\t")
                    self.owner[digest] = key

    @staticmethod
    def digest(turn):
        return hashlib.sha1((turn.role + "\0" + turn.text[:600]).encode("utf-8")).hexdigest()[:16]

    def fresh(self, thread):
        """The thread's turns minus those an earlier thread already said; remembers what is new."""
        kept = []
        for turn in thread.turns:
            if len(turn.text) < self.MIN:
                kept.append(turn)
                continue
            digest = self.digest(turn)
            owner = self.owner.setdefault(digest, thread.key)
            if owner == thread.key:
                kept.append(turn)
        return kept

    def save(self):
        atomic_write(self.path, "".join("%s\t%s\n" % item for item in self.owner.items()))
