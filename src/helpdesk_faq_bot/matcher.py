"""Deterministic FAQ matching.

Matching happens in three tiers, highest precedence first:

1. ``exact``   - the question equals an FAQ question or one of its alternate phrases
                 (after normalising case, punctuation and whitespace).
2. ``phrase``  - a complete FAQ question or alternate phrase (of at least ``MIN_PHRASE_WORDS``
                 words) appears inside the question as whole words, e.g. "connect to the vpn"
                 inside "how do I connect to the vpn from a hotel". Short phrases such as
                 "authenticator app" are too generic to win on containment alone; cover them
                 with a keyword rule instead.
3. ``keyword`` - the FAQ's keyword rule evaluates to true.

Keyword rules are small boolean expressions, for example::

    (wifi OR "wi-fi" OR wireless) AND (password OR connect) AND NOT guest

* Operators are UPPERCASE ``AND``, ``OR``, ``NOT``; parentheses group.
* Adjacent terms without an operator are treated as ``AND``.
* Terms match whole words; quoted terms match whole phrases.
* Lowercase "and"/"or"/"not" are ordinary words.

If the best tier contains one FAQ it is returned as the answer. If it contains several,
the caller gets them all as candidates so the user can pick (Slack buttons / dropdown).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .models import FAQEntry

log = logging.getLogger(__name__)

TIER_ORDER = ("exact", "phrase", "keyword")
MIN_PHRASE_WORDS = 3

_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def normalise(text: str) -> str:
    """Lowercase, replace punctuation with spaces, collapse whitespace.

    "Wi-Fi isn't working!" -> "wi fi isn t working"
    """
    return " ".join(_NORMALISE_RE.sub(" ", text.lower()).split())


def contains_phrase(haystack_norm: str, phrase: str) -> bool:
    """True if ``phrase`` appears in the normalised haystack as complete words."""
    needle = normalise(phrase)
    if not needle:
        return False
    return f" {needle} " in f" {haystack_norm} "


# --------------------------------------------------------------------------- rule parser


class RuleSyntaxError(ValueError):
    """Raised when a keyword rule cannot be parsed."""


_TOKEN_RE = re.compile(r'\s*(?:(\()|(\))|"([^"]*)"|([^\s()"]+))')


def _tokenise(rule: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    rule = rule.strip()
    while pos < len(rule):
        m = _TOKEN_RE.match(rule, pos)
        if not m or m.end() == pos:
            raise RuleSyntaxError(f"Unexpected character at position {pos}: {rule[pos:]!r}")
        pos = m.end()
        lparen, rparen, quoted, word = m.groups()
        if lparen:
            tokens.append(("LPAREN", "("))
        elif rparen:
            tokens.append(("RPAREN", ")"))
        elif quoted is not None:
            tokens.append(("TERM", quoted))
        elif word in ("AND", "OR", "NOT"):
            tokens.append((word, word))
        else:
            tokens.append(("TERM", word))
    return tokens


@dataclass(frozen=True)
class Term:
    text: str

    def evaluate(self, question_norm: str) -> bool:
        return contains_phrase(question_norm, self.text)


@dataclass(frozen=True)
class Not:
    child: Node

    def evaluate(self, question_norm: str) -> bool:
        return not self.child.evaluate(question_norm)


@dataclass(frozen=True)
class And:
    children: tuple[Node, ...]

    def evaluate(self, question_norm: str) -> bool:
        return all(c.evaluate(question_norm) for c in self.children)


@dataclass(frozen=True)
class Or:
    children: tuple[Node, ...]

    def evaluate(self, question_norm: str) -> bool:
        return any(c.evaluate(question_norm) for c in self.children)


Node = Term | Not | And | Or


class _Parser:
    """Recursive-descent parser. Precedence: NOT > AND (explicit or implicit) > OR."""

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.tokens[self.i][0] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def parse(self) -> Node:
        if not self.tokens:
            raise RuleSyntaxError("Empty rule")
        node = self.parse_or()
        if self.peek() is not None:
            raise RuleSyntaxError(f"Unexpected token {self.tokens[self.i][1]!r}")
        return node

    def parse_or(self) -> Node:
        children = [self.parse_and()]
        while self.peek() == "OR":
            self.take()
            children.append(self.parse_and())
        return children[0] if len(children) == 1 else Or(tuple(children))

    def parse_and(self) -> Node:
        children = [self.parse_not()]
        while self.peek() in ("AND", "NOT", "TERM", "LPAREN"):
            if self.peek() == "AND":
                self.take()
            children.append(self.parse_not())
        return children[0] if len(children) == 1 else And(tuple(children))

    def parse_not(self) -> Node:
        if self.peek() == "NOT":
            self.take()
            return Not(self.parse_not())
        return self.parse_atom()

    def parse_atom(self) -> Node:
        kind = self.peek()
        if kind == "LPAREN":
            self.take()
            node = self.parse_or()
            if self.peek() != "RPAREN":
                raise RuleSyntaxError("Missing closing parenthesis")
            self.take()
            return node
        if kind == "TERM":
            text = self.take()[1]
            if not normalise(text):
                raise RuleSyntaxError(f"Term {text!r} has no letters or digits")
            return Term(text)
        if kind is None:
            raise RuleSyntaxError("Rule ended unexpectedly")
        raise RuleSyntaxError(f"Unexpected token {self.tokens[self.i][1]!r}")


def parse_rule(rule: str) -> Node:
    """Parse a keyword rule into an expression tree. Raises RuleSyntaxError."""
    return _Parser(_tokenise(rule)).parse()


def rule_matches(rule: str, question: str) -> bool:
    return parse_rule(rule).evaluate(normalise(question))


# --------------------------------------------------------------------------- matcher


@dataclass
class MatchResult:
    """Outcome of matching one question against the FAQ."""

    tier: str | None = None  # "exact" | "phrase" | "keyword" | None
    candidates: list[FAQEntry] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.candidates)

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def best(self) -> FAQEntry | None:
        return self.candidates[0] if len(self.candidates) == 1 else None


class KeywordMatcher:
    """Matches questions against FAQ entries. Bad rules are logged and skipped, never fatal."""

    def __init__(self, entries: Iterable[FAQEntry]):
        self.entries: list[FAQEntry] = list(entries)
        self._rules: dict[int, Node] = {}
        self.rule_errors: dict[str, str] = {}
        for idx, entry in enumerate(self.entries):
            if not entry.keyword_rule.strip():
                continue
            try:
                self._rules[idx] = parse_rule(entry.keyword_rule)
            except RuleSyntaxError as exc:
                self.rule_errors[entry.question] = str(exc)
                log.warning("Skipping bad keyword rule for %r: %s", entry.question, exc)

    def match(self, question: str) -> MatchResult:
        q = normalise(question)
        if not q:
            return MatchResult()
        tiers: dict[str, list[FAQEntry]] = {t: [] for t in TIER_ORDER}
        for idx, entry in enumerate(self.entries):
            phrases: Sequence[str] = [entry.question, *entry.alternate_phrases]
            norm_phrases = [normalise(p) for p in phrases if normalise(p)]
            if q in norm_phrases:
                tiers["exact"].append(entry)
            elif any(len(p.split()) >= MIN_PHRASE_WORDS and contains_phrase(q, p) for p in norm_phrases):
                tiers["phrase"].append(entry)
            elif idx in self._rules and self._rules[idx].evaluate(q):
                tiers["keyword"].append(entry)
        for tier in TIER_ORDER:
            if tiers[tier]:
                return MatchResult(tier=tier, candidates=tiers[tier])
        return MatchResult()
