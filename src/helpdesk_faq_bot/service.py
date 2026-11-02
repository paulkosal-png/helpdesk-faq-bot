"""The brain of the bot, independent of Slack so it can be tested and run from a CLI.

question -> keyword matcher -> (one match)  -> answer
                            -> (several)    -> let the user pick
                            -> (none)       -> RAG fallback -> answer with sources
                                                            -> escalate + log
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time

from . import metrics
from .faq_source import FaqSource, UnansweredLog
from .matcher import KeywordMatcher
from .models import BotAnswer, FAQEntry
from .rag import Chunk, Embedder, LLMClient, RagAnswerer, VectorIndex, faq_chunks

log = logging.getLogger(__name__)


class HelpdeskService:
    def __init__(
        self,
        faq_source: FaqSource,
        unanswered_log: UnansweredLog,
        embedder: Embedder | None = None,
        llm: LLMClient | None = None,
        kb_chunks: list[Chunk] | None = None,
        rag_enabled: bool = True,
        rag_top_k: int = 4,
        rag_min_score: float = 0.25,
        escalation_contact: str = "the IT team",
    ):
        self.faq_source = faq_source
        self.unanswered_log = unanswered_log
        self.embedder = embedder
        self.llm = llm
        self.kb_chunks = kb_chunks or []
        self.rag_enabled = rag_enabled and embedder is not None
        self.rag_top_k = rag_top_k
        self.rag_min_score = rag_min_score
        self.escalation_contact = escalation_contact
        self._lock = threading.Lock()
        self._fingerprint = ""
        self._matcher = KeywordMatcher([])
        self._rag: RagAnswerer | None = None

    # -- state ---------------------------------------------------------------------------

    def _refresh(self) -> tuple[KeywordMatcher, RagAnswerer | None]:
        """Rebuild the matcher (and re-embed) only when the FAQ content actually changed."""
        entries = self.faq_source.load()
        fp = hashlib.sha256(repr(entries).encode()).hexdigest()
        with self._lock:
            if fp != self._fingerprint:
                self._matcher = KeywordMatcher(entries)
                metrics.FAQ_ENTRIES.set(len(entries))
                metrics.RULE_ERRORS.set(len(self._matcher.rule_errors))
                if self.rag_enabled:
                    index = VectorIndex(self.embedder, faq_chunks(entries) + self.kb_chunks)
                    self._rag = RagAnswerer(index, self.llm, self.rag_top_k, self.rag_min_score)
                self._fingerprint = fp
                log.info("Loaded %d FAQ entries (%d bad rules)", len(entries), len(self._matcher.rule_errors))
            return self._matcher, self._rag

    def warm_up(self) -> int:
        """Load the FAQ (and build the RAG index) at startup so config errors surface immediately."""
        matcher, _ = self._refresh()
        return len(matcher.entries)

    def find_entry(self, question: str) -> FAQEntry | None:
        """Look up an FAQ by its full question text (used when a user clicks a choice button)."""
        matcher, _ = self._refresh()
        return next((e for e in matcher.entries if e.question == question), None)

    # -- main entry point ----------------------------------------------------------------

    def answer(self, question: str, user: str = "", channel: str = "", channel_type: str = "cli") -> BotAnswer:
        start = time.perf_counter()
        metrics.QUESTIONS.labels(channel_type=channel_type).inc()
        matcher, rag = self._refresh()

        result = matcher.match(question)
        if result.matched:
            metrics.MATCHED.inc()
            if result.is_ambiguous:
                ans = BotAnswer(
                    "ambiguous", "I found a few possible answers. Which one do you mean?", result.candidates
                )
            else:
                entry = result.best
                ans = BotAnswer(result.tier or "keyword", entry.answer, [entry])
            return self._finish(ans, start, "keyword")

        metrics.UNMATCHED.inc()
        if rag is not None:
            rag_result = rag.answer(question)
            metrics.RAG_FALLBACKS.labels(result=rag_result.reason).inc()
            if rag_result.answer:
                return self._finish(BotAnswer("rag", rag_result.answer, sources=rag_result.sources), start, "rag")
            return self.escalate(
                question, user, channel, reason=rag_result.reason, sources=rag_result.sources, start=start
            )
        return self.escalate(question, user, channel, reason="no_match", start=start)

    def escalate(self, question, user="", channel="", reason="no_match", sources=None, start=None) -> BotAnswer:
        start = start if start is not None else time.perf_counter()
        self.unanswered_log.record(question, user=user, channel=channel, reason=reason)
        metrics.ESCALATIONS.labels(reason=reason).inc()
        text = (
            f"Sorry, I don't have an answer for that yet. I've logged your question and escalated it to "
            f"{self.escalation_contact}, who will follow up."
        )
        if sources:
            text += "\nIn the meantime, these articles might help: " + ", ".join(s.title for s in sources[:3])
        return self._finish(BotAnswer("unanswered", text, sources=list(sources or [])), start, "escalate")

    @staticmethod
    def _finish(ans: BotAnswer, start: float, path: str) -> BotAnswer:
        ans.latency_seconds = time.perf_counter() - start
        metrics.ANSWERS.labels(outcome=ans.outcome).inc()
        metrics.LATENCY.labels(path=path).observe(ans.latency_seconds)
        return ans


def build_service(settings) -> HelpdeskService:
    """Wire everything up from Settings."""
    from .faq_source import build_faq_source
    from .rag import OpenAICompatibleLLM, build_embedder, markdown_chunks

    faq_source, sheet = build_faq_source(settings)
    unanswered = UnansweredLog(settings.unanswered_log_path, sheet, settings.unanswered_sheet_name)
    embedder = build_embedder(settings) if settings.rag_enabled else None
    llm = None
    if settings.llm_configured:
        llm = OpenAICompatibleLLM(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds
        )
        log.info("RAG LLM: %s @ %s", settings.llm_model, settings.llm_base_url)
    else:
        log.info("No LLM configured: unmatched questions will be escalated (with related articles)")
    return HelpdeskService(
        faq_source,
        unanswered,
        embedder=embedder,
        llm=llm,
        kb_chunks=markdown_chunks(settings.kb_dir) if settings.rag_enabled else [],
        rag_enabled=settings.rag_enabled,
        rag_top_k=settings.rag_top_k,
        rag_min_score=settings.rag_min_score,
        escalation_contact=settings.escalation_contact,
    )
