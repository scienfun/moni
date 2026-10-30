"""웹 채팅 테스트. 실제 소켓으로 접근 제한과 대화 내역 저장을 확인한다."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from moni_bridge.chat import ChatLog
from moni_bridge.server import make_handler


class FakeService:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.fail = False

    def command(self, text: str) -> dict:
        if self.fail:
            raise RuntimeError("device down")
        self.texts.append(text)
        return {
            "line": f"들었어: {text}", "speak": "들었어", "emotion": "happy",
            "latency_ms": 12, "panel": None, "ttl": 20, "id": "x",
        }


class WebChatTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.service = FakeService()
        self.chat = ChatLog(Path(self.dir.name) / "chat.jsonl")
        self.api = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.service, "tok", self.chat)
        )
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.addCleanup(self.api.shutdown)

    def _call(self, method, path, body=None, host=None, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.api.server_port, timeout=3)
        self.addCleanup(conn.close)
        headers = {"Host": host or f"localhost:{self.api.server_port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = content_type
        elif method == "DELETE":
            headers["Content-Type"] = content_type
        conn.request(method, path, data, headers)
        response = conn.getresponse()
        raw = response.read()
        payload = json.loads(raw) if response.getheader("Content-Type", "").startswith(
            "application/json"
        ) else raw.decode()
        return response.status, payload

    def test_page_is_served_without_token(self):
        status, html = self._call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("모니", html)

    def test_send_stores_both_sides_and_returns_reply(self):
        status, body = self._call("POST", "/chat/send", {"text": " 안녕 "})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"]["text"], "들었어: 안녕")
        roles = [m["role"] for m in self._call("GET", "/chat/history")[1]["messages"]]
        self.assertEqual(roles, ["user", "moni"])

    def test_history_survives_a_new_log_object(self):
        self._call("POST", "/chat/send", {"text": "하나"})
        again = ChatLog(Path(self.dir.name) / "chat.jsonl")
        self.assertEqual(len(again.history()), 2)

    def test_clear(self):
        self._call("POST", "/chat/send", {"text": "하나"})
        self.assertEqual(self._call("DELETE", "/chat/history")[0], 200)
        self.assertEqual(self._call("GET", "/chat/history")[1]["messages"], [])

    def test_blank_text_is_rejected(self):
        self.assertEqual(self._call("POST", "/chat/send", {"text": "  "})[0], 400)
        self.assertEqual(self.service.texts, [])

    def test_long_text_is_truncated(self):
        self._call("POST", "/chat/send", {"text": "가" * 900})
        self.assertEqual(len(self.service.texts[0]), 500)

    def test_foreign_host_header_is_refused(self):
        for host in ("evil.example.com", "192.168.1.5:8765"):
            self.assertEqual(self._call("GET", "/", host=host)[0], 403)
            self.assertEqual(self._call("GET", "/chat/history", host=host)[0], 403)

    def test_non_json_post_is_refused(self):
        status, _ = self._call(
            "POST", "/chat/send", {"text": "x"}, content_type="text/plain"
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.service.texts, [])

    def test_command_failure_is_reported_and_user_message_kept(self):
        self.service.fail = True
        self.assertEqual(self._call("POST", "/chat/send", {"text": "안녕"})[0], 502)
        self.assertEqual(len(self.chat.history()), 1)

    def test_client_api_still_requires_token(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.api.server_port, timeout=3)
        self.addCleanup(conn.close)
        conn.request("GET", "/v1/health")
        self.assertEqual(conn.getresponse().status, 401)

    def test_history_is_capped(self):
        chat = ChatLog(Path(self.dir.name) / "capped.jsonl", limit=3)
        for index in range(5):
            chat.add("user", str(index))
        self.assertEqual([m["text"] for m in chat.history()], ["2", "3", "4"])

    def test_torn_line_is_skipped(self):
        path = Path(self.dir.name) / "torn.jsonl"
        path.write_text('{"role":"user","text":"a","ts":1}\n{"role":', encoding="utf-8")
        self.assertEqual(len(ChatLog(path).history()), 1)


if __name__ == "__main__":
    unittest.main()
