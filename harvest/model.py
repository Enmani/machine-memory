"""What an adapter hands the rest of the pipeline: threads made of plain-text turns."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

CODES = {"codex": "cx", "claude": "cc", "omp": "om"}
SOURCE_BY_CODE = {code: source for source, code in CODES.items()}


@dataclass
class Turn:
    role: str            # "user" or "assistant"
    text: str
    at: str              # ISO timestamp as written in the log
    final: bool = False  # an assistant turn the log marks as the closing answer


@dataclass
class Thread:
    source: str
    id: str
    path: str
    title: str = ""
    cwd: str = ""
    started: str = ""
    ended: str = ""
    subagent: bool = False
    parent: str | None = None
    turns: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        """A short, stable pointer written into every memory so the original can be reopened."""
        return f"{CODES.get(self.source, self.source[:2])}:{self.id[:8]}"

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"


def local_date(stamp: str) -> str:
    """The calendar date of an ISO timestamp, in the machine's own time zone."""
    if not stamp:
        return ""
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().date().isoformat()
