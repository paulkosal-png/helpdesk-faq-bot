"""Slack front end (Bolt for Python, Socket Mode - no public URL or inbound firewall rule needed).

* Direct messages to the app and @mentions in channels (channel replies go in a thread).
* One match -> answer. Several -> buttons (<=5) or a dropdown (>5) plus "None of these".
* Only the person who asked can pick; choices expire after 15 minutes; repeat clicks are ignored.
* "None of these" or no match -> RAG fallback / escalation, logged to the unanswered log.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .models import BotAnswer, FAQEntry
from .service import HelpdeskService

log = logging.getLogger(__name__)

CHOICE_TTL_SECONDS = 15 * 60
MAX_BUTTONS = 5
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


@dataclass
class PendingChoice:
    user: str
    question: str
    options: list[str]  # full FAQ question text, so sorting the sheet can't break selections
    channel: str
    created: float = field(default_factory=time.monotonic)
    done: bool = False

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > CHOICE_TTL_SECONDS


class PendingStore:
    """In-memory store. For multiple replicas, swap for Redis/Firestore (see README roadmap)."""

    def __init__(self):
        self._items: dict[str, PendingChoice] = {}
        self._lock = threading.Lock()

    def add(self, choice: PendingChoice) -> str:
        key = uuid.uuid4().hex[:12]
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if not v.expired}
            self._items[key] = choice
        return key

    def claim(self, key: str, user: str) -> tuple[PendingChoice | None, str]:
        """Atomically mark a choice as used. Returns (choice, error_message)."""
        with self._lock:
            choice = self._items.get(key)
            if choice is None or choice.expired:
                return None, "Those choices have expired. Please ask your question again."
            if choice.user != user:
                return None, "Only the person who asked can pick an answer. Ask me directly and I'll help!"
            if choice.done:
                return None, ""  # repeat click: ignore silently
            choice.done = True
            return choice, ""


def clean_text(text: str) -> str:
    return " ".join(_MENTION_RE.sub(" ", text or "").split())


def format_answer(ans: BotAnswer) -> str:
    text = ans.text
    if ans.outcome == "rag" and ans.sources:
        text += "\n\n_Sources: " + ", ".join(s.title for s in ans.sources) + "_"
        text += "\n_AI-generated from our KB. If this doesn't solve it, say `escalate`._"
    return text


def choice_blocks(key: str, candidates: list[FAQEntry], prompt: str) -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": prompt}}]
    if len(candidates) <= MAX_BUTTONS:
        elements = [
            {
                "type": "button",
                "action_id": f"faq_choice_{i}",
                "text": {"type": "plain_text", "text": c.question[:75]},
                "value": f"{key}|{i}",
            }
            for i, c in enumerate(candidates)
        ]
    else:
        elements = [
            {
                "type": "static_select",
                "action_id": "faq_select",
                "placeholder": {"type": "plain_text", "text": "Pick a question"},
                "options": [
                    {"text": {"type": "plain_text", "text": c.question[:75]}, "value": f"{key}|{i}"}
                    for i, c in enumerate(candidates[:100])
                ],
            }
        ]
    elements.append(
        {
            "type": "button",
            "action_id": "faq_none",
            "style": "danger",
            "text": {"type": "plain_text", "text": "None of these"},
            "value": key,
        }
    )
    blocks.append({"type": "actions", "elements": elements})
    return blocks


def create_app(service: HelpdeskService, bot_token: str, verify_token: bool = True) -> App:
    # verify_token=False lets tests build the app without calling Slack's auth.test
    app = App(token=bot_token, token_verification_enabled=verify_token)
    pending = PendingStore()
    last_question: dict[str, str] = {}  # user -> last unanswered-by-keyword question (for "escalate")

    def respond(question: str, user: str, channel: str, channel_type: str, say, thread_ts: str | None):
        question = clean_text(question)
        if not question:
            say(text="Hi! Ask me an IT question, e.g. _how do I connect to the VPN?_", thread_ts=thread_ts)
            return
        lowered = question.lower()
        if lowered in ("cancel", "never mind", "nevermind"):
            say(text="No problem - cancelled.", thread_ts=thread_ts)
            return
        if lowered == "escalate" and user in last_question:
            ans = service.escalate(last_question.pop(user), user=user, channel=channel, reason="user_requested")
            say(text=format_answer(ans), thread_ts=thread_ts)
            return
        if lowered.startswith("new question:"):
            question = question.split(":", 1)[1].strip()

        ans = service.answer(question, user=user, channel=channel, channel_type=channel_type)
        if ans.outcome == "ambiguous":
            key = pending.add(PendingChoice(user, question, [c.question for c in ans.candidates], channel))
            say(text=ans.text, blocks=choice_blocks(key, ans.candidates, ans.text), thread_ts=thread_ts)
            return
        if ans.outcome == "rag":
            last_question[user] = question
        say(text=format_answer(ans), thread_ts=thread_ts)

    @app.event("app_mention")
    def on_mention(event, say):
        thread_ts = event.get("thread_ts") or event.get("ts")
        respond(event.get("text", ""), event.get("user", ""), event.get("channel", ""), "channel", say, thread_ts)

    @app.event("message")
    def on_message(event, say):
        # Only plain DMs from humans; channel messages arrive via app_mention.
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        respond(event.get("text", ""), event.get("user", ""), event.get("channel", ""), "im", say, None)

    def _thread(body) -> str | None:
        msg = body.get("message") or {}
        return msg.get("thread_ts") or (
            msg.get("ts") if body.get("channel", {}).get("id", "").startswith("C") else None
        )

    def handle_pick(ack, body, say, value: str):
        ack()
        key, _, idx = value.partition("|")
        user = body["user"]["id"]
        choice, err = pending.claim(key, user)
        if choice is None:
            if err:
                say(text=err, thread_ts=_thread(body))
            return
        picked = choice.options[int(idx)]
        entry = service.find_entry(picked)
        text = entry.answer if entry else "That FAQ was just removed or renamed - please ask again."
        say(text=f"*{picked}*\n{text}", thread_ts=_thread(body))

    @app.action(re.compile(r"^faq_choice_\d+$"))
    def on_button(ack, body, say, action):
        handle_pick(ack, body, say, action["value"])

    @app.action("faq_select")
    def on_select(ack, body, say, action):
        handle_pick(ack, body, say, action["selected_option"]["value"])

    @app.action("faq_none")
    def on_none(ack, body, say, action):
        ack()
        user = body["user"]["id"]
        choice, err = pending.claim(action["value"], user)
        if choice is None:
            if err:
                say(text=err, thread_ts=_thread(body))
            return
        ans = service.escalate(choice.question, user=user, channel=choice.channel, reason="none_of_these")
        say(text=format_answer(ans), thread_ts=_thread(body))

    return app


def run_slack(service: HelpdeskService, bot_token: str, app_token: str) -> None:
    if not (bot_token.startswith("xoxb-") and app_token.startswith("xapp-")):
        raise SystemExit("Set SLACK_BOT_TOKEN (xoxb-...) and SLACK_APP_TOKEN (xapp-...) - see .env.example")
    app = create_app(service, bot_token)
    log.info("Connecting to Slack via Socket Mode...")
    SocketModeHandler(app, app_token).start()
