"""OS를 감지해 브리지 자동 시작을 설치한다."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "bridge"
LABEL = "com.moni.bridge"


def macos_plist(uv: str) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-s", uv, "run", "--project", str(BRIDGE), "moni-bridge",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(Path.home() / "Library/Logs/moni-bridge.log"),
        "StandardErrorPath": str(Path.home() / "Library/Logs/moni-bridge.log"),
    }


def systemd_unit(uv: str) -> str:
    return f"""[Unit]
Description=Moni bridge
After=network-online.target

[Service]
ExecStart={uv} run --project {BRIDGE} moni-bridge
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def main() -> None:
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("uv를 찾을 수 없습니다.")

    if sys.platform == "darwin":
        path = Path.home() / f"Library/LaunchAgents/{LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(macos_plist(uv)))
        subprocess.run(["launchctl", "unload", str(path)], check=False)
        subprocess.run(["launchctl", "load", str(path)], check=True)
    else:
        path = Path.home() / ".config/systemd/user/moni-bridge.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(systemd_unit(uv), encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "moni-bridge"], check=True
        )
    print(f"설치 완료: {path}")


if __name__ == "__main__":
    main()
