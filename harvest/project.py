"""Which project a thread belongs to, judged from the folder it was started in."""
import os
import re


def project_of(cwd: str) -> str:
    """A short project name from a working directory. The model may still choose a better one from
    the conversation; this is the hint it is given and the fallback."""
    if not cwd:
        return "general"
    parts = [p for p in re.split(r"[\\/]+", cwd) if p]
    lower = [p.lower() for p in parts]
    if "projects" in lower and lower.index("projects") + 1 < len(parts):
        name = parts[lower.index("projects") + 1]
        return name[:-5] if name.lower().endswith("-work") and len(name) > 5 else name
    if "codex" in lower:  # Documents\Codex\<date>\<slug>[\outputs\...]: a chat's own folder
        after = parts[lower.index("codex") + 1:]
        if len(after) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", after[0]):
            return after[1]
    if any("scratch" in p for p in lower):
        return "claude-chat"
    home = os.path.basename(os.path.expanduser("~")).lower()
    if parts and parts[-1].lower() in (home, "documents", "desktop"):
        return "general"
    return parts[-1]
