"""Turn cloud-specific log records into one shape: severity, service, fingerprint."""
from __future__ import annotations

import hashlib
import re

SEVERITIES = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

_LEVEL_WORDS = {
    "CRITICAL": "CRITICAL", "FATAL": "CRITICAL", "PANIC": "CRITICAL", "EMERG": "CRITICAL",
    "EMERGENCY": "CRITICAL", "ALERT": "CRITICAL", "CRIT": "CRITICAL",
    "ERROR": "HIGH", "ERR": "HIGH", "SEVERE": "HIGH",
    "WARN": "MEDIUM", "WARNING": "MEDIUM",
    "NOTICE": "LOW", "DEBUG": "LOW", "TRACE": "LOW",
    "INFO": "INFO", "INFORMATION": "INFO", "INFORMATIONAL": "INFO",
}
_LEVEL_RE = re.compile(r"\b(" + "|".join(sorted(_LEVEL_WORDS, key=len, reverse=True)) + r")\b", re.I)
_HIGH_HINTS = re.compile(
    r"(exception|\w*error\b|outofmemory|enospc|no space left|traceback|failed|failure|timed? ?out|timeout|exhausted|oomkilled|out of memory|"
    r"refused|unreachable|throttl|denied|panic|segfault|deadlock|unhealthy|crashloop)", re.I
)
_BRACKET_RE = re.compile(r"\[([A-Za-z0-9_.\-/:]{2,64})\]")


def rank(sev: str) -> int:
    return SEV_RANK.get(sev, 0)


def max_severity(a: str, b: str) -> str:
    return a if rank(a) >= rank(b) else b


def map_level(level: str | None) -> str | None:
    if not level:
        return None
    return _LEVEL_WORDS.get(str(level).strip().upper())


def parse_severity(message: str, hint: str | None = None) -> str:
    """Prefer the cloud's own level (hint). Otherwise look for a level word near the
    start of the message, then fall back to failure keywords."""
    mapped = map_level(hint)
    if mapped:
        return mapped
    m = _LEVEL_RE.search(message[:60])
    if m:
        return _LEVEL_WORDS[m.group(1).upper()]
    if _HIGH_HINTS.search(message):
        return "HIGH"
    return "INFO"


def extract_service(message: str, default: str | None = None) -> str:
    for m in _BRACKET_RE.finditer(message[:160]):
        token = m.group(1)
        if token.upper() not in _LEVEL_WORDS:
            return token
    return default or "unknown"


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-f]{8,}\b", re.I)
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def fingerprint(service: str, message: str) -> str:
    """Stable id for 'the same kind of error': numbers, ids and addresses are masked."""
    s = message.lower()
    s = _UUID_RE.sub("<id>", s)
    s = _IP_RE.sub("<ip>", s)
    s = _HEX_RE.sub("<hex>", s)
    s = _NUM_RE.sub("<n>", s)
    s = re.sub(r"\s+", " ", s).strip()[:200]
    return hashlib.sha256(f"{(service or '').lower()}|{s}".encode()).hexdigest()[:16]
