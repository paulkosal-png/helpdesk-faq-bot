from helpdesk_faq_bot.models import BotAnswer, FAQEntry, Source
from helpdesk_faq_bot.slack_app import PendingChoice, PendingStore, choice_blocks, clean_text, format_answer


def test_clean_text_removes_mentions():
    assert clean_text("<@U123ABC>  how do I   print?") == "how do I print?"


def test_buttons_for_few_candidates_dropdown_for_many():
    few = [FAQEntry(question=f"Q{i}", answer="a") for i in range(3)]
    many = [FAQEntry(question=f"Q{i}", answer="a") for i in range(7)]
    few_elems = choice_blocks("k", few, "pick")[1]["elements"]
    many_elems = choice_blocks("k", many, "pick")[1]["elements"]
    assert [e["type"] for e in few_elems] == ["button"] * 4  # 3 + "None of these"
    assert many_elems[0]["type"] == "static_select" and len(many_elems[0]["options"]) == 7
    assert many_elems[-1]["action_id"] == "faq_none"


def test_only_requester_can_pick_and_only_once():
    store = PendingStore()
    key = store.add(PendingChoice(user="U1", question="q", options=["A", "B"], channel="D1"))
    choice, err = store.claim(key, "U2")
    assert choice is None and "Only the person" in err
    choice, err = store.claim(key, "U1")
    assert choice is not None and err == ""
    choice, err = store.claim(key, "U1")  # repeat click ignored silently
    assert choice is None and err == ""


def test_expired_choice(monkeypatch):
    store = PendingStore()
    key = store.add(PendingChoice(user="U1", question="q", options=["A"], channel="D1", created=0.0))
    choice, err = store.claim(key, "U1")
    assert choice is None and "expired" in err


def test_format_rag_answer_shows_sources():
    ans = BotAnswer("rag", "Do X.", sources=[Source("VPN guide", "kb/vpn.md", 0.8)])
    assert "Sources: VPN guide" in format_answer(ans)


def test_create_app_registers_listeners_without_network(tmp_path):
    from helpdesk_faq_bot.faq_source import UnansweredLog
    from helpdesk_faq_bot.service import HelpdeskService
    from helpdesk_faq_bot.slack_app import create_app

    class Empty:
        name = "empty"

        def load(self):
            return []

    svc = HelpdeskService(Empty(), UnansweredLog(tmp_path / "u.jsonl"))
    app = create_app(svc, "xoxb-test-not-real", verify_token=False)
    assert len(app._listeners) >= 5  # app_mention, message, 3 actions
