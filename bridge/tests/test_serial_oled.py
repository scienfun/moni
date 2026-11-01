"""USB 시리얼 OLED 백엔드 시험. pty로 보드를 흉내 내 실제 장치 파일로 통신한다."""

from __future__ import annotations

import base64
import io
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from moni_bridge import EMOTIONS
from moni_bridge.assetpack import EmotionFrames, build_pack
from moni_bridge.config import DeviceConfig
from moni_bridge.device import DeviceError, build_device
from moni_bridge.oled import (
    BAND_BYTES,
    FRAME_BYTES,
    TEXT_X,
    OledRenderer,
    face_layer,
    merge,
    pack_frame,
    unpack_frame,
)
from moni_bridge.serial_oled import SerialDevice


def sprite(shade: int) -> bytes:
    image = Image.new("RGB", (120, 120), (0, 0, 0))
    image.paste((255, 255, 255), (30, 30, 90, 90 + shade))
    output = io.BytesIO()
    image.save(output, "JPEG")
    return output.getvalue()


class FakeBoard:
    """pty의 마스터 쪽에서 펌웨어처럼 한 줄씩 받아 응답한다."""

    def __init__(self) -> None:
        self.master, slave = os.openpty()
        self.path = os.ttyname(slave)
        self._slave = slave  # 닫으면 장치 파일이 사라지므로 들고 있는다.
        self.received: list[str] = []
        self.reply = lambda line: "OK moni 1" if line == "PING" else "OK"
        self.mute = False
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        buffer = b""
        while not self._stop:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            buffer += chunk
            while b"\n" in buffer:
                raw, _, buffer = buffer.partition(b"\n")
                line = raw.decode().strip()
                self.received.append(line)
                if not self.mute:
                    os.write(self.master, (self.reply(line) + "\r\n").encode())

    def frames(self, command: str) -> list[bytes]:
        blob = base64.b64decode(command.split(" ", 2)[2])
        return [blob[i : i + FRAME_BYTES] for i in range(0, len(blob), FRAME_BYTES)]

    def close(self) -> None:
        # 슬레이브를 먼저 닫아야 macOS에서 마스터 close가 막히지 않는다.
        self._stop = True
        os.close(self._slave)
        self._thread.join(2)
        os.close(self.master)


class SerialDeviceTest(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        self.addCleanup(self.board.close)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        pack = Path(temp.name) / "pack.bin"
        pack.write_bytes(
            build_pack(
                [EmotionFrames(e, 5, [sprite(0), sprite(10)]) for e in EMOTIONS], 120, 120
            )
        )
        self.device = SerialDevice(
            DeviceConfig(backend="serial", port=self.board.path, timeout=1.0), pack
        )
        self.addCleanup(self.device._cancel_idle)
        self.addCleanup(self.device._drop)
        self.device._band_started = True  # 시험에서는 주기 갱신 스레드를 띄우지 않는다.
        self.device.attach(
            lambda: {"temp": 14, "icon": "sun", "pop": 20},
            lambda: datetime(2026, 11, 1, 14, 5),
        )
        self.renderer = OledRenderer()

    def animations(self) -> list[str]:
        return [line for line in self.board.received if line.startswith("ANIM")]

    def test_prepare_shows_the_big_idle_face(self):
        self.device.prepare()
        command = self.animations()[-1]
        self.assertTrue(command.startswith("ANIM 220 "))
        frames = self.board.frames(command)
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(f) == FRAME_BYTES for f in frames))
        # 큰 얼굴은 화면 가운데에 있어 오른쪽 끝 열은 비어 있다.
        image = unpack_frame(frames[0])
        self.assertEqual(image.getpixel((127, 32)), 0)
        self.assertGreater(sum(frames[0]), 0)

    def test_show_overlays_text_next_to_a_small_face(self):
        self.device.prepare()
        page = self.renderer.bubble("안녕! 반가워")[0]
        self.device.show("happy", page, 1, None, 60, "idle")
        command = self.animations()[-1]
        self.assertTrue(command.startswith("ANIM 300 "))
        for frame in self.board.frames(command):
            merged = unpack_frame(frame)
            text_only = unpack_frame(page)
            # 글자 층의 모든 흰 점이 합성 화면에 들어 있다.
            for x in range(TEXT_X, 128):
                for y in range(64):
                    if text_only.getpixel((x, y)):
                        self.assertTrue(merged.getpixel((x, y)))
            # 얼굴은 글자 영역 왼쪽에 있다.
            self.assertTrue(any(merged.getpixel((x, y)) for x in range(0, TEXT_X) for y in range(64)))

    def test_text_pages_and_idle_return(self):
        self.device.prepare()
        pages = self.renderer.bubble("가나다라마바사 " * 12)
        self.assertEqual(len(pages), 2)
        self.device.show("happy", pages[0], 2, None, 60, "idle")
        self.device.bubble(pages[1], 2, 2)
        first, second = self.animations()[-2:]
        self.assertNotEqual(first, second)
        self.device._go_idle("idle")
        self.assertTrue(self.animations()[-1].startswith("ANIM 220 "))

    def test_timer_panel_replaces_the_text_layer(self):
        self.device.prepare()
        layer = self.renderer.panel({"type": "timer", "data": {"remaining": 125}})
        self.device.panel(layer)
        self.assertEqual(len(self.board.frames(self.animations()[-1])), 2)
        self.assertEqual(len(layer), FRAME_BYTES)

    def test_brightness_maps_to_contrast(self):
        self.device.brightness(30)
        self.device.brightness(999)
        self.assertEqual(self.board.received[-2:], ["CONTRAST 30", "CONTRAST 255"])

    def test_status_reports_firmware(self):
        self.assertEqual(self.device.status()["fw"], "moni 1")

    def test_board_error_is_a_device_error(self):
        self.device.prepare()
        self.board.reply = lambda line: "ERR bad length 3"
        with self.assertRaises(DeviceError):
            self.device.brightness(10)

    def test_silent_board_times_out_and_reconnects_later(self):
        self.device.prepare()
        self.board.mute = True
        with self.assertRaises(DeviceError):
            self.device.brightness(10)
        self.assertIsNone(self.device._link)
        self.board.mute = False
        self.device.brightness(10)
        self.assertEqual(self.board.received[-1], "CONTRAST 10")

    def test_board_without_moni_firmware_is_reported(self):
        self.board.reply = lambda line: "ERR unknown"
        with self.assertRaisesRegex(DeviceError, "펌웨어"):
            self.device.prepare()

    def bands(self) -> list[bytes]:
        return [
            base64.b64decode(line.split(" ", 1)[1])
            for line in self.board.received
            if line.startswith("BAND ")
        ]

    def test_prepare_sends_the_yellow_band(self):
        self.device.prepare()
        band = self.bands()[-1]
        self.assertEqual(len(band), BAND_BYTES)
        self.assertGreater(sum(band), 0)

    def test_band_only_resent_when_it_changes(self):
        self.device.prepare()
        count = len(self.bands())
        self.device._publish_band()
        self.assertEqual(len(self.bands()), count)
        self.device._clock = lambda: datetime(2026, 11, 1, 14, 6)
        self.device._publish_band()
        self.assertEqual(len(self.bands()), count + 1)

    def test_status_text_replaces_the_info_while_thinking(self):
        self.device.prepare()
        before = self.bands()[-1]
        self.device.emotion("think")
        after = self.bands()[-1]
        self.assertNotEqual(before, after)
        # 응답 글자가 뜨면 다시 날짜·시각으로 돌아간다.
        self.device.show("happy", self.renderer.bubble("안녕")[0], 1, None, 60, "idle")
        self.assertNotEqual(self.bands()[-1], after)

    def test_band_pages(self):
        pages = self.device.band_pages(datetime(2026, 11, 1, 14, 5), {"temp": 14, "icon": "rain", "pop": 70})
        self.assertEqual(pages, [("11/1(일)", "14:05"), ("비 14도", "비 70%")])
        self.assertEqual(len(self.device.band_pages(datetime(2026, 11, 1, 14, 5), None)), 1)

    def test_band_is_resent_after_reconnect(self):
        self.device.prepare()
        count = len(self.bands())
        self.device._drop()
        self.device._publish_band()
        self.assertEqual(len(self.bands()), count + 1)

    def test_reconnect_marks_the_screen_for_resend(self):
        self.device.prepare()
        self.device._resync = False
        self.device._drop()
        self.device._publish_band()
        self.assertTrue(self.device._resync)

    def test_prepare_fills_the_screen_even_if_the_board_comes_later(self):
        self.board.reply = lambda line: "ERR unknown"
        with self.assertRaises(DeviceError):
            self.device.prepare()
        self.assertTrue(self.device._resync)

    def test_missing_port_is_searched_for(self):
        from unittest import mock

        self.device.config.port = "/dev/cu.usbmodem-gone"
        with mock.patch("moni_bridge.serial_oled.PORT_PATTERNS", (self.board.path,)):
            self.device.prepare()
        self.assertEqual(self.device._link.port, self.board.path)

    def test_unknown_emotion_is_rejected(self):
        with self.assertRaises(DeviceError):
            self.device.emotion("angry")


class OledRendererTest(unittest.TestCase):
    def setUp(self):
        self.renderer = OledRenderer()

    def test_pack_roundtrip(self):
        image = Image.new("1", (128, 64), 0)
        for point in ((0, 0), (127, 63), (5, 9), (64, 31)):
            image.putpixel(point, 1)
        frame = pack_frame(image)
        self.assertEqual(len(frame), FRAME_BYTES)
        self.assertEqual(frame[0], 1)  # (0,0): 첫 페이지 첫 열의 맨 아래 비트
        self.assertEqual(frame[7 * 128 + 127], 0x80)
        self.assertEqual(unpack_frame(frame).tobytes(), image.tobytes())

    def test_bubble_stays_right_of_the_face(self):
        for page in self.renderer.bubble("오늘은 맑고 14도야. 비 걱정은 없어!"):
            self.assertEqual(len(page), FRAME_BYTES)
            image = unpack_frame(page)
            self.assertEqual(
                sum(image.getpixel((x, y)) for x in range(TEXT_X - 1) for y in range(64)), 0
            )
            self.assertGreater(sum(page), 0)

    def test_long_text_is_cut_to_two_pages(self):
        self.assertEqual(len(self.renderer.bubble("가나다라 " * 40)), 2)

    def test_band_fits_in_sixteen_rows(self):
        band = self.renderer.band("11/1(일)", "14:05")
        self.assertEqual(len(band), BAND_BYTES)
        self.assertGreater(sum(band), 0)
        centered = self.renderer.band("생각 중...")
        self.assertNotEqual(band, centered)

    def test_area_below_the_band_only(self):
        for layer in (
            self.renderer.bubble("안녕! 반가워")[0],
            self.renderer.panel({"type": "timer", "data": {"remaining": 59}}),
            face_layer(sprite(0), 36, True),
            face_layer(sprite(0), 46, False),
        ):
            self.assertEqual(sum(layer[:BAND_BYTES]), 0)

    def test_missing_glyphs_become_question_marks(self):
        self.assertEqual(
            self.renderer.bubble("😀"), self.renderer.bubble("?")
        )

    def test_panels(self):
        for panel in (
            {"type": "timer", "data": {"remaining": 59}},
            {"type": "time", "data": {"text": "09:05"}},
            {"type": "weather", "data": {"temp": 14, "icon": "rain", "pop": 70}},
        ):
            self.assertEqual(len(self.renderer.panel(panel)), FRAME_BYTES)
        with self.assertRaises(ValueError):
            self.renderer.panel({"type": "x", "data": {}})

    def test_merge_ors_layers(self):
        self.assertEqual(merge(b"\x01" * FRAME_BYTES, None, b"\x02" * FRAME_BYTES), b"\x03" * FRAME_BYTES)
        self.assertEqual(merge(), bytes(FRAME_BYTES))

    def test_face_is_centered_alone_and_left_beside_text(self):
        data = sprite(0)
        alone = unpack_frame(face_layer(data, 60, False))
        beside = unpack_frame(face_layer(data, 44, True))
        self.assertTrue(any(alone.getpixel((x, 32)) for x in range(52, 76)))
        self.assertEqual(sum(beside.getpixel((x, y)) for x in range(TEXT_X, 128) for y in range(64)), 0)


class BuildDeviceTest(unittest.TestCase):
    def test_serial_backend(self):
        device = build_device(
            __import__("moni_bridge.config", fromlist=["Config"]).Config(
                device=DeviceConfig(backend="serial")
            )
        )
        self.assertIsInstance(device, SerialDevice)


if __name__ == "__main__":
    unittest.main()
