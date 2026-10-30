"""순정 백엔드 테스트. 순정 HTTP API를 흉내 내는 서버와 실제 소켓으로 통신한다."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from PIL import Image

    from moni_bridge.render import FONT_PATH, Renderer
    AVAILABLE = FONT_PATH.exists()
except ImportError:
    AVAILABLE = False

from moni_bridge import EMOTIONS
from moni_bridge.assetpack import EmotionFrames, build_pack
from moni_bridge.brain import Decision
from moni_bridge.config import DeviceConfig, ScheduleConfig
from moni_bridge.device import DeviceError
from moni_bridge.service import MoniService
from moni_bridge.stock import StockDevice, build_emotion_gif, gif_name


class FakeStock:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"ezgif-1.jpg": b"user photo"}
        self.theme = 1
        self.image = ""
        self.brightness = 100
        self.calls: list[str] = []
        self.lock = threading.Lock()


def make_handler(state: FakeStock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _reply(self, body: bytes = b"OK"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            with state.lock:
                state.calls.append(self.path)
                if self.path == "/v.json":
                    return self._reply(b'{"m":"SmallTV-Ultra","v":"Ultra-V9.0.51"}')
                if self.path.startswith("/filelist"):
                    return self._reply(" ".join(state.files).encode())
                if self.path.startswith("/set?theme="):
                    state.theme = int(self.path.split("=")[1])
                elif self.path.startswith("/set?img=/image/"):
                    name = self.path.split("/image/")[1]
                    if name not in state.files:
                        self.send_error(404)
                        return
                    state.image = name
                elif self.path.startswith("/set?brt="):
                    state.brightness = int(self.path.split("=")[1])
                else:
                    self.send_error(404)
                    return
                self._reply()

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            with state.lock:
                state.calls.append(self.path)
                head, _, rest = body.partition(b"\r\n\r\n")
                name = head.split(b'filename="')[1].split(b'"')[0].decode()
                boundary = head.split(b"\r\n")[0]
                state.files[name] = rest[: rest.rfind(b"\r\n" + boundary)]
            self._reply()

    return Handler


def jpeg_frame(color) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 120), color).save(output, "JPEG", progressive=False)
    return output.getvalue()


@unittest.skipUnless(AVAILABLE, "Pillow 또는 폰트 없음")
class StockTest(unittest.TestCase):
    def setUp(self):
        self.state = FakeStock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        pack = Path(temp.name) / "pack.bin"
        pack.write_bytes(
            build_pack(
                [
                    EmotionFrames(
                        name,
                        6,
                        [jpeg_frame((i * 30, 80, 120)), jpeg_frame((i * 30, 200, 20))],
                    )
                    for i, name in enumerate(EMOTIONS)
                ],
                120,
                120,
            )
        )
        config = DeviceConfig(host=f"127.0.0.1:{self.server.server_port}", timeout=2)
        self.device = StockDevice(config, pack)
        self.addCleanup(self.device._cancel_idle)

    def _service(self, decision: Decision) -> MoniService:
        class Brain:
            def decide(self, *_):
                return decision

        class Weather:
            def current(self):
                return {"temp": 14, "icon": "rain", "pop": 70}

        return MoniService(
            self.device, Brain(), Renderer(), Weather(), ScheduleConfig(),
            clock=lambda: datetime(2026, 10, 30, 9, 5),
        )

    def test_prepare_uploads_gifs_and_keeps_user_photos(self):
        self.device.prepare()
        for emotion in EMOTIONS:
            self.assertIn(gif_name(emotion), self.state.files)
        self.assertEqual(self.state.files["ezgif-1.jpg"], b"user photo")
        self.assertEqual(self.state.theme, 3)
        self.assertEqual(self.state.image, "moni-idle.gif")

    def test_prepare_skips_existing_gifs(self):
        self.device.prepare()
        self.state.calls.clear()
        self.device.prepare()
        self.assertFalse([c for c in self.state.calls if c.startswith("/doUpload")])

    def test_uploaded_gif_is_valid_full_screen_animation(self):
        self.device.prepare()
        image = Image.open(io.BytesIO(self.state.files["moni-happy.gif"]))
        self.assertEqual(image.size, (240, 240))
        self.assertTrue(image.is_animated)

    def test_emotion_switch_does_not_write_flash(self):
        self.device.prepare()
        self.state.calls.clear()
        self.device.emotion("think")
        self.assertEqual(self.state.calls, ["/set?img=/image/moni-think.gif"])

    def test_command_shows_composed_jpeg(self):
        self.device.prepare()
        service = self._service(Decision("worry", "우산 챙겨!", "우산", "weather", 20))
        result = service.command("오늘 날씨?")
        self.assertEqual(result["emotion"], "worry")
        self.assertEqual(self.state.image, "moni-a.jpg")
        blob = self.state.files["moni-a.jpg"]
        image = Image.open(io.BytesIO(blob))
        self.assertEqual(image.size, (240, 240))
        self.assertNotIn("progressive", image.info)
        self.assertGreater(image.convert("L").crop((0, 136, 240, 240)).getextrema()[1], 200)

    def test_responses_alternate_files(self):
        self.device.prepare()
        service = self._service(Decision("happy", "응", "응"))
        service.command("하나")
        first = self.state.image
        service.command("둘")
        self.assertNotEqual(self.state.image, first)

    def test_returns_to_idle_after_ttl(self):
        self.device.prepare()
        self.device.show("happy", bytes(3120), 1, None, 1, "sleepy")
        deadline = time.monotonic() + 3
        while self.state.image != "moni-sleepy.gif" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.state.image, "moni-sleepy.gif")

    def test_new_response_cancels_pending_idle(self):
        self.device.prepare()
        self.device.show("happy", bytes(3120), 1, None, 1, "idle")
        self.device.show("happy", bytes(3120), 1, None, 30, "idle")
        time.sleep(1.3)
        self.assertTrue(self.state.image.endswith(".jpg"))

    def test_brightness_is_scaled_to_percent(self):
        self.device.brightness(255)
        self.assertEqual(self.state.brightness, 100)
        self.device.brightness(128)
        self.assertEqual(self.state.brightness, 50)

    def test_timer_panel_updates_only_each_interval(self):
        self.device.prepare()
        service = self._service(Decision("happy", "응", "응"))
        self.state.calls.clear()
        for remaining in (119, 118, 117, 61, 60, 59):
            service._timer_tick(remaining)
        uploads = [c for c in self.state.calls if c.startswith("/doUpload")]
        self.assertEqual(len(uploads), 2)

    def test_status(self):
        status = self.device.status()
        self.assertEqual(status["fw"], "Ultra-V9.0.51")

    def test_missing_pack_is_reported(self):
        device = StockDevice(self.device.config, Path("/없는/경로.bin"))
        with self.assertRaises(DeviceError):
            device.prepare()

    def test_gif_builder_loops_forever(self):
        blob = build_emotion_gif([jpeg_frame((1, 2, 3)), jpeg_frame((200, 100, 50))])
        self.assertEqual(Image.open(io.BytesIO(blob)).info.get("loop"), 0)


if __name__ == "__main__":
    unittest.main()
