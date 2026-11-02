"""Prometheus metrics, exposed on http://<host>:METRICS_PORT/metrics."""

from __future__ import annotations

import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = logging.getLogger(__name__)

QUESTIONS = Counter("helpdesk_questions_total", "Questions received", ["channel_type"])
ANSWERS = Counter(
    "helpdesk_answers_total",
    "Answers by outcome (exact, phrase, keyword, ambiguous, rag, unanswered)",
    ["outcome"],
)
MATCHED = Counter("helpdesk_matched_total", "Questions answered by the deterministic keyword matcher")
UNMATCHED = Counter("helpdesk_unmatched_total", "Questions the keyword matcher could not answer")
RAG_FALLBACKS = Counter("helpdesk_rag_fallback_total", "Times the RAG fallback was attempted", ["result"])
ESCALATIONS = Counter("helpdesk_escalations_total", "Questions escalated to IT", ["reason"])
LATENCY = Histogram(
    "helpdesk_answer_latency_seconds",
    "Time to produce an answer",
    ["path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
FAQ_ENTRIES = Gauge("helpdesk_faq_entries", "FAQ entries currently loaded")
RULE_ERRORS = Gauge("helpdesk_faq_rule_errors", "FAQ rows whose keyword rule failed to parse")


def start_metrics_server(port: int) -> None:
    start_http_server(port)
    log.info("Prometheus metrics on :%d/metrics", port)
