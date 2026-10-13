#!/usr/bin/env python3
"""python install.py [--instructions | --remove-instructions | --schedule | --unschedule]

With no option: install memo and its skill, create the memory, and write the small instruction block
(integration/AGENTS-block.md). Skill discovery paths are updated only for existing agent directories.

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


SKILL_TARGETS = {
    "Codex": "~/.codex/skills/machine-memory",
    "Claude Code": "~/.claude/skills/machine-memory",
    "oh-my-pi": "~/.omp/agent/skills/machine-memory",
}


def command(interpreter, path):
    return '%s "%s"' % (interpreter, forward(path))


def install_skill():
    """Publish the skill plus local command paths; resolve shared skill directories once."""
    source = os.path.join(HERE, "skills", "machine-memory")
    master = os.path.join(HOME, "skills", "machine-memory")
    shutil.copytree(source, master, dirs_exist_ok=True)
    commands = [("memo", command("python", MEMO)),
                ("ptree", command("python", os.path.join(HERE, "ptree.py"))),
                ("harvest", command("python", os.path.join(HERE, "harvest.py")))]
    paper = os.path.expanduser(os.environ.get("PAPER_TREE_CLI", "D:/paper-tree/paper_tree.py"))
    research = os.path.expanduser("~/Projects/phd-research/tools/research-map/tree-cli.mjs")
    for name, interpreter, path in (("paper-tree", "python", paper), ("research", "node", research)):
        if os.path.isfile(path):
            commands.append((name, command(interpreter, path)))
    lines = ["# Local commands", "", "Expand each alias into the full command before execution.", "",
             "| Alias | Command |", "| --- | --- |"]
    lines += ["| `%s` | `%s` |" % row for row in commands]
    lines += ["", "Memory files: `%s`. `MEMORY_DIR` can override this store." %
              forward(os.environ.get("MEMORY_DIR") or os.path.join(HOME, "memory")), ""]
    with open(os.path.join(master, "references", "local-paths.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    seen = {os.path.normcase(os.path.realpath(master))}
    for agent, raw in SKILL_TARGETS.items():
        if not os.path.isdir(os.path.dirname(os.path.expanduser(TARGETS[agent]))):
            continue
        destination = os.path.expanduser(raw)
        resolved = os.path.normcase(os.path.realpath(destination))
        if resolved not in seen:
            shutil.copytree(master, destination, dirs_exist_ok=True)
            seen.add(resolved)
        print("%-12s skill is in %s" % (agent, forward(destination)))
    return master


def block_text():
    standard = subprocess.run([sys.executable, MEMO, "init"], capture_output=True, text=True, encoding="utf-8")
    if standard.returncode:
        sys.exit(standard.stderr)
    return standard.stdout.split("Paste this", 1)[1].split("\n\n", 1)[1].rstrip()


def install_tool():
    os.makedirs(HOME, exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "memo"), MEMO)
    shutil.copytree(os.path.join(HERE, "memolib"), os.path.join(HOME, "memolib"),
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    install_skill()
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
        original = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as handle:
                original = handle.read()
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
