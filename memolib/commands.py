"""CLI boundary for optional semantic retrieval and source-preserving relationships."""
import argparse
import json

from . import store as S
from .knowledge import Catalog, KINDS

NAMES = {"search", "inspect", "relate", "unlink", "review", "knowledge-status"}


def engine_for(args):
    from .engine import Engine
    engine = Engine(model=args.model, concurrent=1, timeout=180, retries=0)
    ok, detail = engine.available()
    if not ok:
        raise ValueError("Semantic processing needs the existing omp CLI: %s. Keyword recall still works." % detail)
    return engine


def show_note(catalog, row):
    print("#%d %s %s" % (row["id"], row["date"], row["text"]))
    for annotation in catalog.annotations(row["text"]):
        print("  " + annotation)
    sources = catalog.sources(row["text"])
    if len(sources) > 1:
        print("  %d source observations; %s inspect %d shows all." % (len(sources), S.ME, row["id"]))


def run(command, words):
    parser = argparse.ArgumentParser(prog="%s %s" % (S.ME, command))
    if command == "search":
        parser.add_argument("question")
        parser.add_argument("--project", action="append", default=[])
        parser.add_argument("--limit", type=int, default=8)
        parser.add_argument("--max-calls", type=int, default=8)
        parser.add_argument("--history", action="store_true", help="include superseded decisions")
    elif command in {"relate", "unlink"}:
        parser.add_argument("newer", type=int)
        parser.add_argument("older", type=int)
        parser.add_argument("kind", choices=sorted(KINDS))
        if command == "relate":
            parser.add_argument("--reason", required=True)
    elif command == "inspect":
        parser.add_argument("id", type=int)
    elif command == "review":
        parser.add_argument("--ids", type=int, nargs="+", default=None)
        parser.add_argument("--limit", type=int, default=24)
        parser.add_argument("--apply", action="store_true", help="save validated relationships; preview by default")
    if command in {"search", "review"}:
        parser.add_argument("--model", default="")
        parser.add_argument("--json", action="store_true")
    args = parser.parse_args(words)
    with Catalog(S.store()) as catalog:
        with catalog.transaction():
            if command == "relate":
                catalog.relate(args.newer, args.older, args.kind, args.reason)
                print("#%d %s #%d saved; original memories retained." % (args.newer, args.kind, args.older))
                return
            if command == "unlink":
                newer, older = catalog.note(args.newer), catalog.note(args.older)
                edge = catalog.db.execute("SELECT origin FROM relations WHERE newer=? AND older=? AND kind=?",
                                          (newer["text"], older["text"], args.kind)).fetchone()
                if edge is None:
                    raise ValueError("No such relationship.")
                if edge[0] == "exact":
                    raise ValueError("Exact-content grouping is derived from the originals, not a model relation.")
                catalog.db.execute("DELETE FROM relations WHERE newer=? AND older=? AND kind=?",
                                   (newer["text"], older["text"], args.kind))
                print("Relationship removed; original memories retained.")
                return
        # Model work never holds the memory lock.
        if command == "search":
            from .semantic import search
            result = search(catalog, engine_for(args), args.question,
                            {p.casefold() for p in args.project}, args.limit, args.max_calls, args.history)
            with catalog.transaction():
                for row in result["hits"]:
                    if catalog.note(row["id"])["text"] != row["text"]:
                        raise ValueError("The log changed during search. Retry against current IDs.")
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                for row in result["hits"]:
                    show_note(catalog, row)
                print("%d results; %d model calls. Tree-guided search can miss details omitted by summaries."
                      % (len(result["hits"]), result["calls"]))
                if result["limited"]:
                    print("Call budget reached before all selected branches were read; results are partial.")
        elif command == "review":
            from .review import review
            result = review(catalog, engine_for(args), args.ids, args.limit, args.apply)
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                for relation in result["relations"]:
                    print("#%d %s #%d: %s" % (relation["newer"], relation["kind"], relation["older"], relation["reason"]))
                print("%d memories reviewed, %d relations %s, %d model calls." %
                      (result["reviewed"], len(result["relations"]), "saved" if args.apply else "proposed", result["calls"]))
        elif command == "inspect":
            row = catalog.note(args.id)
            show_note(catalog, row)
            print("Source observations (original dates, text and conversation references):")
            for source in catalog.sources(row["text"]):
                print("  %s %s %s" % ("#%d" % source["id"] if source["id"] is not None else "observation",
                                       source["date"], source["text"]))
        elif command == "knowledge-status":
            print("Sidecar: %s" % S.pretty(catalog.path))
            for table in ("locations", "observations", "relations"):
                print("%s: %d" % (table, catalog.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]))
            print("pending review: %d" % sum(not r["reviewed"] for r in catalog.rows()))
