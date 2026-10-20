from __future__ import annotations

import re
import unicodedata
from datetime import UTC
from difflib import SequenceMatcher

from .domain import CanonicalEvent


_PUNCTUATION = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")
_ALIASES = {
    "ny": "new york",
    "nyc": "new york",
    "la": "los angeles",
    "utd": "united",
    "fc": "",
    "cf": "",
}


def normalize_name(value: str) -> str:
    """Normalize a display name for candidate generation, never settlement proof."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    tokens = _PUNCTUATION.sub(" ", ascii_value.lower()).split()
    expanded: list[str] = []
    for token in tokens:
        replacement = _ALIASES.get(token, token)
        if replacement:
            expanded.extend(replacement.split())
    return _SPACE.sub(" ", " ".join(expanded)).strip()


def identity_key(value: str) -> str:
    """Compare explicit identities without aliases or deletion of name tokens.

    Candidate normalization is intentionally lossy. Settlement proof preserves
    punctuation, accents, and tokens such as FC/CF that can distinguish entities.
    Only Unicode canonical equivalence, case, and whitespace are immaterial.
    """
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def name_similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio()


def candidate_event_score(left: CanonicalEvent, right: CanonicalEvent) -> float:
    """Score event identity only; a high score does not verify market equivalence."""
    if normalize_name(left.sport) != normalize_name(right.sport):
        return 0.0
    league = name_similarity(left.league, right.league)
    home = name_similarity(left.home_team, right.home_team)
    away = name_similarity(left.away_team, right.away_team)
    direct = (home + away) / 2
    reversed_score = (
        name_similarity(left.home_team, right.away_team)
        + name_similarity(left.away_team, right.home_team)
    ) / 2
    teams = max(direct, reversed_score * 0.92)
    seconds = abs((left.scheduled_start.astimezone(UTC) - right.scheduled_start.astimezone(UTC)).total_seconds())
    time_score = max(0.0, 1.0 - (seconds / (6 * 60 * 60)))
    return round(0.25 * league + 0.55 * teams + 0.20 * time_score, 6)


def plausible_event_candidate(
    left: CanonicalEvent,
    right: CanonicalEvent,
    *,
    minimum_score: float = 0.72,
    maximum_start_delta_seconds: int = 6 * 60 * 60,
) -> bool:
    delta = abs((left.scheduled_start - right.scheduled_start).total_seconds())
    return delta <= maximum_start_delta_seconds and candidate_event_score(left, right) >= minimum_score

