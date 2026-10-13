#!/usr/bin/env python3
"""The test that matters: a fresh agent, told nothing but the memory block, asked about past work.

    python tests/acceptance.py "帮我找下 Archist 项目里我做过的 keeper 控制台开发" [--expect keeper] [--model ...]

It starts the local oh-my-pi agent (a real agent with a shell, in an empty folder, with no project files
and no history), appends integration/AGENTS-block.md to its system prompt exactly as an instruction file
would, asks the question, and reports what it ran and how long it took. A run passes when the agent
reached the notes through memo, took few steps, and its answer contains what `--expect` names.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCK = os.path.join(HERE, "integration", "AGENTS-block.md")


def run(question, model="", timeout=600, installed=False):
    work = tempfile.mkdtemp(prefix="memo-acceptance-")
    try:
        args = ["omp", "-p", "--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-title", "--no-lsp",
                "--auto-approve", "--tools", "bash", "--thinking", "low", "--cwd", work, "--mode", "json",
                ]
        if not installed:
            args += ["--append-system-prompt", BLOCK]
        if model:
            args += ["--model", model]
        args.append(question)
        started = time.time()
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=work)
        return done, time.time() - started
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summarize(stdout):
    """Shell commands the agent ran (each tool call once), and its final words, from omp's JSON events."""
    commands, final, seen = [], "", set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        call_id, args = data.get("toolCallId"), data.get("args")
        if call_id and isinstance(args, dict) and args.get("command") and call_id not in seen:
            seen.add(call_id)
            commands.append(args["command"])
        message = event.get("message") if isinstance(event.get("message"), dict) else None
        if message and message.get("role") == "assistant":
            parts = [b.get("text", "") for b in message.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
            if any(parts):
                final = "\n".join(p for p in parts if p)
    return commands, final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--expect", action="append", default=[], help="text the answer must contain (repeatable)")
    parser.add_argument("--max-commands", type=int, default=8)
    parser.add_argument("--model", default="")
    parser.add_argument("--installed", action="store_true", help="rely on the global instruction file (install.py --instructions) instead of appending the block")
    args = parser.parse_args()
    if not os.path.exists(BLOCK):
        sys.exit("Run install.py first: integration/AGENTS-block.md is missing.")
    done, seconds = run(args.question, args.model, installed=args.installed)
    commands, final = summarize(done.stdout)
    used_memo = [c for c in commands if ".optmem/memo" in c or "harvest.py" in c]
    print("question: %s\nseconds: %.0f   commands: %d   through memo: %d" % (args.question, seconds, len(commands), len(used_memo)))
    for command in commands:
        print("  $ " + command[:200].replace("\n", " "))
    print("\nanswer:\n%s\n" % (final or done.stdout[-1500:] or done.stderr[-500:]))
    verdict = [("reached the notes through memo", bool(used_memo)), ("%d commands or fewer" % args.max_commands, len(commands) <= args.max_commands)]
    verdict += [("answer contains %r" % word, word.lower() in final.lower()) for word in args.expect]
    for name, ok in verdict:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    sys.exit(0 if all(ok for _, ok in verdict) else 1)


if __name__ == "__main__":
    main()
