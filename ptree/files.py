"""Listing a project's files and reading each one down to a one-line mechanical summary.

Nothing here calls a model. A file's summary is what the file says about itself: a document's title and first
line, a source file's exported names, a JSON file's keys. A model is only asked about directories, from these.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile

SKIP_DIRS = {".git", "node_modules", ".logs", ".obsidian", "dist", "build", ".next", "coverage", "__pycache__", ".venv",
             "venv", ".cache", ".turbo", ".vite", "out", "target"}
BINARY = {"png", "jpg", "jpeg", "gif", "webp", "ico", "bmp", "svgz", "woff", "woff2", "ttf", "otf", "eot", "pdf", "zip", "gz",
          "tar", "7z", "rar", "mp3", "mp4", "mov", "wav", "ogg", "webm", "exe", "dll", "so", "dylib", "bin", "wasm", "pyc",
          "psd", "ai", "blend", "glb", "gltf", "fbx", "obj", "3dm", "dwg", "dxf", "ifc", "sqlite", "db", "map", "lock"}
SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb", ".DS_Store", "Thumbs.db"}
MAX_BYTES = 2_000_000
HEAD = 65536


def extension(path):
    return path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""


def wanted(rel, size=0):
    parts = rel.split("/")
    name = parts[-1]
    if any(p in SKIP_DIRS for p in parts[:-1]) or name in SKIP_NAMES or name.endswith((".min.js", ".min.css")):
        return False
    return extension(name) not in BINARY and size <= MAX_BYTES


def list_files(root):
    """Relative posix paths of the text files worth indexing: the git view when there is one (tracked and
    untracked-but-not-ignored), else a walk."""
    found = []
    try:
        done = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                              capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode == 0 and done.stdout:
            found = [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        pass
    if not found:
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            found += [os.path.relpath(os.path.join(folder, n), root).replace("\\", "/") for n in names]
    out = []
    for rel in sorted(set(found)):
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue  # deleted but still tracked, or a submodule folder
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if wanted(rel, size):
            out.append(rel)
    return out


def read_head(root, rel, limit=HEAD):
    with open(os.path.join(root, rel), "rb") as handle:
        return handle.read(limit).decode("utf-8", "replace")


def sha_of(root, rel):
    digest = hashlib.sha1()
    with open(os.path.join(root, rel), "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


# ------------------------------------------------------------------ mechanical outlines

EXPORT = re.compile(r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|type|interface|enum)\s+([A-Za-z_$][\w$]*)", re.M)
TOP_DEF = re.compile(r"^(?:async\s+)?(?:function\*?|class)\s+([A-Za-z_$][\w$]*)", re.M)
PY_DEF = re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M)
LEADING_COMMENT = re.compile(r"\A\s*(?:/\*\*?\s*([^\n*]{4,}?)\s*(?:\*/|\n)|//+\s*([^\n]{4,}))")
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def markdown_outline(text):
    front = FRONT.match(text)
    meta = {}
    if front:
        for line in front.group(1).splitlines():
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("\"'")
        text = text[front.end():]
    title = meta.get("title") or next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), "")
    lead = meta.get("summary") or meta.get("description") or ""
    if not lead:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", ">", "```", "|", "<", "---", "![", "[!")):
                lead = stripped
                break
    return clip(" — ".join(p for p in (title, lead) if p), 200)


def code_outline(text, rel):
    names = list(dict.fromkeys(EXPORT.findall(text) or (PY_DEF if extension(rel) == "py" else TOP_DEF).findall(text)))[:8]
    note = ""
    if extension(rel) == "py":
        doc = re.match(r'\A\s*(?:#[^\n]*\n)*\s*[ru]?"""\s*([^\n"]{4,})', text)
        note = doc.group(1) if doc else ""
    else:
        comment = LEADING_COMMENT.match(text)
        note = (comment.group(1) or comment.group(2)) if comment else ""
    return clip(" | ".join(p for p in (note, ", ".join(names)) if p), 200)


def json_outline(text):
    try:
        data = json.loads(text)
    except ValueError:
        return "json (large or invalid)"
    if isinstance(data, dict):
        lead = " — ".join(str(data[k]) for k in ("name", "description") if isinstance(data.get(k), str))
        return clip(lead or "keys: " + ", ".join(list(data)[:8]), 200)
    return "json list of %d" % len(data) if isinstance(data, list) else "json"


def outline(rel, head, size=0):
    ext = extension(rel)
    if ext in ("md", "markdown", "mdx", "txt", "rst"):
        return markdown_outline(head)
    if ext in ("ts", "tsx", "js", "jsx", "mjs", "cjs", "py"):
        return code_outline(head, rel)
    if ext == "json" and size <= HEAD:
        return json_outline(head)
    if ext in ("html", "htm"):
        title = re.search(r"<title[^>]*>([^<]{1,120})</title>", head, re.I)
        return clip("html " + (title.group(1) if title else ""), 160)
    return clip("%s file, %d KB" % (ext or "plain", max(1, size // 1024)), 80)


# ------------------------------------------------------------------ a cache so a rescan reads only what changed

class FileCache:
    """rel path -> [size, mtime_ns, sha, outline]. A file whose size and mtime are unchanged is not read again."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                self.data = json.load(handle)

    def refresh(self, root, files):
        """Bring the cache in line with `files`; returns how many files were (re)read."""
        fresh, reread = {}, 0
        for rel in files:
            stat = os.stat(os.path.join(root, rel))
            known = self.data.get(rel)
            if known and known[0] == stat.st_size and known[1] == stat.st_mtime_ns:
                fresh[rel] = known
                continue
            reread += 1
            fresh[rel] = [stat.st_size, stat.st_mtime_ns, sha_of(root, rel), outline(rel, read_head(root, rel), stat.st_size)]
        self.data = fresh
        return reread

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(temporary, self.path)
