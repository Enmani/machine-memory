"""Where the two trees meet: a memory that names a file, and the file it names.

Notes written by agents say where results are (`docs/workflows/x.md`, `D:/AI-Video/...`). Matching those paths to the
project's files lets the project tree show why and when a file changed, and lets the memory be checked against the
files: a note that names a file that is gone, or that changed after the note was written, is flagged. Matching is by
path, never by a model.
"""
import os
import re
import subprocess

EXTENSIONS = r"(?:md|ts|tsx|json|cjs|mjs|js|jsx|py|css|html|yml|yaml|toml|sh|ps1|cmd|txt|sql|svg)"
WORD = r"[\w\u4e00-\u9fff.\-]"
MENTION = re.compile("|".join([
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s,;，；)）\]\"'<>]+",                      # an absolute Windows path
    r"(?:%s+/)+%s+/?" % (WORD, WORD),                              # a relative path with at least one slash
    r"[\w一-鿿\-]+(?:\.[\w\-]+)*\.%s(?![A-Za-z0-9_])" % EXTENSIONS,      # a bare file name with a known extension
]))
TAIL_WORDS = re.compile(r"^(.*?\.%s)[一-鿿].*$" % EXTENSIONS)
LEADING_WORDS = re.compile(r"^[一-鿿]+(?=[A-Za-z0-9._~])")
TRAILING = ".,;:)）。，、\"'…"


def mentions(text):
    """Path-looking strings in a note, cleaned of punctuation."""
    found = []
    for match in MENTION.finditer(text):
        if text[max(0, match.start() - 3):match.start()].endswith("//"):
            continue  # the host and path of a URL
        cleaned =TAIL_WORDS.sub(chr(92) + "1", match.group(0).replace("\\", "/")).rstrip(TRAILING)
        if len(cleaned) >= 4 and not cleaned.startswith(("http", "//")) and cleaned not in found:
            found.append(cleaned)
    return found


def git_worktrees(root):
    """Paths of the project's git worktrees, as git lists them."""
    try:
        done = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"], capture_output=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return [l[9:].strip() for l in done.stdout.decode("utf-8", "replace").splitlines() if l.startswith("worktree ")]


class Resolver:
    """Maps a mention to a file or folder of one project: exactly, by unique path suffix, or by unique file name."""

    def __init__(self, root, files, worktrees=()):
        # `worktrees` are folders that hold worktrees; git's own list of worktrees is added to them
        paths = [os.path.join(folder, n) for folder in worktrees if os.path.isdir(folder) for n in sorted(os.listdir(folder))]
        paths += git_worktrees(root)
        self.worktrees = [(os.path.basename(os.path.dirname(w)) if os.path.basename(w) == os.path.basename(root) else os.path.basename(w), w)
                          for w in dict.fromkeys(paths) if os.path.isdir(w) and os.path.abspath(w).replace("\\", "/").casefold() != root.replace("\\", "/").rstrip("/").casefold()]
        self.root_path = root.replace("\\", "/").rstrip("/")
        self.root = self.root_path.casefold()
        self.files = set(files)
        self.dirs = {"/".join(f.split("/")[:i]) for f in files for i in range(1, len(f.split("/")))}
        self.names = {}
        for f in files:
            self.names.setdefault(os.path.basename(f), []).append(f)
        self.tops = {f.split("/")[0] for f in files if "/" in f}

    def relative(self, mention):
        low = mention.casefold()
        if low.startswith(self.root + "/"):
            return mention[len(self.root) + 1:].strip("/")
        return None if re.match(r"[A-Za-z]:/", mention) else (mention[2:] if mention.startswith("./") else mention)

    def resolve(self, mention):
        """A mention may have words glued on in front (保存于docs/x.md): both readings are tried, the whole first."""
        readings = [mention]
        stripped = LEADING_WORDS.sub("", mention)
        if stripped != mention and stripped:
            readings.append(stripped)
        results = [self._resolve_one(r) for r in readings]
        return next((r for r in results if r and r[0] != "missing"), next((r for r in reversed(results) if r), None))

    def _resolve_one(self, mention):
        """('file'|'dir', path) if it names something that exists, ('worktree', path, name) if it exists only in
        a worktree, ('missing', path) if it should exist here and does not, else None (not about this project)."""
        rel = self.relative(mention)
        if rel is None:
            return None
        rel = rel.rstrip("/")
        if rel in self.files:
            return "file", rel
        if rel in self.dirs:
            return "dir", rel
        if "/" in rel:
            tails = [f for f in self.files if f.endswith("/" + rel)]
            if len(tails) == 1:
                return "file", tails[0]
            folders = [d for d in self.dirs if d.endswith("/" + rel)]
            if len(folders) == 1:
                return "dir", folders[0]
        elif len(self.names.get(rel, [])) == 1:
            return "file", self.names[rel][0]
        for name, path in self.worktrees:  # work not merged yet lives beside the project, not in it
            if os.path.exists(os.path.join(path, rel)):
                return "worktree", rel, name
        full = os.path.join(self.root_path, rel)
        if os.path.isfile(full):  # ignored by git, so not indexed, but it is there
            return "file", rel
        if os.path.isdir(full):
            return "dir", rel
        # Only a path that is anchored in this project can be called missing: an absolute path under it, or one
        # starting at one of its top-level folders. A bare `spatial-quality/x.md` could belong to anywhere.
        absolute = mention.casefold().startswith(self.root + "/")
        anchored = absolute or ("/" in rel and os.path.isdir(os.path.join(self.root_path, rel.split("/")[0])))
        return ("missing", rel) if anchored else None


def reverse_index(notes, resolver):
    """path -> [note ids] for every file or folder a note resolves to."""
    index = {}
    for note_id, _, text in notes:
        for mention in mentions(text):
            hit = resolver.resolve(mention)
            if hit and hit[0] in ("file", "dir") and note_id not in index.setdefault(hit[1], []):
                index[hit[1]].append(note_id)
    return index


def notes_about(index, path, kind):
    """Notes that name this path, or (for a folder) anything inside it."""
    if kind == "file":
        return index.get(path, [])
    prefix = path.rstrip("/") + "/" if path else ""
    return sorted({n for p, ids in index.items() if p == path or p.startswith(prefix) for n in ids})


def last_changes(root):
    """path -> date (YYYY-MM-DD) of the newest commit that touched it, from one pass over the history."""
    try:
        done = subprocess.run(["git", "-C", root, "log", "--format=\x01%cs", "--name-only", "--no-renames", "-n", "20000"],
                              capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return {}
    changes, date = {}, ""
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("\x01"):
            date = line[1:]
        elif line.strip():
            changes.setdefault(line.strip(), date)
    return changes


def verify(notes, resolver, changes):
    """[(note id, date, text, [issues])] for notes whose files are gone or changed after the note was written."""
    flagged = []
    for note_id, date, text in notes:
        issues = []
        for mention in mentions(text):
            hit = resolver.resolve(mention)
            if not hit:
                continue
            if hit[0] == "worktree":
                issues.append("%s is only in worktree %s (not merged into the project folder)" % (hit[1], hit[2]))
            elif hit[0] == "missing":
                issues.append("%s no longer exists here" % hit[1])
            elif hit[0] == "file" and changes.get(hit[1], "") > date:
                issues.append("%s changed %s" % (hit[1], changes[hit[1]]))
        if issues:
            flagged.append((note_id, date, text, issues))
    return flagged
