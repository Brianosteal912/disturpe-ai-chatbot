from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=32)
def _name_pattern(names: tuple[str, ...]) -> re.Pattern[str] | None:
    alternatives = []
    configured_names = {item.casefold().strip() for item in names if item.strip()}
    for name in sorted(configured_names, key=len, reverse=True):
        alternatives.append(re.escape(name).replace(r"\ ", r"\s+"))
    if not alternatives:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.UNICODE)


def calls_bot(text: str, names: tuple[str, ...]) -> bool:
    """Return whether text calls one of the configured bot names."""
    pattern = _name_pattern(names)
    return pattern is not None and pattern.search(text.casefold()) is not None


def split_discord_message(text: str, limit: int = 1900) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]
