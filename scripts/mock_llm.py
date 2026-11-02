"""A fake OpenAI-compatible LLM server for wiring tests (no API key, no cost, NOT a real model).

It "answers" by echoing the first retrieved context chunk, so you can watch the RAG path work
end to end before pointing LLM_BASE_URL at a real model (Ollama, OpenAI, etc.).

    python scripts/mock_llm.py            # serves http://127.0.0.1:8099/v1
    LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_MODEL=mock python -m helpdesk_faq_bot demo
"""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8099


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        m = re.search(r"\[1\] (.+?)\n(.*?)(?:\n\n\[2\]|\n\nQuestion:)", prompt, re.S)
        if m:
            snippet = " ".join(m.group(2).split())[:400]
            reply = f"(mock LLM) Based on '{m.group(1)}': {snippet}"
        else:
            reply = "NO_ANSWER"
        payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    print(f"Mock LLM listening on http://127.0.0.1:{PORT}/v1 (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
