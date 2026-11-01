"""USB 시리얼로 연결한 RP2040 + SSD1306 OLED(128x64) 백엔드.

노랑 2색 패널의 위 띠에는 날짜·시각·날씨·상태 문구를, 파랑 영역에는 표정과 응답을 둔다.
표정은 얼굴 프레임을 반복 재생하고, 응답은 얼굴 옆에 글자를 겹쳐 보낸다.
화면은 보드 펌웨어(firmware-rp2040/main.py)가 한 줄 명령으로 받는다. 네트워크가 없고
플래시도 쓰지 않으므로 갱신이 빠르다. macOS·Linux 전용(termios)이다.
"""

from __future__ import annotations

import base64
import glob
import logging
import os
import select
import termios
import threading
import time
from datetime import datetime
from typing import Callable

from . import EMOTIONS
from .assetpack import read_pack
from .config import REPO_ROOT, DeviceConfig
from .device import DeviceError
from .oled import BIG_FACE, SMALL_FACE, OledRenderer, face_layer, merge

log = logging.getLogger(__name__)

PACK_PATH = REPO_ROOT / "assets" / "build" / "moni-assets.bin"
PORT_PATTERNS = ("/dev/cu.usbmodem*", "/dev/ttyACM*")
IDLE_FRAME_MS = 220
TEXT_FRAME_MS = 300
FIRMWARE_VERSION = "moni 2"
BAND_PAGE_SECONDS = 4
WEEKDAYS = "월화수목금토일"
WEATHER_NAMES = {"sun": "맑음", "cloud": "흐림", "rain": "비", "snow": "눈"}
STATUS_TEXT = {"think": "생각 중...", "listen": "듣는 중..."}


class SerialLink:
    """한 줄 명령을 보내고 한 줄 응답을 기다린다."""

    def __init__(self, port: str) -> None:
        self.port = port
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self._buffer = b""

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _write(self, data: bytes, deadline: float) -> None:
        view = memoryview(data)
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("쓰기 시간 초과")
            _, writable, _ = select.select([], [self.fd], [], remaining)
            if writable:
                try:
                    view = view[os.write(self.fd, view) :]
                except BlockingIOError:
                    pass

    def _read_line(self, deadline: float) -> str:
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("응답 시간 초과")
            readable, _, _ = select.select([self.fd], [], [], remaining)
            if readable:
                try:
                    chunk = os.read(self.fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise OSError("포트가 닫혔습니다")
                self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8", "replace").strip()

    def command(self, text: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        self._buffer = b""  # 이전 응답의 찌꺼기를 버린다.
        self._write(text.encode() + b"\n", deadline)
        while True:
            line = self._read_line(deadline)
            if line.startswith(("OK", "ERR")):
                return line


def find_port(preferred: str) -> str:
    """설정한 포트가 있으면 그 포트만, 없으면 PING에 답하는 보드를 찾는다.

    USB 구멍을 바꾸면 포트 이름이 바뀌므로, 설정한 포트가 사라졌을 때도 찾아 본다.
    """
    if preferred and os.path.exists(preferred):
        candidates = [preferred]
    else:
        candidates = []
        for pattern in PORT_PATTERNS:
            candidates += sorted(glob.glob(pattern))
    for port in candidates:
        try:
            link = SerialLink(port)
        except OSError:
            continue
        try:
            if link.command("PING", 1.5).startswith("OK moni"):
                return port
        except (OSError, TimeoutError):
            pass
        finally:
            link.close()
    raise DeviceError(
        "모니 펌웨어가 올라간 보드를 찾지 못했습니다. USB 연결과 `make rp2040-install`을 확인하세요."
    )


class SerialDevice:
    timer_interval = 1

    def __init__(self, config: DeviceConfig, pack_path=PACK_PATH) -> None:
        self.config = config
        self.pack_path = pack_path
        self._lock = threading.RLock()
        self._link: SerialLink | None = None
        self._frames: dict[str, list[bytes]] | None = None
        self._faces: dict[tuple[str, bool], list[bytes]] = {}
        self._emotion = "idle"
        self._text: bytes | None = None
        self._idle_timer: threading.Timer | None = None
        self._renderer: OledRenderer | None = None
        self._weather: Callable[[], dict | None] = lambda: None
        self._clock: Callable[[], datetime] = datetime.now
        self._band_started = False
        self._resync = False
        self._band_sent: bytes | None = None

    def attach(self, weather: Callable[[], dict | None], clock: Callable[[], datetime]) -> None:
        """띠에 보여 줄 날씨와 시계를 받는다."""
        self._weather = weather
        self._clock = clock

    # 연결

    def _connect(self) -> SerialLink:
        if self._link is None:
            self._link = SerialLink(find_port(self.config.port))
            log.info("OLED 보드 연결: %s", self._link.port)
            self._resync = True  # 보드가 새로 켜졌을 수 있어 화면을 다시 보낸다.
        return self._link

    def _send(self, text: str, timeout: float | None = None) -> str:
        with self._lock:
            try:
                reply = self._connect().command(text, timeout or self.config.timeout)
            except (OSError, TimeoutError) as error:
                self._drop()
                raise DeviceError(f"OLED 보드 통신 실패 ({error})") from error
            if reply.startswith("ERR"):
                raise DeviceError(f"OLED 보드 오류: {reply}")
            return reply

    def _drop(self) -> None:
        self._band_sent = None  # 다시 연결되면 띠를 새로 보낸다.
        if self._link:
            self._link.close()
            self._link = None

    # 화면

    def _load_frames(self) -> dict[str, list[bytes]]:
        if self._frames is None:
            if not self.pack_path.exists():
                raise DeviceError(f"{self.pack_path}가 없습니다. `make assets`를 실행하세요.")
            self._frames = read_pack(self.pack_path.read_bytes())
        return self._frames

    def _face(self, emotion: str, beside_text: bool) -> list[bytes]:
        key = (emotion, beside_text)
        if key not in self._faces:
            size = SMALL_FACE if beside_text else BIG_FACE
            self._faces[key] = [
                face_layer(sprite, size, beside_text)
                for sprite in self._load_frames()[emotion]
            ]
        return self._faces[key]

    def _publish(self) -> None:
        with self._lock:
            faces = self._face(self._emotion, self._text is not None)
            frames = [merge(face, self._text) for face in faces]
            delay = TEXT_FRAME_MS if self._text else IDLE_FRAME_MS
            payload = base64.b64encode(b"".join(frames)).decode()
            self._send(f"ANIM {delay} {payload}")
        self._publish_band_quietly()

    def _publish_band_quietly(self) -> None:
        """생각 중·듣는 중 같은 상태 문구가 표정 전환과 함께 바뀌게 한다."""
        try:
            self._publish_band()
        except DeviceError as error:
            log.debug("띠 갱신 실패: %s", error)

    # 노랑 띠

    def band_pages(self, now: datetime, weather: dict | None) -> list[tuple[str, str]]:
        """대기 중에 돌려 보여 줄 (왼쪽, 오른쪽) 문구들."""
        pages = [(f"{now.month}/{now.day}({WEEKDAYS[now.weekday()]})", f"{now:%H:%M}")]
        if weather:
            sky = WEATHER_NAMES.get(weather.get("icon"), "")
            pages.append((f"{sky} {weather['temp']}도".strip(), f"비 {weather['pop']}%"))
        return pages

    def _band_text(self) -> tuple[str, str]:
        status = STATUS_TEXT.get(self._emotion)
        if status and self._text is None:
            return status, ""
        now = self._clock()
        pages = self.band_pages(now, self._weather())
        return pages[int(now.timestamp() // BAND_PAGE_SECONDS) % len(pages)]

    def _publish_band(self, force: bool = False) -> None:
        if self._renderer is None:
            self._renderer = OledRenderer()
        band = self._renderer.band(*self._band_text())
        with self._lock:
            if force or band != self._band_sent:
                self._send(f"BAND {base64.b64encode(band).decode()}")
                self._band_sent = band

    def _band_loop(self) -> None:
        while True:
            time.sleep(1)
            try:
                if self._resync:
                    self._resync = False
                    self._publish()
                self._publish_band()
            except Exception as error:  # 보드가 꺼져 있어도 다음 주기에 다시 시도한다.
                log.debug("띠 갱신 실패: %s", error)

    def _start_band(self) -> None:
        if not self._band_started:
            self._band_started = True
            threading.Thread(target=self._band_loop, daemon=True).start()

    # 공통 기기 API

    def status(self) -> dict:
        reply = self._send("PING")
        return {"fw": reply.removeprefix("OK ") or FIRMWARE_VERSION, "heap": None, "model": "RP2040+SSD1306"}

    def prepare(self) -> None:
        self._load_frames()
        self._emotion = "idle"
        self._text = None
        # 보드가 꺼져 있어도 주기 갱신은 시작해, 나중에 연결되면 화면을 채운다.
        self._start_band()
        self._resync = True
        self._publish()
        self._publish_band(force=True)

    def upload_assets(self, blob: bytes) -> None:
        with self._lock:
            self._frames = read_pack(blob)
            self._faces.clear()
            self._publish()

    def _cancel_idle(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def emotion(self, name: str) -> None:
        if name not in EMOTIONS:
            raise DeviceError(f"알 수 없는 표정: {name}")
        with self._lock:
            self._cancel_idle()
            self._emotion = name
            self._text = None
            self._publish()

    def show(
        self,
        emotion: str,
        bubble: bytes,
        pages: int,
        panel: bytes | None,
        ttl: int,
        idle_emotion: str,
    ) -> None:
        # 글자 층에 답이 이미 들어 있어 패널은 겹치지 않는다. 패널은 타이머 갱신에만 쓴다.
        with self._lock:
            self._cancel_idle()
            self._emotion = emotion
            self._text = bubble
            self._publish()
            self.idle(ttl, idle_emotion)

    def bubble(self, bitmap: bytes, page: int = 1, of: int = 1) -> None:
        with self._lock:
            self._text = bitmap
            self._publish()

    def panel(self, jpeg: bytes, x: int = 128, y: int = 8) -> None:
        # OledRenderer.panel은 글자 층을 돌려준다(인자 이름만 공통 API를 따른다).
        with self._lock:
            self._text = jpeg
            self._publish()

    def idle(self, after: int, emotion: str = "idle") -> None:
        with self._lock:
            self._cancel_idle()
            self._idle_timer = threading.Timer(after, self._go_idle, (emotion,))
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _go_idle(self, emotion: str) -> None:
        try:
            self.emotion(emotion)
        except DeviceError as error:
            log.warning("대기 화면 복귀 실패: %s", error)

    def brightness(self, value: int) -> None:
        self._send(f"CONTRAST {max(0, min(255, value))}")
