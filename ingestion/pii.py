"""Regex PII detection for tagging chunks at ingest and masking answers at serve time.

Detection is deliberately high-recall and cheap: tags drive policy (masking, audit), not
blocking, so a false positive costs a masked token while a miss costs a leak. Salaries are not
tagged here because compensation is protected by ACLs and row-level security, not masking.
"""

import re
from typing import Final

PII_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(
        r"(?<![\w-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?:\s?x\d{1,5})?(?![\w-])"
    ),
    "ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "card": re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)"),
}


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 16:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _matches(kind: str, text: str) -> list[re.Match[str]]:
    found = list(PII_PATTERNS[kind].finditer(text))
    return [m for m in found if _luhn_ok(m.group())] if kind == "card" else found


def detect_pii(text: str) -> list[str]:
    """Sorted PII types present in `text`."""
    return sorted(kind for kind in PII_PATTERNS if _matches(kind, text))


def mask_pii(text: str) -> str:
    for kind in PII_PATTERNS:
        for match in reversed(_matches(kind, text)):
            text = f"{text[: match.start()]}[{kind.upper()}]{text[match.end() :]}"
    return text
