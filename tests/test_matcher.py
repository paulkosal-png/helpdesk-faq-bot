import pytest

from helpdesk_faq_bot.matcher import KeywordMatcher, RuleSyntaxError, normalise, parse_rule, rule_matches
from helpdesk_faq_bot.models import FAQEntry, split_alternates


def test_normalise_strips_case_and_punctuation():
    assert normalise("  Wi-Fi isn't WORKING!! ") == "wi fi isn t working"


@pytest.mark.parametrize(
    "rule,question,expected",
    [
        ("vpn OR remote", "the VPN is down", True),
        ("vpn OR remote", "printer is down", False),
        ("printer AND jam", "printer has a paper jam", True),
        ("printer AND jam", "printer is fine", False),
        ("printer jam", "the printer has a jam", True),  # implicit AND
        ("printer jam", "the printer is fine", False),
        ("wifi AND NOT guest", "wifi for staff", True),
        ("wifi AND NOT guest", "wifi for a guest", False),
        ("(fridge OR refrigerator) AND (food OR lunch)", "can I put my lunch in the refrigerator", True),
        ("(fridge OR refrigerator) AND (food OR lunch)", "the fridge is broken", False),
        ('"out of office"', "how do I set out of office", True),
        ('"out of office"', "I am out of the office", False),  # phrase must be contiguous
        ('"wi-fi"', "wi-fi is slow", True),
        ('"wi-fi"', "Wi Fi is slow", True),  # punctuation-insensitive
        ("print", "the printer is broken", False),  # whole words only
        ("a OR b AND c", "a", True),  # AND binds tighter than OR
        ("a OR b AND c", "b", False),
        ("NOT NOT a", "a", True),
        ("vpn and wifi", "vpn", False),  # lowercase 'and' is a normal word -> implicit AND of 3 terms
    ],
)
def test_rule_evaluation(rule, question, expected):
    assert rule_matches(rule, question) is expected


@pytest.mark.parametrize("bad", ["", "(wifi AND vpn", "wifi AND", "AND wifi", "wifi )", 'wifi OR "!!"'])
def test_bad_rules_raise(bad):
    with pytest.raises(RuleSyntaxError):
        parse_rule(bad)


def test_bad_rule_is_skipped_not_fatal(entries):
    broken = FAQEntry(question="Broken row", answer="x", keyword_rule="(vpn AND")
    matcher = KeywordMatcher([*entries, broken])
    assert "Broken row" in matcher.rule_errors
    assert matcher.match("vpn help").matched is False  # other rows still work, no crash


def test_split_alternates_semicolons_and_newlines():
    assert split_alternates("a; b\nc ;; ") == ["a", "b", "c"]


def test_exact_question_match(entries):
    result = KeywordMatcher(entries).match("how do i reset my password")
    assert result.tier == "exact"
    assert result.best.answer == "Use the password portal."


def test_exact_alternate_phrase_match(entries):
    result = KeywordMatcher(entries).match("Forgot my password!")
    assert result.tier == "exact"
    assert result.best.question == "How do I reset my password?"


def test_alternate_phrase_contained_in_longer_question(entries):
    result = KeywordMatcher(entries).match("Hi, I need to set up the printer on my new laptop")
    assert result.tier == "phrase"
    assert result.best.question == "How do I add the office printer?"


def test_short_alternate_phrase_does_not_win_by_containment(entries):
    # "authenticator app" (2 words) should not hijack an unrelated question
    result = KeywordMatcher(entries).match("my authenticator app shows the wrong code")
    assert result.matched is False


def test_exact_and_phrase_beat_keyword_rules(entries):
    # "wifi password" is an exact alternate for the Wi-Fi entry; the password-reset rule has NOT wifi anyway,
    # but the guest rule doesn't match either - make sure exact wins cleanly with a single answer.
    result = KeywordMatcher(entries).match("wifi password")
    assert result.tier == "exact"
    assert [c.question for c in result.candidates] == ["How do I connect to the office Wi-Fi?"]


def test_keyword_rule_match(entries):
    result = KeywordMatcher(entries).match("I can't join the wireless network")
    assert result.tier == "keyword"
    assert result.best.question == "How do I connect to the office Wi-Fi?"


def test_not_operator_routes_to_other_entry(entries):
    result = KeywordMatcher(entries).match("my guest needs to connect to the wifi")
    assert [c.question for c in result.candidates] == ["What is the guest Wi-Fi password?"]


def test_multiple_keyword_matches_are_ambiguous(entries):
    result = KeywordMatcher(entries).match("how do I connect the printer to wifi")
    assert result.tier == "keyword"
    assert result.is_ambiguous
    assert {c.question for c in result.candidates} == {
        "How do I connect to the office Wi-Fi?",
        "How do I add the office printer?",
    }
    assert result.best is None


def test_no_match(entries):
    result = KeywordMatcher(entries).match("can I bring my dog to work")
    assert not result.matched and result.tier is None


def test_empty_question(entries):
    assert not KeywordMatcher(entries).match("   ?!  ").matched


def test_sample_faq_csv_has_valid_rules():
    from helpdesk_faq_bot.config import PROJECT_ROOT
    from helpdesk_faq_bot.faq_source import CsvFaqSource

    rows = CsvFaqSource(PROJECT_ROOT / "data" / "sample_faq.csv").load()
    assert len(rows) >= 25
    assert KeywordMatcher(rows).rule_errors == {}
