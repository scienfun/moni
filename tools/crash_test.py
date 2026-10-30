"""M3 수용 시험: moni-debug 펌웨어를 3회 연속 크래시시켜 안전 모드 진입을 확인한다."""

from __future__ import annotations

import time

from moni_bridge.config import load_config
from moni_bridge.device import DeviceClient, DeviceError


def main() -> None:
    config = load_config()
    client = DeviceClient(config.device, config.secrets.device_token)
    for attempt in range(1, 4):
        for _ in range(20):
            try:
                client._request("POST", "/api/debug/crash", b"", timeout=1.5)
                break
            except DeviceError:
                time.sleep(0.5)
        print(f"크래시 {attempt}/3")
    print("이제 화면에 Safe mode가 뜨고 Wi-Fi 목록에 Moni-Recovery가 보여야 합니다.")


if __name__ == "__main__":
    main()
