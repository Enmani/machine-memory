"""The only place the harvester touches the memory: through memo's own functions, so the log and the
tree keep every guarantee memo gives them (fixed width, one lock, ids assigned inside it)."""
import json
import os
import sys

from .ledger import DIR, atomic_write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(DIR, "staging")
IMPORTED = os.path.join(DIR, "imported")


def load_memo():
    """memo's own store module, so the harvester and the command share one implementation of the format."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from memolib import store
    return store


def stage(thread, notes, turns_total):
    """Keep a thread's notes until they are appended, so a crash between reading and writing loses nothing."""
    name = "%s-%d.json" % (thread.ref.replace(":", "_"), turns_total)
    atomic_write(os.path.join(STAGING, name), json.dumps(
        {"ref": thread.ref, "started": thread.started, "notes": [{"date": d, "text": t} for d, t in notes]}, ensure_ascii=False, indent=1))


def append_notes(memo, notes):
    """Append [(date, line)]. Notes dated before the log's last memory take that date: the log is
    chronological and an imported memory cannot be older than the one before it. Returns (first_id, count)."""
    d = memo.store()
    memo.apply_config(d)
    items = []
    for date, text in notes:
        if len(text.encode("utf-8")) > memo.ENTRY_CHARS or "\n" in text:
            continue
        items.append((date, text))
    if not items:
        return None, 0
    from memolib.knowledge import Catalog
    with Catalog(d) as catalog:
        result = catalog.append(items)
    added = [i for i, appended in result if appended]
    return (added[0], len(added)) if added else (None, 0)


def import_staged(memo):
    """Everything staged, in date order, appended once; the staging files move to imported/."""
    files = sorted(os.listdir(STAGING)) if os.path.isdir(STAGING) else []
    rows = []
    for name in files:
        with open(os.path.join(STAGING, name), encoding="utf-8") as handle:
            record = json.load(handle)
        for index, note in enumerate(record["notes"]):
            rows.append((note["date"], record["started"], name, index, note["text"]))
    rows.sort()
    first, count = append_notes(memo, [(r[0], r[4]) for r in rows])
    os.makedirs(IMPORTED, exist_ok=True)
    for name in files:
        os.replace(os.path.join(STAGING, name), os.path.join(IMPORTED, name))
    return first, count
