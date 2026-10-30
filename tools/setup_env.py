"""설정 파일을 복사하고 비어 있는 토큰을 생성한다. 이미 있는 값은 건드리지 않는다."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "bridge"
GENERATED = ("MONI_CLIENT_TOKEN", "MONI_DEVICE_TOKEN", "MONI_OTA_PASSWORD")


def main() -> None:
    if not (BRIDGE / "moni.toml").exists():
        shutil.copy(BRIDGE / "moni.toml.example", BRIDGE / "moni.toml")
    env_path = BRIDGE / ".env"
    if not env_path.exists():
        shutil.copy(BRIDGE / ".env.example", env_path)

    lines = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key in GENERATED and not value:
            line = f"{key}={secrets.token_urlsafe(24)}"
        lines.append(line)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
    print("bridge/moni.toml, bridge/.env 준비 완료")


if __name__ == "__main__":
    main()
