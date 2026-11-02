import pytest

from helpdesk_faq_bot.models import FAQEntry


@pytest.fixture
def entries():
    return [
        FAQEntry(
            question="How do I connect to the office Wi-Fi?",
            answer="Join Corp-WiFi.",
            alternate_phrases=("wifi password", "how do I get on the wifi"),
            keyword_rule='(wifi OR "wi-fi" OR wireless) AND (connect OR join OR password) AND NOT guest',
        ),
        FAQEntry(
            question="What is the guest Wi-Fi password?",
            answer="Ask reception.",
            keyword_rule='(wifi OR "wi-fi") AND (guest OR visitor)',
        ),
        FAQEntry(
            question="How do I add the office printer?",
            answer="Settings > Printers.",
            alternate_phrases=("set up the printer",),
            keyword_rule="(printer OR print) AND (add OR install OR connect)",
        ),
        FAQEntry(
            question="How do I reset my password?",
            answer="Use the password portal.",
            alternate_phrases=("forgot my password", "locked out"),
            keyword_rule='password AND (reset OR forgot OR change) AND NOT (wifi OR "wi-fi")',
        ),
        FAQEntry(
            question="How do I set up MFA?",
            answer="Install an authenticator.",
            alternate_phrases=("authenticator app",),
            keyword_rule='(mfa OR 2fa OR authenticator) AND (setup OR "set up" OR install)',
        ),
    ]
