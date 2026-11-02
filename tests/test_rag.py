import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from helpdesk_faq_bot.faq_source import UnansweredLog
from helpdesk_faq_bot.rag import (
    NO_ANSWER,
    Chunk,
    HashingEmbedder,
    OpenAICompatibleLLM,
    RagAnswerer,
    VectorIndex,
    markdown_chunks,
)
from helpdesk_faq_bot.service import HelpdeskService

VOCAB = ["laptop", "fan", "overheating", "battery", "vpn", "printer", "dog"]


class StubEmbedder:
    """Deterministic one-hot-per-keyword embedder so tests don't download models."""

    def embed(self, texts):
        out = np.zeros((len(texts), len(VOCAB)), dtype=np.float32)
        for i, t in enumerate(texts):
            for j, word in enumerate(VOCAB):
                if word in t.lower():
                    out[i, j] = 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return out / norms


class StubLLM:
    def __init__(self, reply="Keep vents clear and restart. [1]", fail=False):
        self.reply = reply
        self.fail = fail
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        if self.fail:
            raise TimeoutError("LLM down")
        return self.reply


class ListSource:
    name = "list"

    def __init__(self, entries):
        self.entries = entries

    def load(self):
        return self.entries


KB = [
    Chunk("Laptop overheating", "sample_kb/laptop.md", "Laptop fan loud, overheating: keep vents clear, restart."),
    Chunk("VPN troubleshooting", "sample_kb/vpn.md", "VPN errors: reset password, switch gateway."),
]


def make_service(tmp_path, entries, llm=None, embedder=None):
    return HelpdeskService(
        ListSource(entries),
        UnansweredLog(tmp_path / "unanswered.jsonl"),
        embedder=embedder or StubEmbedder(),
        llm=llm,
        kb_chunks=KB,
        rag_min_score=0.3,
    )


def read_log(tmp_path):
    path = tmp_path / "unanswered.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_vector_index_ranks_relevant_chunk_first():
    index = VectorIndex(StubEmbedder(), KB)
    (best, score), *_ = index.search("my laptop is overheating", k=2)
    assert best.title == "Laptop overheating"
    assert score > 0.5


def test_keyword_match_skips_rag(tmp_path, entries):
    llm = StubLLM()
    ans = make_service(tmp_path, entries, llm).answer("forgot my password")
    assert ans.outcome == "exact"
    assert llm.calls == []


def test_rag_fallback_answers_with_sources(tmp_path, entries):
    llm = StubLLM()
    ans = make_service(tmp_path, entries, llm).answer("my laptop fan sounds like a jet, overheating")
    assert ans.outcome == "rag"
    assert ans.text.startswith("Keep vents clear")
    assert ans.sources[0].title == "Laptop overheating"
    assert "Laptop fan loud" in llm.calls[0][1]  # retrieved context was sent to the LLM
    assert read_log(tmp_path) == []  # answered -> not logged as unanswered


def test_no_llm_key_degrades_to_escalation(tmp_path, entries):
    ans = make_service(tmp_path, entries, llm=None).answer("laptop overheating")
    assert ans.outcome == "unanswered"
    assert "escalated" in ans.text
    assert "Laptop overheating" in ans.text  # related article still suggested
    assert read_log(tmp_path)[0]["reason"] == "no_llm"


def test_llm_error_degrades_to_escalation(tmp_path, entries):
    ans = make_service(tmp_path, entries, StubLLM(fail=True)).answer("laptop overheating")
    assert ans.outcome == "unanswered"
    assert read_log(tmp_path)[0]["reason"] == "llm_error"


def test_llm_says_no_answer(tmp_path, entries):
    ans = make_service(tmp_path, entries, StubLLM(reply=NO_ANSWER)).answer("laptop battery warranty")
    assert ans.outcome == "unanswered"
    assert read_log(tmp_path)[0]["reason"] == "llm_declined"


def test_irrelevant_question_never_calls_llm(tmp_path, entries):
    llm = StubLLM()
    ans = make_service(tmp_path, entries, llm).answer("can I bring my dog to work?")
    assert ans.outcome == "unanswered"
    assert llm.calls == []
    assert read_log(tmp_path)[0]["reason"] == "low_score"


def test_rag_disabled_escalates_directly(tmp_path, entries):
    svc = HelpdeskService(ListSource(entries), UnansweredLog(tmp_path / "u.jsonl"), embedder=None)
    ans = svc.answer("laptop overheating")
    assert ans.outcome == "unanswered"


def test_empty_index():
    result = RagAnswerer(VectorIndex(StubEmbedder(), []), StubLLM()).answer("anything")
    assert result.answer is None and result.reason == "empty_index"


def test_hashing_embedder_is_deterministic_and_normalised():
    emb = HashingEmbedder()
    a, b = emb.embed(["printer jam", "printer jam"])
    assert np.allclose(a, b)
    assert np.isclose(np.linalg.norm(a), 1.0)


def test_markdown_chunks_split_by_heading(tmp_path):
    (tmp_path / "guide.md").write_text("# Guide\nintro\n\n## Step one\ndo this\n\n## Step two\ndo that\n")
    chunks = markdown_chunks(tmp_path)
    assert [c.title for c in chunks] == ["Guide", "Guide - Step one", "Guide - Step two"]
    assert chunks[0].location.endswith("guide.md")


def test_service_reloads_when_faq_changes(tmp_path, entries):
    source = ListSource(entries[:1])
    svc = HelpdeskService(source, UnansweredLog(tmp_path / "u.jsonl"), embedder=StubEmbedder())
    assert svc.answer("forgot my password").outcome == "unanswered"
    source.entries = entries  # someone edited the sheet
    assert svc.answer("forgot my password").outcome == "exact"


@pytest.fixture
def fake_openai_server():
    """A tiny local OpenAI-compatible /chat/completions endpoint."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            Handler.last = (self.path, self.headers.get("Authorization"), body)
            payload = json.dumps({"choices": [{"message": {"content": " Restart the laptop. [1] "}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", Handler
    server.shutdown()


def test_openai_compatible_client(fake_openai_server):
    url, handler = fake_openai_server
    llm = OpenAICompatibleLLM(url, "test-key", "tiny-model")
    assert llm.complete("sys", "user q") == "Restart the laptop. [1]"
    path, auth, body = handler.last
    assert path == "/v1/chat/completions"
    assert auth == "Bearer test-key"
    assert body["model"] == "tiny-model"
    assert body["messages"][0]["role"] == "system"
