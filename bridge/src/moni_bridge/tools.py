"""날씨·타이머 도구."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable

from .config import WeatherConfig

# WMO 날씨 코드 → 패널 아이콘
def weather_icon(code: int) -> str:
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code >= 51:
        return "rain"
    if code >= 2:
        return "cloud"
    return "sun"


def parse_forecast(payload: dict, hour: int) -> dict:
    current = payload["current"]
    probabilities = payload["hourly"]["precipitation_probability"][hour : hour + 12]
    return {
        "temp": round(current["temperature_2m"]),
        "icon": weather_icon(int(current["weather_code"])),
        "pop": max((p for p in probabilities if p is not None), default=0),
    }


class WeatherService:
    """Open-Meteo를 쓴다. API 키가 필요 없고 응답은 10분간 캐시한다."""

    TTL = 600
    RETRY_AFTER = 60  # 실패 뒤 명령마다 타임아웃을 기다리지 않도록 쉰다.

    def __init__(self, config: WeatherConfig) -> None:
        self.config = config
        self._cached: dict | None = None
        self._next_fetch = 0.0
        self._lock = threading.Lock()

    def current(self) -> dict | None:
        with self._lock:
            if time.monotonic() < self._next_fetch:
                return self._cached
            self._next_fetch = time.monotonic() + self.RETRY_AFTER
            query = urllib.parse.urlencode(
                {
                    "latitude": self.config.latitude,
                    "longitude": self.config.longitude,
                    "current": "temperature_2m,weather_code",
                    "hourly": "precipitation_probability",
                    "forecast_days": 2,
                    "timezone": "Asia/Seoul",
                }
            )
            url = f"https://api.open-meteo.com/v1/forecast?{query}"
            try:
                with urllib.request.urlopen(url, timeout=2.5) as response:
                    payload = json.load(response)
                self._cached = parse_forecast(payload, time.localtime().tm_hour)
                self._next_fetch = time.monotonic() + self.TTL
            except (OSError, KeyError, ValueError):
                return self._cached
            return self._cached


class TimerManager:
    """타이머 하나를 돌리며 매초 남은 시간을 알린다."""

    def __init__(
        self, on_tick: Callable[[int], None], on_done: Callable[[], None]
    ) -> None:
        self.on_tick = on_tick
        self.on_done = on_done
        self._generation = 0
        self._lock = threading.Lock()

    def start(self, seconds: int) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._run, args=(generation, seconds), daemon=True
        ).start()

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1

    def _alive(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _run(self, generation: int, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while self._alive(generation):
            remaining = round(deadline - time.monotonic())
            if remaining <= 0:
                self.on_done()
                return
            self.on_tick(remaining)
            time.sleep(1)
