"""python ptree.py <command>: per-project indexes that work with the machine memory.

  projects                       every project: indexed ones with their state, and the tags the memory knows
  register <name> <root>         start indexing a project (sends its file outlines to the model once you sync --ai)
  stats <project>                what is indexed and how much model work is stale
  sync [project] [--ai]          route the project's notes, rescan its files, and with --ai write what is stale
  wake <project>                 the project's current state, as a small map
  zoom <project> <id>            open one node: apps/web/  apps/web/#01  apps/web/main.ts  /
  find <project> "<question>"    where it is now (files) and what happened (notes), in one answer
  node <project> <path>          one file or folder: what it is, and the notes that explain it
  verify <project>               notes whose files are gone or changed since
  history <project> wake|zoom|recall|nap ...   the project's own memory (the global notes tagged for it)
"""
import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from memolib import store as S  # noqa: E402

from . import history, links, registry  # noqa: E402
from .search import search_notes, search_tree  # noqa: E402
from .tree import ProjectTree  # noqa: E402

ME = "python " + registry.forward(os.path.join(ROOT, "ptree.py"))
TAG = re.compile(r"\[([^\]\n]{1,40})\]")


def die(message):
    sys.exit(message)


def project_tree(name, scan=False):
    key, entry = registry.get(name)
    tree = ProjectTree(key, entry["root"], registry.home(key))
    if scan or not tree.cache.data:
        tree.scan()
    else:
        tree.load()
    return key, entry, tree


def line(brief):
    marker = {"ai-stale": "  (summary out of date)", "mechanical": "" if brief["kind"] == "file" else "  (outline only)"}.get(brief["source"], "")
    size = "  [%d files]" % brief["count"] if brief["kind"] != "file" else ""
    return "%s%s  %s\n    %s%s" % (brief["id"], size, brief["title"], brief["summary"], marker)


def changes_of(key, entry):
    path = os.path.join(registry.home(key), "content", "changes.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return links.last_changes(entry["root"])


def note_line(note):
    return "#%d %s %s" % note


# ------------------------------------------------------------------ commands

def cmd_projects(args):
    registry_ = registry.load()
    global_dir = S.memory_dir()
    tags = Counter()
    if os.path.isdir(global_dir) and S.log_len(global_dir):
        for _, _, text in S.log_scan(global_dir):
            tag = TAG.match(text)
            if tag:
                tags[tag.group(1)] += 1
    if registry_:
        print("Indexed projects (ptree wake <name>):")
    for name, entry in registry_.items():
        folder = history.memory_path(name)
        notes = S.log_len(folder) if os.path.isdir(folder) else 0
        last = S.log_get(folder, notes - 1)[1] if notes else "-"
        tree = ProjectTree(name, entry["root"], registry.home(name))
        tree.load()
        root = tree.summaries.get("/")
        print("  %s  %s  %d files, %d notes, last note %s\n      %s" % (name, entry["root"], len(tree.files), notes, last,
              root["text"] if root else "(no summary yet: run ptree sync %s --ai)" % name))
    known = {k.casefold() for k in registry_} | {a.casefold() for e in registry_.values() for a in e.get("aliases", [])}
    rest = [(t, n) for t, n in tags.most_common() if t.casefold() not in known]
    if rest:
        print("\nOther projects the memory knows (not indexed; memo recall \"[name]\" lists their notes):")
        print("  " + ", ".join("%s (%d)" % item for item in rest[:25]))


def cmd_register(args):
    entry = registry.register(args.name, args.root, args.alias, args.worktrees)
    history.init(args.name)
    print("Registered %s -> %s (aliases: %s). Next: %s sync %s --ai" % (args.name, entry["root"], ", ".join(entry["aliases"]) or "none", ME, args.name))


def cmd_stats(args):
    key, entry, tree = project_tree(args.project)
    nodes, stale = tree.ai_nodes(), tree.stale()
    print("%s  %s\nfiles indexed: %d   folders: %d\nsummaries the model writes: %d (%d stale, about %d model calls)\nnotes: %d"
          % (key, entry["root"], len(tree.files), len(tree.entries_of), len(nodes), len(stale), -(-len(stale) // 8), len(history.notes(key))))


def make_engine(args):
    from memolib.engine import Engine
    engine = Engine(model=args.model or "", concurrent=args.workers)
    ok, detail = engine.available()
    if not ok:
        die("The AI engine (omp) is not available: %s" % detail)
    return engine


def sync_one(name, args):
    key, entry = registry.get(name)
    added, rebuilt = history.route(key, entry, S.memory_dir())
    tree = ProjectTree(key, entry["root"], registry.home(key))
    reread = tree.scan()
    changes = links.last_changes(entry["root"])
    os.makedirs(os.path.join(registry.home(key), "content"), exist_ok=True)
    with open(os.path.join(registry.home(key), "content", "changes.json"), "w", encoding="utf-8") as handle:
        json.dump(changes, handle)
    print("%s: %d notes routed%s; %d files indexed (%d read); %d summaries stale" % (key, added, " after a rebuild" if rebuilt else "", len(tree.files), reread, len(tree.stale())))
    if args.ai:
        from harvest.napper import nap_all
        from . import keeper
        engine = make_engine(args)
        d = history.memory_path(key)
        if args.what in ("history", "all") and S.pending_count(d, S.log_len(d)):
            previous = os.environ.get("MEMORY_DIR")
            os.environ["MEMORY_DIR"] = d
            try:
                nap_all(S, engine, workers=args.workers, batch=8, progress=lambda m: print("  history:", m, flush=True))
            finally:
                os.environ.pop("MEMORY_DIR", None) if previous is None else os.environ.__setitem__("MEMORY_DIR", previous)
        if args.what in ("content", "all"):
            written = keeper.refresh(tree, engine, workers=args.workers, max_calls=args.max_calls, progress=lambda m: print("  tree:", m, flush=True))
            print("%s: %d summaries written, %d stale" % (key, written, len(tree.stale())))


def sync_all(workers=3, model="", max_stale=60):
    """The routine run for every registered project: route and rescan, then let the model catch up on what is
    due, unless a lot is stale (a big change deserves a deliberate `ptree sync <project> --ai`)."""
    for name in registry.load():
        sync_one(name, argparse.Namespace(ai=False, what="all", max_calls=0, workers=workers, model=model))
        key, entry = registry.get(name)
        tree = ProjectTree(key, entry["root"], registry.home(key)).load()
        folder = history.memory_path(key)
        stale, pending = len(tree.stale()), S.pending_count(folder, S.log_len(folder))
        if pending or 0 < stale <= max_stale:
            sync_one(name, argparse.Namespace(ai=True, what="all", max_calls=0, workers=workers, model=model))
        elif stale:
            print("%s: %d summaries are stale; run: %s sync %s --ai" % (key, stale, ME, key))


def cmd_sync(args):
    for name in ([args.project] if args.project else list(registry.load())):
        sync_one(name, args)


def cmd_wake(args):
    key, entry, tree = project_tree(args.project)
    stale = len(tree.stale())
    print("%s  %s  (%d files; %s)\nOpen a branch: %s zoom %s <id>   Search: %s find %s \"<question>\"   History: %s history %s wake\n"
          % (key, entry["root"], len(tree.files), "summaries current" if not stale else "%d summaries out of date" % stale, ME, key, ME, key, ME, key))
    for brief in tree.wake(args.budget):
        print(line(brief))


def cmd_zoom(args):
    key, entry, tree = project_tree(args.project)
    try:
        brief, kids = tree.zoom(args.id)
    except KeyError as error:
        die(str(error.args[0]))
    print(line(brief) + "\n")
    for kid in kids:
        print(line(kid))
    if not kids:
        print("(a file: %s node %s %s shows the notes that mention it)" % (ME, key, brief["id"]))


def resolver_for(entry, tree):
    return links.Resolver(entry["root"], tree.files, entry.get("worktrees", []))


def cmd_node(args):
    key, entry, tree = project_tree(args.project)
    resolver = resolver_for(entry, tree)
    path = args.path.replace("\\", "/")
    try:
        brief = tree.brief(path if path.endswith("/") or path in tree.cache.data or "#" in path else path + "/")
    except KeyError as error:
        die(str(error.args[0]))
    kind = "file" if brief["kind"] == "file" else "dir"
    print(line(brief))
    changes = changes_of(key, entry)
    if kind == "file":
        size, mtime = tree.cache.data[brief["id"]][:2]
        print("\n%d bytes, modified %s, last commit %s" % (size, datetime.datetime.fromtimestamp(mtime / 1e9).strftime("%Y-%m-%d"), changes.get(brief["id"], "unknown")))
    index = links.reverse_index(history.notes(key), resolver)
    ids = links.notes_about(index, brief["id"].rstrip("/") if kind == "dir" else brief["id"], kind)
    notes = {n[0]: n for n in history.notes(key)}
    shown = [notes[i] for i in ids][-8:]
    print("\n%d notes mention it%s" % (len(ids), ":" if shown else "."))
    for note in shown:
        print("  " + note_line(note))


def cmd_find(args):
    key, entry, tree = project_tree(args.project)
    resolver = resolver_for(entry, tree)
    changes = changes_of(key, entry)
    files, folders = search_tree(tree, args.question)
    print("Where it is now (%s, %d files)" % (key, len(tree.files)))
    for node_id, score, shared in folders[:5]:
        print("  %s  [%d files]  %s\n      %s" % (node_id, tree.count(node_id), tree.title(node_id), tree.text_of(node_id)))
    for rel, score, shared in files:
        print("  %s\n      %s" % (rel, tree.cache.data[rel][3]))
    if not files and not folders:
        print("  nothing matched in the files")
    notes = history.notes(key)
    hits = search_notes(notes, args.question, args.notes)
    if args.semantic:
        from memolib.knowledge import Catalog
        from memolib.semantic import search
        from memolib.engine import Engine
        with Catalog(S.store()) as catalog:
            with catalog.transaction():
                pass
            engine = Engine(model=args.model, concurrent=1, timeout=180, retries=0)
            ok, detail = engine.available()
            if not ok:
                die("Semantic search needs the existing omp CLI: " + detail)
            result = search(catalog, engine, args.question, registry.names_of(key, entry), args.notes)
            hits = [((r["id"], r["date"], r["text"]), 0, []) for r in result["hits"]]
            if result["limited"]:
                print("  semantic search reached its call budget; results are partial")
    print("\nWhat happened (notes about %s, %s)" % (key, "by relevance" if args.semantic else "oldest first"))
    from memolib.knowledge import Catalog
    with Catalog(S.store()) as catalog:
        with catalog.transaction():
            pass
        shown = set()
        for note, score, shared in hits:
            current = catalog.resolve(note[2])
            if current in shown:
                continue
            shown.add(current)
            position = catalog.position(current)
            if position is not None:
                row = catalog.note(position)
                note = (row["id"], row["date"], row["text"])
            flags = [issue for _, _, _, issues in links.verify([note], resolver, changes) for issue in issues]
            print("  " + note_line(note) + ("".join("\n      ! " + f for f in flags)))
            for annotation in catalog.annotations(current):
                print("      " + annotation)
    if not hits:
        print("  no note matched; try: %s history %s recall \"<regex>\"" % (ME, key))


def cmd_verify(args):
    key, entry, tree = project_tree(args.project)
    flagged = links.verify(history.notes(key), resolver_for(entry, tree), changes_of(key, entry))
    if not flagged:
        return print("No note names a file that is gone or changed since.")
    print("%d notes name files that are gone or changed since (newest first); check them against the files:" % len(flagged))
    for note_id, date, text, issues in flagged[::-1][:args.limit]:
        print("  #%d %s %s%s" % (note_id, date, text[:170], "".join("\n      ! " + i for i in issues)))


MEMO_COMMANDS = {"wake", "zoom", "recall", "config", "nap"}


def cmd_history(args):
    key, entry = registry.get(args.project)
    if not args.words or args.words[0] not in MEMO_COMMANDS:
        die("usage: %s history %s wake | zoom <a-b> | recall <regex> | nap | config\n(Write notes with the global memo: they are routed here.)" % (ME, key))
    folder = history.init(key)
    os.environ["MEMORY_DIR"] = folder
    loader = importlib.machinery.SourceFileLoader("memo_script", os.path.join(ROOT, "memo"))
    spec = importlib.util.spec_from_loader("memo_script", loader)
    memo = importlib.util.module_from_spec(spec)
    loader.exec_module(memo)
    memo.ME = S.ME = "%s history %s" % (ME, key)  # every follow-up command the tool prints names this project
    S.apply_config(folder)
    memo.COMMANDS[args.words[0]](args.words[1:])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ptree.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, run, *arguments, **options):
        p = sub.add_parser(name)
        for argument in arguments:
            p.add_argument(argument, **options.get(argument, {}))
        p.set_defaults(run=run)
        return p

    add("projects", cmd_projects)
    p = add("register", cmd_register, "name", "root")
    p.add_argument("--alias", action="append", default=[], help="another name notes use for this project")
    p.add_argument("--worktrees", action="append", default=[], help="a folder holding this project's git worktrees")
    add("stats", cmd_stats, "project")
    p = add("sync", cmd_sync, "project", **{"project": {"nargs": "?", "default": ""}})
    p.add_argument("--ai", action="store_true", help="also let the model compress the history and write stale summaries")
    p.add_argument("--what", choices=["all", "content", "history"], default="all", help="with --ai: the file summaries, the history compressions, or both")
    p.add_argument("--max-calls", type=int, default=0)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--model", default="")
    p = add("wake", cmd_wake, "project")
    p.add_argument("--budget", type=int, default=20)
    add("zoom", cmd_zoom, "project", "id")
    add("node", cmd_node, "project", "path")
    p = add("find", cmd_find, "project", "question")
    p.add_argument("--notes", type=int, default=8)
    p.add_argument("--semantic", action="store_true", help="search global project memories by meaning, keeping file checks")
    p.add_argument("--model", default="")
    p = add("verify", cmd_verify, "project")
    p.add_argument("--limit", type=int, default=30)
    p = add("history", cmd_history, "project")
    p.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except KeyError as error:
        die(str(error.args[0]))
