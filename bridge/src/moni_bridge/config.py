"""moni.toml과 .env를 읽는다. 모든 OS에서 같은 형식을 쓴다."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BRIDGE_ROOT.parent


@dataclass
class DeviceConfig:
    backend: str = "stock"  # stock: 순정 펌웨어, custom: firmware/ 커스텀 펌웨어
    host: str = "moni.local"
    fallback_ip: str = ""
    timeout: float = 4.0


@dataclass
class LlmConfig:
    provider: str = "anthropic"  # anthropic | lmstudio
    model: str = "claude-haiku-5-5"
    max_line_chars: int = 40
    monthly_token_limit: int = 500_000
    base_url: str = "http://127.0.0.1:1234/v1"  # lmstudio 전용
    timeout: float = 30.0  # lmstudio 전용, 모델 로딩 시간을 포함한다


@dataclass
class ServerConfig:
    bind: str = "0.0.0.0"
    port: int = 8765


@dataclass
class ScheduleConfig:
    night_start: str = "23:00"
    night_end: str = "07:00"
    day_brightness: int = 200
    night_brightness: int = 30


@dataclass
class WeatherConfig:
    latitude: float = 37.5665
    longitude: float = 126.9780


@dataclass
class Secrets:
    anthropic_api_key: str = ""
    client_token: str = ""
    device_token: str = ""


@dataclass
class Config:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    secrets: Secrets = field(default_factory=Secrets)
    state_dir: Path = BRIDGE_ROOT / ".state"


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_config(root: Path = BRIDGE_ROOT) -> Config:
    data: dict = {}
    toml_path = root / "moni.toml"
    if toml_path.exists():
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))

    env = parse_env(root / ".env") | dict(os.environ)
    return Config(
        device=DeviceConfig(**data.get("device", {})),
        llm=LlmConfig(**data.get("llm", {})),
        server=ServerConfig(**data.get("server", {})),
        schedule=ScheduleConfig(**data.get("schedule", {})),
        weather=WeatherConfig(**data.get("weather", {})),
        secrets=Secrets(
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
            client_token=env.get("MONI_CLIENT_TOKEN", ""),
            device_token=env.get("MONI_DEVICE_TOKEN", ""),
        ),
        state_dir=root / ".state",
    )
