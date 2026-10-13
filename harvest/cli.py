"""python harvest.py <command>: stats, distill, assemble, nap, sync, show."""
import argparse
import collections
import json
import os
import re
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import ledger as ledger_module
from .distill import distill_thread, render, segments
from memolib.engine import Engine
from .links import Links
from .memory_io import append_notes, import_staged, load_memo, stage
from .model import CODES, SOURCE_BY_CODE, local_date
from .napper import nap_all
from .project import project_of
from .sources import ADAPTERS

PROJECTS_DIR = os.path.expanduser("~/Projects")


def say(message):
    print(message, file=sys.stderr, flush=True)


def approximate_time(path):
    """Cheap ordering of log files, so forks are read after the thread they forked from."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", os.path.basename(path))
    return "%sT%s:%s:%s" % match.groups() if match else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path)))


def chosen_adapters(names):
    return [ADAPTERS[name]() for name in (ADAPTERS if names == "all" else names.split(","))]


def known_projects():
    names = {"general"}
    if os.path.isdir(PROJECTS_DIR):
        names |= {n[:-5] if n.lower().endswith("-work") and len(n) > 5 else n for n in os.listdir(PROJECTS_DIR)}
    return sorted(names)


def worklist(adapters):
    items = [(approximate_time(path), adapter, path) for adapter in adapters for path in adapter.discover()]
    return sorted(items, key=lambda item: item[0])


# ------------------------------------------------------------------ stats

def cmd_stats(args):
    """How much there is to read, before spending anything on a model."""
    by_source, by_project, by_month = collections.Counter(), collections.Counter(), collections.Counter()
    totals = collections.Counter()
    started = time.time()
    items = worklist(chosen_adapters(args.source))
    for number, (_, adapter, path) in enumerate(items, 1):
        if number % 40 == 0:
            say("read %d/%d files (%ds)" % (number, len(items), time.time() - started))
        for thread in adapter.load(path):
            totals["threads"] += 1
            if thread.subagent:
                totals["subagent threads skipped"] += 1
                continue
            pairs = segments(thread.turns)
            if not pairs:
                continue
            lines = render(pairs)
            by_source[thread.source] += 1
            by_project[project_of(thread.cwd)] += 1
            by_month[local_date(thread.started)[:7]] += 1
            totals["kept threads"] += 1
            totals["user turns"] += len(pairs)
            totals["transcript chars"] += sum(len(line) for line in lines)
            totals["model calls (chunks of 48k chars)"] += max(1, sum(len(line) for line in lines) // 48000 + 1)
    for key, value in totals.items():
        print("%-36s %s" % (key, "{:,}".format(value)))
    print("\nthreads kept by source:", dict(by_source))
    print("by month:", dict(sorted(by_month.items())))
    print("top projects:", by_project.most_common(15))
    print("\nreading took %ds" % (time.time() - started))


# ------------------------------------------------------------------ distill / sync

def make_engine(args):
    engine = Engine(model=args.model or "", concurrent=args.workers)
    ok, detail = engine.available()
    if not ok:
        sys.exit("The AI engine (omp) is not available: %s" % detail)
    return engine


def process(args, only_new, quiet_hours=0):
    """Read threads in time order, distill each (or only its new tail), and stage the notes."""
    adapters = chosen_adapters(args.source)
    ledger, seen, links, engine = ledger_module.Ledger(), ledger_module.Seen(), Links(), make_engine(args)
    projects = known_projects()
    pending, done, notes_total = {}, 0, 0
    cutoff = time.time() - quiet_hours * 3600
    with ThreadPoolExecutor(args.workers) as pool:
        def finish(future):
            nonlocal done, notes_total
            thread, total, sig = pending.pop(future)
            try:
                notes = future.result()
            except Exception as error:
                say("FAILED %s: %s" % (thread.ref, error))
                ledger.put(thread.key, status="failed", error=str(error)[:200], **sig)
                return
            if notes:
                stage(thread, notes, total)
            ledger.put(thread.key, status="distilled" if notes else "skipped", turns=total, notes=len(notes), at=time.strftime("%Y-%m-%d %H:%M"), **sig)
            done += 1
            notes_total += len(notes)
            say("%s %s -> %d notes" % (thread.ref, (thread.title or thread.cwd)[:40], len(notes)))
            for date, text in notes[:args.show]:
                print("  %s %s" % (date, text))
            ledger.save()
            seen.save()

        for _, adapter, path in worklist(adapters):
            if args.limit and done + len(pending) >= args.limit:
                break
            signature = {"path": path, "size": os.path.getsize(path), "mtime": int(os.path.getmtime(path))}
            for thread in adapter.load(path):
                if thread.subagent or (args.ref and not thread.ref.startswith(args.ref)):
                    continue
                if args.since and local_date(thread.started) < args.since:
                    continue
                record = ledger.get(thread.key)
                if record and record.get("status") in ("distilled", "skipped") and record.get("size") == signature["size"]:
                    continue
                if quiet_hours and signature["mtime"] > cutoff:
                    continue  # still being written: read it when it has been quiet for a while
                already = record["turns"] if record and record.get("status") in ("distilled", "skipped") else 0
                fresh = seen.fresh(thread)
                tail = fresh[already:] if already and already <= len(thread.turns) else fresh
                if not tail:
                    ledger.put(thread.key, status="skipped", turns=len(thread.turns), notes=0, **signature)
                    continue
                if args.dry_run:
                    say("would read %s %s (%d turns)" % (thread.ref, (thread.title or thread.cwd)[:40], len(tail)))
                    done += 1
                    continue
                total = len(fresh)  # post-dedupe count, so a grown thread resumes where this run stopped
                hint = project_of(thread.cwd)
                future = pool.submit(distill_thread, thread, tail, hint, projects, engine, links, args.thinking)
                thread.turns = []  # the pool holds what it needs; do not keep a whole thread alive
                pending[future] = (thread, total, signature)
                while len(pending) >= args.workers * 2:
                    for future in wait(list(pending), return_when=FIRST_COMPLETED)[0]:
                        finish(future)
        for future in wait(list(pending))[0] if pending else []:
            finish(future)
    ledger.save()
    seen.save()
    say("threads processed: %d, notes staged: %d" % (done, notes_total))


def cmd_distill(args):
    process(args, only_new=False, quiet_hours=args.quiet)


def cmd_assemble(args):
    memo = load_memo()
    first, count = import_staged(memo)
    print("appended %d notes%s" % (count, " starting at #%d" % first if count else ""))
    d = memo.store()
    print("pending compressions: %d" % memo.pending_count(d, memo.log_len(d)))


def cmd_nap(args):
    memo = load_memo()
    nap_all(memo, make_engine(args), workers=args.workers, batch=args.batch, thinking=args.thinking, progress=say)


LIVE_REF = re.compile(r"\[(?:cx|cc|om):[0-9a-f]{8}\]$")


def cmd_rebuild(args):
    """Rebuild the memory in true date order from everything already read. A note found late (a conversation
    that was still being written, or an agent added later) is appended with the log's last date, so after a
    batch of late finds the order drifts; this puts every note back where its date says. Notes an agent wrote
    itself cannot be reproduced from the staged files, so they are kept and re-dated into place with the rest."""
    import shutil
    from . import memory_io
    memo = load_memo()
    d = memo.store()
    memo.apply_config(d)
    stray = [(e[1], e[2]) for e in memo.log_scan(d) if not LIVE_REF.search(e[2])]
    if not args.yes:
        sys.exit("This archives the current log and tree and rebuilds them (%d notes written by agents are kept). Add --yes to go ahead." % len(stray))
    backup = os.path.join(ledger_module.DIR, "backups", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(backup)
    shutil.move(memo.log_path(d), os.path.join(backup, "LOG.txt"))
    shutil.move(os.path.join(d, "TREE"), os.path.join(backup, "TREE"))
    os.makedirs(os.path.join(d, "TREE"))
    open(memo.log_path(d), "a").close()
    os.makedirs(memory_io.STAGING, exist_ok=True)
    if stray:
        memory_io.stage(type("Live", (), {"ref": "live:" + time.strftime("%H%M%S"), "started": ""})(), stray, len(stray))
    for name in os.listdir(memory_io.IMPORTED) if os.path.isdir(memory_io.IMPORTED) else []:
        os.replace(os.path.join(memory_io.IMPORTED, name), os.path.join(memory_io.STAGING, name))
    first, count = import_staged(memo)
    say("rebuilt %d notes in date order; the old log and tree are in %s" % (count, backup))
    nap_all(memo, make_engine(args), workers=args.workers, batch=args.batch, thinking=args.thinking, progress=say)


def open_log():
    """Send everything this run prints to sync.log (a scheduled run has no window to print to)."""
    path = os.path.join(ledger_module.DIR, "sync.log")
    os.makedirs(ledger_module.DIR, exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        os.replace(path, path + ".1")
    stream = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    print("=== %s sync ===" % time.strftime("%Y-%m-%d %H:%M"))


def cmd_sync(args):
    """The routine run: read what is new and quiet, append it, compress what that made due, then bring every
    indexed project up to date."""
    if args.log:
        open_log()
    lock = ledger_module.SyncLock()
    if not lock.acquire():
        return say("another sync is still running; this one stops")
    try:
        process(args, only_new=True, quiet_hours=args.quiet)
        memo = load_memo()
        first, count = import_staged(memo)
        say("appended %d notes" % count)
        if count and not args.no_nap:
            nap_all(memo, make_engine(args), workers=args.workers, batch=args.batch, thinking=args.thinking, progress=say)
        relation_failure = None
        if not args.no_relations and not args.dry_run:
            from memolib.knowledge import Catalog
            from memolib.review import review
            try:
                with Catalog(memo.store()) as catalog:
                    with catalog.transaction():
                        queued = any(not r["reviewed"] for r in catalog.rows())
                    if queued:
                        result = review(catalog, make_engine(args), limit=24, apply=True)
                        say("relations: reviewed %d memories, saved %d links (%d model calls)" %
                            (result["reviewed"], len(result["relations"]), result["calls"]))
            except Exception as error:
                relation_failure = "relationship review failed: %s" % error
                say(relation_failure + "; pending memories retained for retry")
        if not args.no_projects:
            from ptree.cli import sync_all
            sync_all(workers=args.workers, model=args.model)
        if relation_failure:
            sys.exit(relation_failure)
    finally:
        lock.release()


# ------------------------------------------------------------------ show (the evidence behind a note)

def find_thread(ref):
    code, _, prefix = ref.partition(":")
    if code not in SOURCE_BY_CODE or not prefix:
        sys.exit("A reference looks like cx:019e7e85 (cx codex, cc claude, om omp).")
    adapter = ADAPTERS[SOURCE_BY_CODE[code]]()
    record = next((v for k, v in ledger_module.Ledger().data.items() if k.startswith(SOURCE_BY_CODE[code] + ":" + prefix)), None)
    paths = [record["path"]] if record and os.path.exists(record["path"]) else [p for p in adapter.discover() if prefix in os.path.basename(p) or prefix in p]
    for path in paths:
        for thread in adapter.load(path):
            if thread.id.startswith(prefix):
                return thread
    sys.exit("No conversation matches %s." % ref)


def cmd_show(args):
    thread = find_thread(args.ref)
    print("%s  %s\nproject %s  cwd %s\n%s .. %s  (%d turns)  %s\n" % (
        thread.ref, thread.title or "(untitled)", project_of(thread.cwd), thread.cwd, local_date(thread.started),
        local_date(thread.ended), len(thread.turns), thread.path))
    lo, hi = (int(x) for x in args.turns.split("-")) if args.turns else (0, len(thread.turns) - 1)
    pattern = re.compile(args.grep, re.I) if args.grep else None
    for index, turn in enumerate(thread.turns):
        if not lo <= index <= hi or (pattern and not pattern.search(turn.text)):
            continue
        text = turn.text if args.full or len(turn.text) <= 1500 else turn.text[:1100] + "\n…[%d chars cut; use --full]…\n" % len(turn.text) + turn.text[-300:]
        print("[%d] %s %s %s\n%s\n" % (index, turn.role, local_date(turn.at), turn.at[11:16], text))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harvest.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, ai=False):
        p.add_argument("--source", default="all", help="all, or a comma list of: " + ",".join(ADAPTERS))
        if ai:
            p.add_argument("--workers", type=int, default=3)
            p.add_argument("--model", default="")
            p.add_argument("--thinking", default="low")

    p = sub.add_parser("stats", help="count what there is to read; no model is called")
    common(p)
    p.set_defaults(run=cmd_stats)
    for name, run in (("distill", cmd_distill), ("sync", cmd_sync)):
        p = sub.add_parser(name)
        common(p, ai=True)
        p.add_argument("--limit", type=int, default=0, help="stop after this many threads")
        p.add_argument("--ref", default="", help="only threads whose reference starts with this")
        p.add_argument("--since", default="", help="only threads started on or after YYYY-MM-DD")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--show", type=int, default=3, help="print this many notes per thread")
        p.add_argument("--quiet", type=float, default=3 if name == "sync" else 0, help="hours a log must be unchanged before it is read")
        if name == "sync":
            p.add_argument("--batch", type=int, default=8)
            p.add_argument("--no-nap", action="store_true")
            p.add_argument("--no-projects", action="store_true", help="skip the per-project indexes")
            p.add_argument("--no-relations", action="store_true", help="skip bounded semantic duplicate and decision review")
            p.add_argument("--log", action="store_true", help="write progress to ~/.optmem/harvest/sync.log")
        p.set_defaults(run=run)
    p = sub.add_parser("assemble", help="append staged notes to the memory, in date order")
    p.set_defaults(run=cmd_assemble)
    p = sub.add_parser("nap", help="do every pending compression with the model")
    common(p, ai=True)
    p.add_argument("--batch", type=int, default=8)
    p.set_defaults(run=cmd_nap)
    p = sub.add_parser("rebuild", help="rebuild the log and tree in date order from what has been read")
    common(p, ai=True)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(run=cmd_rebuild)
    p = sub.add_parser("show", help="the original conversation behind a note, e.g. cx:019e7e85")
    p.add_argument("ref")
    p.add_argument("--turns", default="")
    p.add_argument("--grep", default="")
    p.add_argument("--full", action="store_true")
    p.set_defaults(run=cmd_show)
    args = parser.parse_args(argv)
    args.run(args)
