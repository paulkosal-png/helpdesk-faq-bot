"""Command line entry point.

python -m helpdesk_faq_bot demo              # answer sample questions, no Slack needed
python -m helpdesk_faq_bot ask "vpn won't connect"
python -m helpdesk_faq_bot chat              # interactive prompt
python -m helpdesk_faq_bot check             # validate FAQ rows + keyword rules
python -m helpdesk_faq_bot match "question"  # show which FAQ rows match (read-only, no logging)
python -m helpdesk_faq_bot simulate          # generate traffic + /metrics for the Grafana demo
python -m helpdesk_faq_bot slack             # run the real Slack bot (Socket Mode)
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from .config import load_settings
from .matcher import KeywordMatcher
from .models import BotAnswer

DEMO_QUESTIONS = [
    "How do I reset my password?",  # exact
    "my wifi keeps dropping in the conference room",  # keyword rule
    "how do i connect to the vpn from home",  # alternate phrase
    "how do I connect the printer to wifi",  # two keyword rules -> user picks
    "I got a new phone, how do I move my authenticator app?",  # keyword (MFA move)
    "my laptop fan is really loud and it's overheating",  # RAG fallback (KB article)
    "can I bring my dog to the office?",  # nothing -> escalate
]


def print_answer(question: str, ans: BotAnswer) -> None:
    print(f"\nQ: {question}")
    print(f"   [{ans.outcome}] ({ans.latency_seconds * 1000:.1f} ms)")
    if ans.outcome == "ambiguous":
        print("   " + ans.text)
        for i, c in enumerate(ans.candidates, 1):
            print(f"     {i}. {c.question}")
        print("     x. None of these")
    else:
        for line in ans.text.splitlines():
            print("   " + line)
    if ans.sources:
        print("   sources: " + "; ".join(f"{s.title} [{s.location}] ({s.score})" for s in ans.sources))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="helpdesk_faq_bot", description="Slack IT helpdesk FAQ bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="answer built-in sample questions")
    p_ask = sub.add_parser("ask", help="answer one question")
    p_ask.add_argument("question", nargs="+")
    sub.add_parser("chat", help="interactive prompt")
    sub.add_parser("check", help="validate the FAQ source and keyword rules")
    p_match = sub.add_parser("match", help="show matching FAQ rows (read-only)")
    p_match.add_argument("question", nargs="+")
    p_sim = sub.add_parser("simulate", help="generate demo traffic and serve /metrics")
    p_sim.add_argument("--interval", type=float, default=2.0)
    sub.add_parser("slack", help="run the Slack bot (Socket Mode)")
    args = parser.parse_args(argv)

    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd in ("demo", "ask", "chat", "check", "match"):
        logging.getLogger().setLevel(logging.WARNING)  # keep CLI output readable

    from .service import build_service

    service = build_service(settings)

    if args.cmd == "check":
        entries = service.faq_source.load()
        matcher = KeywordMatcher(entries)
        print(f"FAQ source: {service.faq_source.name}  entries: {len(entries)}")
        print(f"Rows with keyword rules: {sum(1 for e in entries if e.keyword_rule)}")
        for q, err in matcher.rule_errors.items():
            print(f"  BAD RULE  {q!r}: {err}")
        print(f"LLM configured: {settings.llm_configured}  embeddings: {settings.embedding_backend}")
        return 1 if matcher.rule_errors else 0

    if args.cmd == "match":
        question = " ".join(args.question)
        result = KeywordMatcher(service.faq_source.load()).match(question)
        print(f"tier: {result.tier}")
        for c in result.candidates:
            print(f"  - {c.question}   rule: {c.keyword_rule or '-'}")
        return 0

    if args.cmd == "demo":
        for q in DEMO_QUESTIONS:
            print_answer(q, service.answer(q, user="demo"))
        print(f"\nUnanswered questions are logged to {settings.unanswered_log_path}")
        return 0

    if args.cmd == "ask":
        q = " ".join(args.question)
        print_answer(q, service.answer(q, user="cli"))
        return 0

    if args.cmd == "chat":
        print("Ask an IT question (Ctrl+C or 'quit' to exit).")
        try:
            while (q := input("\n> ").strip()).lower() not in ("quit", "exit"):
                if q:
                    print_answer(q, service.answer(q, user="cli"))
        except (EOFError, KeyboardInterrupt):
            pass
        return 0

    from .metrics import start_metrics_server

    start_metrics_server(settings.metrics_port)
    logging.info("Loaded %d FAQ entries", service.warm_up())

    if args.cmd == "simulate":
        extra = ["wifi password", "vpn", "monitor not working", "new hire laptop", "is the printer broken"]
        logging.info("Simulating traffic every %.1fs (Ctrl+C to stop)", args.interval)
        try:
            while True:
                q = random.choice(DEMO_QUESTIONS + extra)
                ans = service.answer(q, user="simulator", channel_type=random.choice(["im", "channel"]))
                logging.info("%-12s %s", ans.outcome, q)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0

    from .slack_app import run_slack

    run_slack(service, settings.slack_bot_token, settings.slack_app_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
