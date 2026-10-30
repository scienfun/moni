"""명령 처리 흐름: think 표시 → 응답 생성 → 화면 갱신."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from . import EMOTIONS
from .brain import Decision
from .config import ScheduleConfig
from .device import DeviceClient, DeviceError
from .tools import TimerManager

log = logging.getLogger(__name__)

PANEL_X = 128
PANEL_Y = 8
MIN_TTL = 5
MAX_TTL = 120
LISTEN_TIMEOUT = 30


def is_night(now: datetime, start: str, end: str) -> bool:
    current = now.strftime("%H:%M")
    if start <= end:
        return start <= current < end
    return current >= start or current < end


class MoniService:
    def __init__(
        self,
        device: DeviceClient,
        brain,
        renderer,
        weather,
        schedule: ScheduleConfig,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.device = device
        self.brain = brain
        self.renderer = renderer
        self.weather = weather
        self.schedule = schedule
        self.clock = clock
        self.timer = TimerManager(self._timer_tick, self._timer_done)
        self._busy_until = 0.0
        self._sequence = 0
        self._page_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._night: bool | None = None
        self._timer_bucket: int | None = None

    def _safe(self, action: Callable, *args) -> bool:
        """기기가 꺼져 있어도 클라이언트 응답은 계속 돌려준다."""
        try:
            action(*args)
            return True
        except DeviceError as error:
            log.warning("기기 요청 실패: %s", error)
            return False

    @property
    def idle_emotion(self) -> str:
        s = self.schedule
        return "sleepy" if is_night(self.clock(), s.night_start, s.night_end) else "idle"

    def prepare(self) -> None:
        self._safe(self.device.prepare)

    def listen(self) -> None:
        self._safe(self.device.emotion, "listen")
        # 명령이 오지 않아도 귀 기울이는 표정에 멈춰 있지 않게 한다.
        self._safe(self.device.idle, LISTEN_TIMEOUT, self.idle_emotion)

    def health(self) -> dict:
        try:
            status = self.device.status()
            device = {
                "online": True,
                "fw": status.get("fw"),
                "heap": status.get("heap"),
            }
        except (DeviceError, ValueError):
            device = {"online": False}
        return {"bridge": "ok", "device": device}

    def _next_id(self, now: datetime) -> str:
        with self._lock:
            self._sequence += 1
            return f"c-{now:%Y%m%d}-{self._sequence:04d}"

    def _normalize(self, decision: Decision) -> Decision:
        if decision.emotion not in EMOTIONS:
            decision.emotion = "confused"
        decision.ttl = max(MIN_TTL, min(MAX_TTL, int(decision.ttl)))
        decision.speak = decision.speak or decision.line
        return decision

    def _panel_for(self, decision: Decision, now: datetime, weather: dict | None):
        if decision.panel == "weather" and weather:
            return {"type": "weather", "data": weather}
        if decision.panel == "time":
            return {"type": "time", "data": {"text": now.strftime("%H:%M")}}
        if decision.timer_seconds:
            return {"type": "timer", "data": {"remaining": decision.timer_seconds}}
        return None

    def _show(self, emotion: str, line: str, panel: dict | None, ttl: int) -> None:
        pages = self.renderer.bubble(line)
        self._busy_until = time.monotonic() + ttl
        if self._page_timer:
            self._page_timer.cancel()
            self._page_timer = None

        self._safe(
            self.device.show,
            emotion,
            pages[0],
            len(pages),
            self.renderer.panel(panel) if panel else None,
            ttl,
            self.idle_emotion,
        )

        if len(pages) > 1:
            self._page_timer = threading.Timer(
                ttl / 2, self._safe, (self.device.bubble, pages[1], 2, len(pages))
            )
            self._page_timer.daemon = True
            self._page_timer.start()

    def command(self, text: str) -> dict:
        started = time.monotonic()
        # LLM을 기다리지 않도록 think 표시는 따로 보낸다.
        think = threading.Thread(
            target=self._safe, args=(self.device.emotion, "think"), daemon=True
        )
        think.start()

        now = self.clock()
        weather = self.weather.current()
        decision = self._normalize(self.brain.decide(text, now, weather))
        think.join()

        if decision.timer_seconds == 0:
            self.timer.cancel()
        elif decision.timer_seconds:
            self._timer_bucket = None
            self.timer.start(decision.timer_seconds)
        if decision.brightness is not None:
            self._safe(self.device.brightness, decision.brightness)

        panel = self._panel_for(decision, now, weather)
        self._show(decision.emotion, decision.line, panel, decision.ttl)

        return {
            "id": self._next_id(now),
            "emotion": decision.emotion,
            "line": decision.line,
            "speak": decision.speak,
            "panel": panel,
            "ttl": decision.ttl,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }

    def _timer_tick(self, remaining: int) -> None:
        if time.monotonic() < self._busy_until:
            return
        # 기기마다 갱신 주기가 다르다. 순정은 갱신할 때마다 플래시에 쓴다.
        bucket = (remaining - 1) // self.device.timer_interval
        if bucket == self._timer_bucket:
            return
        self._timer_bucket = bucket
        panel = {"type": "timer", "data": {"remaining": remaining}}
        self._safe(self.device.panel, self.renderer.panel(panel), PANEL_X, PANEL_Y)

    def _timer_done(self) -> None:
        self._show("excited", "타이머 끝났어!", None, 15)

    def tick_schedule(self) -> None:
        """낮·밤이 바뀌면 대기 표정과 밝기를 바꾼다."""
        night = self.idle_emotion == "sleepy"
        if night == self._night:
            return
        s = self.schedule
        applied = self._safe(
            self.device.brightness, s.night_brightness if night else s.day_brightness
        )
        if applied and time.monotonic() >= self._busy_until:
            self._safe(self.device.emotion, self.idle_emotion)
        if applied:
            self._night = night

    def run_schedule(self, interval: float = 30) -> None:
        while True:
            self.tick_schedule()
            time.sleep(interval)
