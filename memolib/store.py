"""The memory store: fixed-width log and tree files, locking, sizes, blocks and compressions.

Everything that touches LOG.txt or TREE/ lives here, so the memo command and the harvester share one
implementation of the format and its guarantees (position is identity, one lock, ids assigned inside it).
"""

import heapq
import os
import re
import sys
import time

try:
    import fcntl
except ImportError:  # Windows: msvcrt is used instead
    fcntl = None

# ------------------------------------------------------------------ naming

def pretty(path):
    path = os.path.abspath(path).replace("\\", "/")
    home = os.path.expanduser("~").replace("\\", "/")
    return "~" + path[len(home):] if os.name != "nt" and path.startswith(home + "/") else path

# The command line this tool prints for the user to run; the memo script sets it from its own path.
ME = "memo"

# ------------------------------------------------------------------ sizes

# name: (default, meaning). A memory may override them in its `config` file.
KNOBS = {
    "WAKE_LINES": (96, "the memory context: how many lines wake prints"),
    "ENTRY_CHARS": (280, "the longest one memory may be, in bytes"),
    "PART_CHARS": (20000, "output paging: largest part, in bytes"),
    "PART_LINES": (500, "output paging: largest part, in lines"),
}
WAKE_LINES, ENTRY_CHARS, PART_CHARS, PART_LINES = (KNOBS[k][0] for k in KNOBS)

RAW_MAX = 16     # a block of at most this many memories is compressed from the raw log
LOG_REC = 320    # fixed width: memory i lives at byte i * LOG_REC
TREE_REC = 288   # fixed width: block [k*s, (k+1)*s) lives at byte k * TREE_REC of TREE/<s>

def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)

def plural(n, word):
    if n == 1:
        return "1 " + word
    if word.endswith("y"):
        word = word[:-1] + "ie"
    elif word.endswith(("s", "h", "x")):
        word += "e"
    return "%d %ss" % (n, word)

# ------------------------------------------------------------------ the store

def memory_dir():
    return os.path.expanduser(os.environ.get("MEMORY_DIR") or "~/.optmem/memory")

def store():
    """Open the memory. Only `init` creates it: creating the directory is creating the
    identity, so a typo in MEMORY_DIR must be an error, not a second empty memory."""
    d = memory_dir()
    if not os.path.isdir(d):
        die("No memory at %s.\nTo create one, run: %s init\nTo use an existing one, "
            "point MEMORY_DIR at it." % (pretty(d), ME))
    os.makedirs(os.path.join(d, "TREE"), exist_ok=True)
    if not os.path.exists(log_path(d)):
        open(log_path(d), "a").close()
    return d

def log_path(d):
    return os.path.join(d, "LOG.txt")

def tree_path(d, size):
    return os.path.join(d, "TREE", str(size))

def check_knob(name, value, where=""):
    if not value.isdigit() or int(value) < 1:
        die("%s%s must be a positive whole number, not '%s'." % (where, name, value))
    ceiling = min(TREE_REC - 8, LOG_REC - 40)
    if name == "ENTRY_CHARS" and int(value) > ceiling:
        die("%sENTRY_CHARS is at most %d: a memory has to fit the fixed-width records." % (where, ceiling))
    return int(value)

def overrides(d):
    found, path = {}, os.path.join(d, "config")
    if not os.path.exists(path):
        return found
    for number, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.split("#")[0].strip()
        if "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        name = name.upper()
        where = "%s line %d: " % (pretty(path), number)
        if name not in KNOBS:
            die("%s%s is not a size. Name one of: %s." % (where, name, ", ".join(KNOBS)))
        found[name] = check_knob(name, value, where)
    return found

def apply_config(d):
    for name, value in overrides(d).items():
        globals()[name] = value  # a knob's name is the name of the global it sets

def write_config(d, over):
    lines = ["# Sizes for this memory. A commented line follows the tool's default.",
             "# Edit with `%s config NAME=VALUE`." % ME, ""]
    for name, (default, meaning) in KNOBS.items():
        lines.append("%-2s%-12s = %-6d # %s" % ("" if name in over else "# ", name, over.get(name, default), meaning))
    with open(os.path.join(d, "config"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# ------------------------------------------------------------------ locking

class Lock:
    """An exclusive advisory lock on <memory>/.lock, shared by every process on the machine."""
    def __init__(self, d):
        self.handle = open(os.path.join(d, ".lock"), "a")  # "a", never "w": truncating breaks locks on Windows

    def __enter__(self):
        if fcntl is not None:
            fcntl.flock(self.handle, fcntl.LOCK_EX)
            return self
        import msvcrt
        waited = 0.0
        while True:
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if waited > 30:
                    raise
                time.sleep(min(0.01 + waited * 0.2, 0.25))
                waited += 0.01

    def __exit__(self, *exc):
        if fcntl is None:
            import msvcrt
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.handle.close()

def count(path, width):
    try:
        return os.path.getsize(path) // width
    except FileNotFoundError:
        return 0

def repair(path, width):
    """Drop a partial trailing record left by a crash: it was never acknowledged, and leaving it
    would misalign every later append."""
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return
    if size % width:
        with open(path, "r+b") as f:
            f.truncate(size - size % width)

def pad(text, width):
    raw = text.encode("utf-8")
    if len(raw) > width - 1:
        die("Too long: %d bytes. The record holds %d." % (len(raw), width - 1))
    return raw + b" " * (width - 1 - len(raw)) + b"\n"

# ------------------------------------------------------------------ the log

def log_len(d):
    return count(log_path(d), LOG_REC)

def decode_log(buffer):
    # Slice the BYTES first and decode each record: slicing decoded text would shift every
    # boundary after the first multi-byte character.
    out = []
    for i in range(len(buffer) // LOG_REC):
        head, _, rest = buffer[i * LOG_REC:(i + 1) * LOG_REC].decode("utf-8").rstrip().partition(" ")
        date, _, text = rest.partition(" ")
        out.append((int(head[1:]), date, text))
    return out

def log_slice(d, lo, hi):
    with open(log_path(d), "rb") as f:
        f.seek(lo * LOG_REC)
        return decode_log(f.read((hi - lo) * LOG_REC))

def log_get(d, i):
    return log_slice(d, i, i + 1)[0]

def log_scan(d):
    """Every memory, streamed: a search reads the whole log but must not hold it."""
    with open(log_path(d), "rb") as f:
        while True:
            buffer = f.read(LOG_REC * 4096)
            if not buffer:
                return
            yield from decode_log(buffer)

def log_append(d, items):
    """Append [(date, text)]. The only way LOG.txt changes. Ids are assigned inside the lock so
    two sessions noting together cannot be handed the same id. Returns the first id used."""
    with Lock(d):
        return log_append_locked(d, items)


def log_append_locked(d, items):
    """Append while the caller owns Lock(d), also used by the duplicate-aware writer.

    The durable log is written before the optional knowledge transaction commits.
    An interrupted transaction is recovered by indexing the log on the next access.
    """
    repair(log_path(d), LOG_REC)
    base = log_len(d)
    records = [pad("#%d %s %s" % (base + k, date, text), LOG_REC)
               for k, (date, text) in enumerate(items)]
    with open(log_path(d), "ab") as f:
        f.writelines(records)
        f.flush()
        os.fsync(f.fileno())
    return base

# ------------------------------------------------------------------ the tree

def tree_get(d, lo, hi):
    """The summary of block [lo,hi) in one seek, or None if it is not built yet."""
    size = hi - lo
    try:
        with open(tree_path(d, size), "rb") as f:
            f.seek((lo // size) * TREE_REC)
            record = f.read(TREE_REC)
    except FileNotFoundError:
        return None
    try:
        return record.decode("utf-8").rstrip() or None
    except UnicodeDecodeError:
        die("The summary of #%d-%d is corrupt. Run: %s forget %d-%d" % (lo, hi - 1, ME, lo, hi - 1))

def tree_put(d, lo, hi, text):
    """Write block [lo,hi). Blocks are built in order, so this only appends to one level file."""
    size = hi - lo
    with Lock(d):
        path = tree_path(d, size)
        repair(path, TREE_REC)
        if count(path, TREE_REC) != lo // size:
            return False
        with open(path, "ab") as f:
            f.write(pad(text, TREE_REC))
            f.flush()
            os.fsync(f.fileno())
        return True

def tree_drop(d, lo, hi):
    """Forget block [lo,hi) and everything built on it by truncating each level back to that point.
    Later blocks at those levels go too and are rebuilt; the log is never touched."""
    gone, size = [], hi - lo
    with Lock(d):
        while size <= log_len(d):
            path, k = tree_path(d, size), lo // size
            have = count(path, TREE_REC)
            if have > k:
                gone += [(i * size, (i + 1) * size) for i in range(k, have)]
                with open(path, "r+b") as f:
                    f.truncate(k * TREE_REC)
            size *= 2
    return gone

def tile(T):
    """Tile [0,T) with the largest aligned power-of-two blocks that fit, oldest first."""
    blocks, lo = [], 0
    for bit in range(T.bit_length() - 1, -1, -1):
        if T & (1 << bit):
            blocks.append((lo, lo + (1 << bit)))
            lo += 1 << bit
    return blocks

def cover(T, budget):
    """The blocks `wake` prints: at most `budget` lines, finest near the present.
    Start from the coarsest tiling and keep splitting the block that is largest relative to its
    age, so recent memories stay verbatim and old ones collapse. If everything fits, nothing is
    compressed at all."""
    blocks = tile(T)
    heap = [(-(hi - lo) / (T - lo), -lo, lo, hi) for lo, hi in blocks if hi - lo > 1]
    heapq.heapify(heap)
    while heap and len(blocks) < budget:
        _, _, lo, hi = heapq.heappop(heap)
        mid = (lo + hi) // 2
        blocks.remove((lo, hi))
        for a, b in ((lo, mid), (mid, hi)):
            blocks.append((a, b))
            if b - a > 1:
                heapq.heappush(heap, (-(b - a) / (T - a), -a, a, b))
    return sorted(blocks)

def block_id(text):
    """Parse `<lo>-<hi>` as wake prints it (inclusive at both ends): an aligned power-of-two range."""
    match = re.fullmatch(r"(\d+)-(\d+)", text)
    if not match:
        die("'%s' is not a block id. Copy it from the prompt." % text)
    lo, hi = int(match.group(1)), int(match.group(2)) + 1
    n = hi - lo
    if n < 2 or n & (n - 1) or lo % n:
        die("%s is not a block. Copy the id printed by wake, like 16-31." % text)
    return lo, hi

def check_line(text):
    text = text.strip()
    if not text:
        die("Empty. A memory is one line of text.")
    if "\n" in text or "\r" in text:
        die("%d lines. A memory is one line: merge them, or note them separately." % (text.count("\n") + 1))
    size = len(text.encode("utf-8"))
    if size > ENTRY_CHARS:
        die("Too long: %d bytes, limit %d. Accented and CJK characters cost 2-3 bytes. Compress it further." % (size, ENTRY_CHARS))
    return text

# ------------------------------------------------------------------ naps

def pending(d, T, limit=None):
    """Blocks that can be built and are not, smallest first. Each level file holds a dense prefix,
    so its length says how far that level got: one stat per level, never a scan."""
    todo, size = [], 2
    while size <= T:
        for k in range(count(tree_path(d, size), TREE_REC), T // size):
            todo.append((k * size, (k + 1) * size))
            if limit and len(todo) >= limit:
                return todo
        size *= 2
    return todo

def pending_count(d, T):
    total, size = 0, 2
    while size <= T:
        total += max(0, T // size - count(tree_path(d, size), TREE_REC))
        size *= 2
    return total

def nap_inputs(d, lo, hi):
    """The lines a block is compressed from: the raw memories if there are few, else its two halves."""
    if hi - lo <= RAW_MAX:
        return ["#%d %s %s" % entry for entry in log_slice(d, lo, hi)]
    mid, lines = (lo + hi) // 2, []
    for a, b in ((lo, mid), (mid, hi)):
        summary = tree_get(d, a, b)
        if summary is None:
            die("The summary of #%d-%d is blank. Run: %s forget %d-%d" % (a, b - 1, ME, a, b - 1))
        lines.append("#%d-%d %s" % (a, b - 1, summary))
    return lines

def nap_prompt(d, lo, hi, left):
    body = "\n".join("  " + line for line in nap_inputs(d, lo, hi))
    tail = "" if not left else "\n%s after this one." % ("1 compression remains" if left == 1 else "%d compressions remain" % left)
    return ("Compress memories #%d-%d into one line of at most %d bytes, in the language they are written in.\n"
            "Keep what has lasting effect, drop what does not. Invent nothing.\n\n%s\n%s\n"
            "Run: %s nap %d-%d \"<your line>\"" % (lo, hi - 1, ENTRY_CHARS, body, tail, ME, lo, hi - 1))

def next_nap(d, T):
    todo = pending(d, T, limit=1)
    return nap_prompt(d, *todo[0], pending_count(d, T) - 1) if todo else None
