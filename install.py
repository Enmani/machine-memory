#!/usr/bin/env python3
"""python install.py [--instructions | --remove-instructions | --schedule | --unschedule]

With no option: put `memo` where every agent can find it, create the memory, and write the block an agent needs
(integration/AGENTS-block.md). It changes nothing outside ~/.optmem and this folder.

--instructions         put that block at the top of each agent's global instruction file (Codex, Claude Code,
                       oh-my-pi), between markers, after saving a copy of the original. Safe to repeat: it
                       replaces its own block and nothing else.
--remove-instructions  take the block out again.
--schedule             run `harvest.py sync` every 3 hours (a Windows scheduled task, only while logged on).
--unschedule           remove that task.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~/.optmem")
MEMO = os.path.join(HOME, "memo")
MARK = "<!-- machine-memory:begin"
BEGIN = MARK + " (managed by machine-memory/install.py; edit there) -->"
END = "<!-- machine-memory:end -->"
TASK = "MachineMemorySync"

# The agents on this machine and the file each reads as its global instructions.
TARGETS = {
    "Codex": "~/.codex/AGENTS.md",
    "Claude Code": "~/.claude/CLAUDE.md",
    "oh-my-pi": "~/.omp/agent/AGENTS.md",
}


def forward(path):
    return os.path.abspath(path).replace("\\", "/")


# What the agents need to know beyond OptMem's own block: this machine has older binary trees, and the memory
# points at them instead of copying them.
SIBLINGS = """
### Papers, research, projects, and where a note's pointers lead

The memory records what was *done*. Papers, the PhD research and each project already live in binary trees of
their own; do not copy their content into notes, point at it:
- Papers (the archive in D:/papers, indexed by PaperTree): `python D:/paper-tree/paper_tree.py wake`,
  `search "<question>"`, `zoom <path>`, `paper <id>`. A note's `paper:<id>` is that paper's PaperTree id.
- PhD research map: `node {research}/tools/research-map/tree-cli.mjs wake`, `zoom <id>`, `search <words>`,
  `node <id>`. A note's `node:K3` is that research node.
- Projects: `python {ptree} projects` lists the indexed ones. For one project, `wake <project>` is what exists
  now as a small map, `zoom <project> <id>` opens one level, `node <project> <path>` shows a file or folder and the
  notes that mention it, and `history <project> wake|zoom|recall` is that project's own memory (the notes tagged
  `[project]`; never `note` there, note into the global memory).
- Everything done about one paper or node: `{memo} recall "paper:12"` or `{memo} recall "node:K3"`.

### Writing a note

Start every note with `[project]` (the project's folder name, `general` if none), say what was done or decided
and where the result is, and end with `paper:<id>` or `node:<id>` only when it truly concerns that paper or node.
One event, one note, in its final state.

### Search by meaning, duplicate sources, and current decisions

When the wording is uncertain, use `{memo} search "<question>" --project <project>`.
It uses the existing omp model on demand; it does not load a separate model or start a service.
For an indexed project, `python {ptree} find <project> "<question>" --semantic` also checks the matching
notes against the project's files. Exact keywords still use `recall` and ordinary `ptree find`.

Identical content is grouped across conversations, with every source retained. `{memo} inspect <id>`
shows the original text, dates, conversation references and decision relationships. Reopen a source
with `python {harvest} show <reference>` before treating a summary as evidence.

After an adopted decision changes, record the actual change explicitly, then use
`{memo} relate <new-id> <old-id> replaces --reason "<evidence for the adopted change>"`.
Use `duplicate` only for the same fact/event, `enriches` when both remain valid, and `challenges` for
an unresolved disagreement. A proposal is not an adopted decision. Relations preserve the originals.
`search` follows replacement links to current decisions; `search --history` keeps superseded ones.
The ordinary binary-tree `wake` is historical context, so check current relationships before reusing
an old decision. `{memo} review` previews pending links; `review --apply` saves validated ones.
The existing three-hour sync reviews at most 24 pending memories per run. No new startup item is added.

### When the user asks what they did before

"What did I do about X in project Y?" Do not ask for details. If Y is indexed, run
`python {ptree} find Y "<words about X>"`: it answers in one go with where X is now (files) and what happened
(Y's notes, oldest first). The files are the truth about the present; the notes are the truth about why and when.
A note marked `!` names a file that is gone or changed since, so check it against the file before relying on it.
If Y is not indexed, `{memo} recall "Y.*X"` (every note starts with `[project]`; `recall "Y"` lists the project),
then `{memo} zoom <a-b>` to open a range. When the exact words matter, a note's trailing `[cx:019e7e85]` names the
original conversation: `python {harvest} show cx:019e7e85 --grep "<word>"`.
"""


def block_text():
    standard = subprocess.run([sys.executable, MEMO, "init"], capture_output=True, text=True, encoding="utf-8")
    if standard.returncode:
        sys.exit(standard.stderr)
    base = standard.stdout.split("Paste this", 1)[1].split("\n\n", 1)[1].rstrip()
    return base + "\n" + SIBLINGS.format(
        memo="python " + forward(MEMO), harvest=forward(os.path.join(HERE, "harvest.py")), ptree=forward(os.path.join(HERE, "ptree.py")),
        research=forward(os.path.expanduser("~/Projects/phd-research")))


def install_tool():
    os.makedirs(HOME, exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "memo"), MEMO)
    shutil.copytree(os.path.join(HERE, "memolib"), os.path.join(HOME, "memolib"),
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    block = block_text()
    os.makedirs(os.path.join(HERE, "integration"), exist_ok=True)
    with open(os.path.join(HERE, "integration", "AGENTS-block.md"), "w", encoding="utf-8") as handle:
        handle.write(block)
    return block


def with_block(text, block):
    """`text` with the managed block at the top, replacing an earlier one if there is one."""
    newline = "\r\n" if "\r\n" in text else "\n"
    managed = newline.join([BEGIN, *block.split("\n"), END])
    if MARK in text and END in text:
        return text[:text.index(MARK)] + managed + text[text.index(END) + len(END):]
    return managed + newline + newline + text


def without_block(text):
    if MARK not in text or END not in text:
        return text
    return (text[:text.index(MARK)] + text[text.index(END) + len(END):]).lstrip("\r\n")


def edit_instructions(block, remove=False):
    for agent, raw in TARGETS.items():
        path = os.path.expanduser(raw)
        if not os.path.isdir(os.path.dirname(path)):
            print("%-12s skipped: %s does not exist" % (agent, os.path.dirname(path)))
            continue
        original = open(path, encoding="utf-8", newline="").read() if os.path.exists(path) else ""
        backup = path + ".before-machine-memory"
        if original and not os.path.exists(backup):
            shutil.copyfile(path, backup)
        updated = without_block(original) if remove else with_block(original, block)
        if updated != original:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
        print("%-12s %s %s" % (agent, "block removed from" if remove else "block is in", path))


def schedule(remove=False):
    if os.name != "nt":
        sys.exit("--schedule uses the Windows task scheduler; on this OS add `python harvest.py sync --log` to cron.")
    if remove:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", "Unregister-ScheduledTask -TaskName %s -Confirm:$false" % TASK], capture_output=True, text=True)
        return print(done.stdout.strip() or done.stderr.strip() or "task removed")
    python = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    command = (
        "$a = New-ScheduledTaskAction -Execute '%s' -Argument '\"%s\" sync --log' -WorkingDirectory '%s'; "
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 3); "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3); "
        "Register-ScheduledTask -TaskName %s -Action $a -Trigger $t -Settings $s -Description 'Reads new AI conversations into the machine memory (see machine-memory/README.md)' -Force | Out-Null; "
        "Get-ScheduledTask -TaskName %s | Select-Object TaskName, State | Format-List"
    ) % (python, os.path.join(HERE, "harvest.py"), HERE, TASK, TASK)
    done = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
    print(done.stdout.strip() or done.stderr.strip())
    print("Progress is written to ~/.optmem/harvest/sync.log")


def main():
    option = sys.argv[1] if len(sys.argv) > 1 else ""
    if option in ("--schedule", "--unschedule"):
        return schedule(remove=option == "--unschedule")
    block = install_tool()
    if option == "--instructions":
        edit_instructions(block)
    elif option == "--remove-instructions":
        edit_instructions(block, remove=True)
    elif option:
        sys.exit(__doc__)
    else:
        print("The tool is %s\nThe block for the agents' instruction files is in integration/AGENTS-block.md" % forward(MEMO))


if __name__ == "__main__":
    main()
