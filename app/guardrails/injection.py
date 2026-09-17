"""Detection of prompt-injection attempts in retrieved text.

This is defence in depth, not the defence. A ticket that tells the model to print every salary
already fails three times before it reaches here: retrieval only returns chunks the caller's
groups allow, the salary rows live behind row-level security, and `app.generation.prompt` wraps
every chunk in an escaped element the text cannot break out of. Pattern matching adds what those
cannot — a name for what was attempted, so it can be counted, alerted on and shown in a trace.

Detection therefore tags rather than blocks. A flagged chunk is still passed to the model, with
the flag stated in its source element, because dropping it would let anyone who can file a
ticket delete a document from someone else's search results.
"""

import re
from typing import Final

INJECTION_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    # "ignore all previous instructions", "disregard the above rules"
    "override_instructions": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[\w\s,]{0,30}?"
        r"\b(?:previous|prior|above|earlier|initial|all|any|your)\b[\w\s,]{0,30}?"
        r"\b(?:instruction|prompt|rule|direction|guideline|polic)",
        re.IGNORECASE,
    ),
    # Text addressed at the model rather than at a human reader.
    "addresses_the_model": re.compile(
        r"\b(?:note|message|instruction)s?\s+to\s+(?:any\s+|the\s+)?"
        r"(?:ai|llm|assistant|model|chatbot|bot)\b",
        re.IGNORECASE,
    ),
    # "you are now a...", "act as an unrestricted...". "act as" only counts as an imperative
    # or addressed to the reader: "managers act as approvers" is ordinary policy prose.
    "role_change": re.compile(
        r"\b(?:you\s+are\s+now|pretend\s+to\s+be|from\s+now\s+on,?\s+you)\b"
        r"|(?:^|[.!?]\s+|\byou\s+(?:must|should|shall|will)\s+)act\s+as\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    # Attempts to forge the prompt's own structure or sentinels.
    "forged_markup": re.compile(
        r"</?(?:source|sources|system|question)\b|\bINSUFFICIENT_EVIDENCE\b", re.IGNORECASE
    ),
    # "reveal the system prompt", "print your instructions"
    "exfiltrate_prompt": re.compile(
        r"\b(?:reveal|repeat|print|show|output|disclose)\b[\w\s]{0,20}?"
        r"\b(?:system\s+prompt|your\s+(?:instruction|prompt|rule))",
        re.IGNORECASE,
    ),
}


def detect_injection(text: str) -> list[str]:
    """Sorted names of the injection patterns present in `text`; empty when it looks ordinary."""
    return sorted(name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(text))
