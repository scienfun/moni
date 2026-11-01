"""LM Studio 연동 테스트. OpenAI 호환 서버를 흉내 내 실제 소켓으로 통신한다."""

from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from moni_bridge.brain import Brain, LocalBrain, build_brain
from moni_bridge.config import Config, LlmConfig

NOW = datetime(2026, 10, 30, 9, 5)
ANSWER = {
    "emotion": "happy", "line": "안녕!", "speak": "안녕!", "panel": "none",
    "ttl": 20, "timer_seconds": None, "brightness": None,
}


class FakeLmStudio:
    def __init__(self) -> None:
        self.message: dict = {"content": json.dumps(ANSWER)}
        self.finish_reason = "stop"
        self.status = 200
        self.requests: list[dict] = []


def make_handler(state: FakeLmStudio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state.requests.append({"path": self.path, "body": json.loads(body)})
            payload = json.dumps(
                {
                    "choices": [
                        {"message": state.message, "finish_reason": state.finish_reason}
                    ]
                }
            ).encode()
            self.send_response(state.status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


class LocalBrainTest(unittest.TestCase):
    def setUp(self):
        self.state = FakeLmStudio()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.brain = LocalBrain(
            f"http://127.0.0.1:{server.server_port}/v1/", "m", 40, timeout=2
        )

    def test_parses_content(self):
        self.assertEqual(self.brain.decide("안녕", NOW, None).line, "안녕!")

    def test_request_turns_reasoning_off_and_sets_schema(self):
        self.brain.decide("안녕", NOW, {"temp": 14})
        request = self.state.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        body = request["body"]
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertIn("<command>안녕</command>", body["messages"][1]["content"])

    def test_weather_is_only_in_the_prompt_when_asked(self):
        self.brain.decide("심심해", NOW, {"temp": 14, "pop": 70})
        self.assertNotIn("weather", self.state.requests[0]["body"]["messages"][1]["content"])
        self.assertNotIn("temp", self.state.requests[0]["body"]["messages"][1]["content"])
        from moni_bridge.brain import user_message

        self.assertIn('"temp": 14', user_message("날씨 알려줘", NOW, {"temp": 14}))
        self.assertIn("불러오지 못함", user_message("우산 필요해?", NOW, None))

    def test_previous_turns_come_before_the_new_message(self):
        from moni_bridge.brain import Decision

        past = Decision("happy", "지금 12시야.", "지금 열두 시야.", panel="time")
        self.brain.decide("어케 알았음?", NOW, None, [("지금 몇 시야", past)])
        roles = [m["role"] for m in self.state.requests[0]["body"]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        messages = self.state.requests[0]["body"]["messages"]
        self.assertEqual(messages[1]["content"], "<command>지금 몇 시야</command>")
        self.assertEqual(json.loads(messages[2]["content"])["line"], "지금 12시야.")
        self.assertIn("<command>어케 알았음?</command>", messages[3]["content"])

    def test_json_in_reasoning_content_is_accepted(self):
        self.state.message = {
            "content": "", "reasoning_content": json.dumps(ANSWER),
        }
        self.assertEqual(self.brain.decide("안녕", NOW, None).line, "안녕!")

    def test_invalid_json_falls_back_to_rules(self):
        self.state.message = {"content": "죄송합니다"}
        self.assertEqual(self.brain.decide("모니야", NOW, None).emotion, "confused")

    def test_truncated_response_falls_back_to_rules(self):
        self.state.finish_reason = "length"
        self.assertEqual(self.brain.decide("심심해", NOW, None).emotion, "confused")

    def test_server_error_falls_back_to_rules(self):
        self.state.status = 500
        self.assertEqual(self.brain.decide("심심해", NOW, None).emotion, "confused")

    def test_stopped_server_falls_back_to_rules(self):
        brain = LocalBrain("http://127.0.0.1:1/v1", "m", 40, timeout=0.3)
        self.assertEqual(brain.decide("심심해", NOW, None).emotion, "confused")

    def test_device_commands_skip_the_model(self):
        cases = {
            "지금 몇 시야": "time",
            "화면 좀 어둡게 해줘": "none",
            "10초 타이머 맞춰줘": "timer",
        }
        for text, panel in cases.items():
            self.assertEqual(self.brain.decide(text, NOW, None).panel, panel)
        self.assertEqual(self.brain.decide("어둡게", NOW, None).brightness, 60)
        self.assertEqual(self.state.requests, [])


class BuildBrainTest(unittest.TestCase):
    def test_lmstudio(self):
        config = Config(llm=LlmConfig(provider="lmstudio", model="m"))
        self.assertIsInstance(build_brain(config), LocalBrain)

    def test_anthropic_without_key_uses_rules(self):
        brain = build_brain(Config())
        self.assertIs(type(brain), Brain)
        self.assertEqual(brain.decide("타이머 취소", NOW, None).timer_seconds, 0)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            build_brain(Config(llm=LlmConfig(provider="?")))


if __name__ == "__main__":
    unittest.main()
