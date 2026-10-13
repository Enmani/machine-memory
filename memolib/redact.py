"""Scrub secrets before any chat text leaves this machine for a summarising model."""
import re

PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[private key]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), "[key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "[jwt]"),
    (re.compile(r"(?i)\b(bearer|authorization:?)\s+[A-Za-z0-9._\-]{16,}"), r"\1 [token]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|pwd|密码|密钥)\b(\s*[:=：]\s*)[\"']?[^\s\"',;]{6,}"), r"\1\2[redacted]"),
    # A long opaque string that mixes letters and digits and is not part of a path or file name: a key or hash, not a slug.
    (re.compile(r"(?<![/\.\w-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{48,}={0,2}(?![/\.\w-])"), "[blob]"),
]


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text
