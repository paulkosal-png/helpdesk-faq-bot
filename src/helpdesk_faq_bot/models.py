"""Shared data types."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SPLIT_RE = re.compile(r"[;\n]+")


def split_alternates(raw: str) -> list[str]:
    """Alternate phrases are separated by semicolons or line breaks."""
    return [p.strip() for p in _SPLIT_RE.split(raw or "") if p.strip()]


@dataclass(frozen=True)
class FAQEntry:
    question: str
    answer: str
    alternate_phrases: tuple[str, ...] = ()
    keyword_rule: str = ""
    category: str = ""

    @classmethod
    def from_row(cls, row: dict[str, str]) -> FAQEntry:
        """Build from a CSV / Sheet row. Header names are case-insensitive."""
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        return cls(
            question=r.get("question", ""),
            answer=r.get("answer", ""),
            alternate_phrases=tuple(split_alternates(r.get("alternate phrases", ""))),
            keyword_rule=r.get("keyword rules", r.get("keyword rule", "")),
            category=r.get("category", ""),
        )


@dataclass
class Source:
    title: str
    location: str  # e.g. "FAQ" or "docs/sample_kb/vpn.md"
    score: float = 0.0


@dataclass
class BotAnswer:
    """What the bot will tell the user."""

    outcome: str  # exact | phrase | keyword | ambiguous | rag | unanswered
    text: str
    candidates: list[FAQEntry] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    latency_seconds: float = 0.0

    @property
    def answered(self) -> bool:
        return self.outcome in ("exact", "phrase", "keyword", "rag")
