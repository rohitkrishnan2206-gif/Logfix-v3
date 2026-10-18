"""Strip secrets and obvious PII before text is stored, shown to the LLM, or returned
from code tools. Deliberately conservative: it keeps IPs and hostnames because they
are needed for troubleshooting. Regex redaction is a safety net, not a guarantee."""
from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "SECRET",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)\b(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
        ),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def redact_counted(text: str | None) -> tuple[str, int]:
    """Redact and also report how many substitutions were made."""
    if not text:
        return text or "", 0
    out, n = text, 0
    for label, pat in _PATTERNS:
        if label == "SECRET":
            out, k = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED_SECRET]", out)
            n += k
        elif label == "CARD":
            hits = 0

            def _card(m: re.Match) -> str:
                nonlocal hits
                digits = re.sub(r"\D", "", m.group(0))
                if 13 <= len(digits) <= 16 and _luhn_ok(digits):
                    hits += 1
                    return "[REDACTED_CARD]"
                return m.group(0)

            out = pat.sub(_card, out)
            n += hits
        else:
            out, k = pat.subn(f"[REDACTED_{label}]", out)
            n += k
    return out, n


def redact(text: str | None) -> str:
    return redact_counted(text)[0]
