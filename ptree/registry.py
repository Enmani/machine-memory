"""Which projects have an index, and where each one keeps its files.

A project is indexed only once it is registered, so sending a project's file outlines to a model is a choice
made per project. Everything for a project lives under ~/.optmem/projects/<name>/ and can be rebuilt from the
project's files and the global memory.
"""
import json
import os
import tempfile
import time

HOME = os.path.expanduser(os.environ.get("PTREE_HOME", "~/.optmem/projects"))


def forward(path):
    return os.path.abspath(path).replace("\\", "/")


def registry_file():
    return os.path.join(HOME, "registry.json")


def load():
    if not os.path.exists(registry_file()):
        return {}
    with open(registry_file(), encoding="utf-8") as handle:
        return json.load(handle)


def save(registry):
    os.makedirs(HOME, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=HOME, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=1)
    os.replace(temporary, registry_file())


def register(name, root, aliases=(), worktrees=()):
    """Add or update a project. `aliases` are other names notes may use for it (a worktree folder, an old name)."""
    root = forward(root)
    if not os.path.isdir(root):
        raise ValueError("%s is not a folder" % root)
    registry = load()
    entry = registry.get(name, {})
    registry[name] = {"root": root, "aliases": sorted(set(aliases) | set(entry.get("aliases", []))),
                      "worktrees": sorted({forward(w) for w in worktrees} | set(entry.get("worktrees", []))),
                      "added": entry.get("added") or time.strftime("%Y-%m-%d")}
    save(registry)
    return registry[name]


def get(name):
    """The registry entry for a project, matching its name without regard to case."""
    for key, entry in load().items():
        if key.casefold() == name.casefold():
            return key, entry
    raise KeyError("No project %r. Registered: %s" % (name, ", ".join(load()) or "none"))


def home(name):
    return os.path.join(HOME, name)


def names_of(name, entry):
    """Every tag a note may carry for this project, lower-cased."""
    return {name.casefold(), *(alias.casefold() for alias in entry.get("aliases", []))}
