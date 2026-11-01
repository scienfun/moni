"""브리지 테스트. 표준 라이브러리만으로 실행된다."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

from moni_bridge.assetpack import EmotionFrames, build_pack, read_pack
from moni_bridge.brain import Brain, Decision, Usage, rule_decide
from moni_bridge.config import DeviceConfig, ScheduleConfig, parse_env
from moni_bridge.device import DeviceClient, DeviceError
from moni_bridge.layout import paginate, wrap_text
from moni_bridge.server import make_handler
from moni_bridge.service import MoniService, is_night
from moni_bridge.sim import serve
from moni_bridge.tools import TimerManager, parse_forecast, weather_icon

TOKEN = "device-token"
NOW = datetime(2026, 10, 30, 9, 5)
WEATHER = {"temp": 14, "icon": "rain", "pop": 70}


def measure(text: str) -> float:
    return len(text) * 10


class FakeRenderer:
    def __init__(self, pages: int = 1) -> None:
        self.pages = pages

    def bubble(self, text: str) -> list[bytes]:
        return [bytes(3120)] * self.pages

    def panel(self, panel: dict) -> bytes:
        return b"\xff\xd8jpeg"


class FakeWeather:
    def current(self) -> dict:
        return WEATHER


class FakeBrain:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    def decide(self, text, now, weather, history=None) -> Decision:
        self.histories = getattr(self, "histories", []) + [list(history or [])]
        return self.decision


class FakeMessages:
    def __init__(self, body: dict, stop_reason: str = "end_turn") -> None:
        self.body = body
        self.stop_reason = stop_reason
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = type("B", (), {"type": "text", "text": json.dumps(self.body)})
        usage = type("U", (), {"input_tokens": 100, "output_tokens": 20})
        return type(
            "R",
            (),
            {"content": [block], "usage": usage, "stop_reason": self.stop_reason},
        )


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


class LayoutTest(unittest.TestCase):
    def test_wraps_on_spaces(self):
        self.assertEqual(wrap_text("가나 다라 마바", measure, 50), ["가나 다라", "마바"])

    def test_breaks_long_word(self):
        self.assertEqual(wrap_text("가" * 7, measure, 30), ["가가가", "가가가", "가"])

    def test_overflow_is_trimmed_to_two_pages(self):
        lines = [f"줄{i}" for i in range(10)]
        pages = paginate(lines, 3, measure, 100)
        self.assertEqual(len(pages), 2)
        self.assertTrue(pages[-1][-1].endswith("..."))

    def test_empty_text_has_one_page(self):
        self.assertEqual(paginate([], 3, measure, 100), [[]])


class AssetPackTest(unittest.TestCase):
    def test_roundtrip(self):
        pack = build_pack(
            [EmotionFrames("idle", 6, [b"a", b"bb"]), EmotionFrames("think", 8, [b"ccc"])],
            120,
            120,
        )
        self.assertEqual(
            read_pack(pack), {"idle": [b"a", b"bb"], "think": [b"ccc"]}
        )

    def test_corruption_is_detected(self):
        pack = bytearray(build_pack([EmotionFrames("idle", 6, [b"abc"])], 120, 120))
        pack[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            read_pack(bytes(pack))


class RuleTest(unittest.TestCase):
    def test_timer(self):
        decision = rule_decide("25분 타이머", NOW, None)
        self.assertEqual(decision.timer_seconds, 1500)
        self.assertEqual(decision.emotion, "happy")

    def test_timer_cancel(self):
        self.assertEqual(rule_decide("타이머 취소", NOW, None).timer_seconds, 0)

    def test_rainy_weather_worries(self):
        decision = rule_decide("오늘 날씨 어때?", NOW, WEATHER)
        self.assertEqual((decision.emotion, decision.panel), ("worry", "weather"))

    def test_time(self):
        self.assertIn("9시 5분", rule_decide("지금 몇 시야", NOW, None).line)

    def test_brightness_number(self):
        self.assertEqual(rule_decide("밝기 100으로", NOW, None).brightness, 100)

    def test_unknown_is_confused(self):
        self.assertEqual(rule_decide("모니야 심심해", NOW, None).emotion, "confused")


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.usage = Usage(Path(self.temp.name) / "usage.json", 1000)

    def _answer(self):
        return {
            "emotion": "happy", "line": "안녕!", "speak": "안녕!", "panel": "none",
            "ttl": 20, "timer_seconds": None, "brightness": None,
        }

    def test_parses_structured_output_and_counts_usage(self):
        messages = FakeMessages(self._answer())
        brain = Brain("", "m", 40, self.usage, FakeClient(messages))
        self.assertEqual(brain.decide("안녕", NOW, None).line, "안녕!")
        self.assertIn("output_config", messages.kwargs)
        self.assertFalse(self.usage.exceeded())
        self.usage.add(900)
        self.assertTrue(self.usage.exceeded())

    def test_refusal_falls_back_to_rules(self):
        messages = FakeMessages(self._answer(), stop_reason="refusal")
        brain = Brain("", "m", 40, self.usage, FakeClient(messages))
        self.assertEqual(brain.decide("안녕", NOW, None).emotion, "confused")

    def test_over_limit_skips_llm(self):
        self.usage.add(5000)
        messages = FakeMessages(self._answer())
        brain = Brain("", "m", 40, self.usage, FakeClient(messages))
        brain.decide("안녕", NOW, None)
        self.assertEqual(messages.kwargs, {})


class ScheduleTest(unittest.TestCase):
    def test_overnight_window(self):
        self.assertTrue(is_night(datetime(2026, 1, 1, 23, 30), "23:00", "07:00"))
        self.assertTrue(is_night(datetime(2026, 1, 1, 6, 59), "23:00", "07:00"))
        self.assertFalse(is_night(datetime(2026, 1, 1, 7, 0), "23:00", "07:00"))


class ToolsTest(unittest.TestCase):
    def test_weather_icons(self):
        self.assertEqual(
            [weather_icon(c) for c in (0, 3, 61, 73)], ["sun", "cloud", "rain", "snow"]
        )

    def test_forecast_uses_max_upcoming_probability(self):
        payload = {
            "current": {"temperature_2m": 13.6, "weather_code": 61},
            "hourly": {"precipitation_probability": [90, 10, 40, None]},
        }
        self.assertEqual(
            parse_forecast(payload, 1), {"temp": 14, "icon": "rain", "pop": 40}
        )

    def test_timer_finishes_and_cancels(self):
        done = threading.Event()
        ticks: list[int] = []
        timer = TimerManager(ticks.append, done.set)
        timer.start(1)
        self.assertTrue(done.wait(3))
        self.assertTrue(ticks)

        cancelled = threading.Event()
        timer = TimerManager(lambda _: None, cancelled.set)
        timer.start(1)
        timer.cancel()
        self.assertFalse(cancelled.wait(1.5))


class EndToEndTest(unittest.TestCase):
    """브리지 → HTTP → 시뮬레이터까지 실제 소켓으로 돌린다."""

    def setUp(self):
        self.sim, self.state = serve(TOKEN)
        self.addCleanup(self.sim.shutdown)
        host = f"127.0.0.1:{self.sim.server_port}"
        self.device = DeviceClient(DeviceConfig(host=host, timeout=2), TOKEN)

    def _service(self, decision: Decision, pages: int = 1, device=None) -> MoniService:
        return MoniService(
            device or self.device,
            FakeBrain(decision),
            FakeRenderer(pages),
            FakeWeather(),
            ScheduleConfig(),
            clock=lambda: NOW,
        )

    def test_previous_turns_are_passed_to_the_brain_and_forgotten_later(self):
        service = self._service(Decision("happy", "12시야", "12시야", "time", 20))
        service.command("지금 몇 시야")
        service.command("어케 알았음?")
        histories = service.brain.histories
        self.assertEqual(histories[0], [])
        self.assertEqual([t for t, _ in histories[1]], ["지금 몇 시야"])
        service._history = [(h[0] - 10_000, h[1], h[2]) for h in service._history]
        service.command("그래?")
        self.assertEqual(service.brain.histories[2], [])

    def test_history_keeps_only_recent_turns(self):
        service = self._service(Decision("happy", "응", "응", "none", 20))
        for n in range(10):
            service.command(f"말 {n}")
        texts = [t for t, _ in service.brain.histories[-1]]
        self.assertEqual(texts, [f"말 {n}" for n in range(3, 9)])

    def test_command_updates_screen_and_returns_schema(self):
        service = self._service(Decision("worry", "우산!", "우산!", "weather", 20))
        result = service.command("오늘 날씨?")
        self.assertEqual(
            set(result),
            {"id", "emotion", "line", "speak", "panel", "ttl", "latency_ms"},
        )
        self.assertEqual(result["id"], "c-20261030-0001")
        self.assertEqual(result["panel"]["data"], WEATHER)
        self.assertEqual(self.state.emotion, "worry")
        self.assertEqual(len(self.state.bubble), 3120)
        self.assertIsNotNone(self.state.panel)
        self.assertEqual(self.state.idle_after, 20)

    def test_think_arrives_before_response(self):
        service = self._service(Decision("happy", "응", "응"))
        service.command("안녕")
        self.assertEqual(self.state.log[0], "POST /api/emotion")
        self.assertLess(
            self.state.log.index("POST /api/emotion"),
            self.state.log.index("POST /api/bubble"),
        )

    def test_response_without_panel_clears_previous_panel(self):
        self.state.panel = b"old"
        self._service(Decision("happy", "응", "응")).command("안녕")
        self.assertIsNone(self.state.panel)

    def test_ttl_is_clamped(self):
        result = self._service(Decision("happy", "응", "응", ttl=9999)).command("안녕")
        self.assertEqual(result["ttl"], 120)

    def test_unknown_emotion_becomes_confused(self):
        result = self._service(Decision("angry", "응", "응")).command("안녕")
        self.assertEqual(result["emotion"], "confused")

    def test_second_page_is_sent_later(self):
        service = self._service(Decision("happy", "긴 문장", "긴 문장", ttl=5), pages=2)
        service.command("안녕")
        self.assertEqual(self.state.page, (1, 2))
        service._page_timer.join(4)
        self.assertEqual(self.state.page, (2, 2))

    def test_night_uses_sleepy_idle(self):
        service = self._service(Decision("happy", "응", "응"))
        service.clock = lambda: datetime(2026, 10, 30, 23, 30)
        service.command("안녕")
        self.assertEqual(self.state.idle_emotion, "sleepy")
        service.tick_schedule()
        self.assertEqual(self.state.brightness, 30)

    def test_wrong_device_token_is_rejected(self):
        client = DeviceClient(self.device.config, "wrong")
        with self.assertRaises(DeviceError):
            client.status()

    def test_offline_device_still_answers(self):
        offline = DeviceClient(DeviceConfig(host="127.0.0.1:1", timeout=0.2), TOKEN)
        service = self._service(Decision("happy", "응", "응"), device=offline)
        self.assertEqual(service.command("안녕")["line"], "응")
        self.assertEqual(service.health()["device"], {"online": False})

    def test_fallback_ip_is_used(self):
        good = self.device.config.host
        client = DeviceClient(
            DeviceConfig(host="127.0.0.1:1", fallback_ip=good, timeout=0.2), TOKEN
        )
        self.assertEqual(client.status()["fw"], "sim")
        self.assertEqual(client._preferred, good)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.sim, self.state = serve(TOKEN)
        self.addCleanup(self.sim.shutdown)
        device = DeviceClient(
            DeviceConfig(host=f"127.0.0.1:{self.sim.server_port}", timeout=2), TOKEN
        )
        service = MoniService(
            device,
            FakeBrain(Decision("happy", "응", "응")),
            FakeRenderer(),
            FakeWeather(),
            ScheduleConfig(),
            clock=lambda: NOW,
        )
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, "client"))
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.addCleanup(self.api.shutdown)

    def _call(self, method, path, body=None, token="client"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.api.server_port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}"},
            method=method,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_requires_token(self):
        self.assertEqual(self._call("GET", "/v1/health", token="no")[0], 401)

    def test_health(self):
        status, body = self._call("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["device"], {"online": True, "fw": "sim", "heap": 31240})

    def test_listen_is_accepted_and_shows_listen(self):
        self.assertEqual(self._call("POST", "/v1/listen", {"client": "mac"})[0], 202)
        deadline = time.monotonic() + 2
        while self.state.emotion != "listen" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.state.emotion, "listen")

    def test_command(self):
        status, body = self._call("POST", "/v1/command", {"text": "안녕"})
        self.assertEqual((status, body["line"]), (200, "응"))

    def test_blank_command_is_rejected(self):
        self.assertEqual(self._call("POST", "/v1/command", {"text": "  "})[0], 400)


class EnvTest(unittest.TestCase):
    def test_parse_env(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("# c\nA=1\nB='two'\n", encoding="utf-8")
            self.assertEqual(parse_env(path), {"A": "1", "B": "two"})


if __name__ == "__main__":
    unittest.main()
