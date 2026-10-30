"""디바이스 API 클라이언트. LAN 기기와 직접 통신하므로 프록시를 쓰지 않는다."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from .config import Config, DeviceConfig


class DeviceError(RuntimeError):
    pass


MULTIPART_BOUNDARY = "moni-boundary-7f3a"


def encode_multipart(
    blob: bytes,
    filename: str = "blob",
    content_type: str = "application/octet-stream",
) -> bytes:
    """ESP8266 웹서버는 multipart 업로드만 메모리에 쌓지 않고 스트리밍한다."""
    head = (
        f"--{MULTIPART_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    )
    return head.encode() + blob + f"\r\n--{MULTIPART_BOUNDARY}--\r\n".encode()


class DeviceClient:
    """커스텀 펌웨어용 백엔드."""

    timer_interval = 1

    def __init__(self, config: DeviceConfig, token: str) -> None:
        self.config = config
        self.token = token
        self._lock = threading.RLock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._preferred: str | None = None

    def _hosts(self) -> list[str]:
        hosts = [self.config.host, self.config.fallback_ip]
        if self._preferred:
            hosts.insert(0, self._preferred)
        return list(dict.fromkeys(h for h in hosts if h))

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str = "application/octet-stream",
        query: dict | None = None,
        timeout: float | None = None,
    ) -> bytes:
        suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
        headers = {"X-Moni-Token": self.token, "Connection": "close"}
        if body is not None:
            headers["Content-Type"] = content_type

        last_error: Exception | None = None
        # 기기는 요청을 한 번에 하나만 처리하므로 브리지에서 직렬화한다.
        with self._lock:
            for host in self._hosts():
                request = urllib.request.Request(
                    f"http://{host}{path}{suffix}",
                    data=body,
                    headers=headers,
                    method=method,
                )
                try:
                    with self._opener.open(
                        request, timeout=timeout or self.config.timeout
                    ) as response:
                        self._preferred = host
                        return response.read()
                except urllib.error.HTTPError as error:
                    raise DeviceError(f"{path}: HTTP {error.code}") from error
                except OSError as error:
                    last_error = error
        raise DeviceError(f"{path}: 기기에 연결할 수 없습니다 ({last_error})")

    def _upload(self, path: str, blob: bytes, query=None, timeout=None) -> None:
        self._request(
            "POST",
            path,
            encode_multipart(blob),
            f"multipart/form-data; boundary={MULTIPART_BOUNDARY}",
            query,
            timeout,
        )

    def prepare(self) -> None:
        pass

    def show(
        self,
        emotion: str,
        bubble: bytes,
        pages: int,
        panel: bytes | None,
        ttl: int,
        idle_emotion: str,
    ) -> None:
        self.emotion(emotion)
        if panel:
            self.panel(panel)
        else:
            self.clear_panel()
        self.bubble(bubble, 1, pages)
        self.idle(ttl, idle_emotion)

    def status(self) -> dict:
        return json.loads(self._request("GET", "/api/status"))

    def emotion(self, name: str) -> None:
        body = json.dumps({"name": name}).encode()
        self._request("POST", "/api/emotion", body, "application/json")

    def bubble(self, bitmap: bytes, page: int = 1, of: int = 1) -> None:
        self._upload("/api/bubble", bitmap, {"page": page, "of": of})

    def clear_bubble(self) -> None:
        self._request("DELETE", "/api/bubble")

    def panel(self, jpeg: bytes, x: int = 128, y: int = 8) -> None:
        self._upload("/api/panel", jpeg, {"x": x, "y": y})

    def clear_panel(self) -> None:
        self._request("DELETE", "/api/panel")

    def idle(self, after: int, emotion: str = "idle") -> None:
        self._request("POST", "/api/idle", b"", query={"after": after, "emotion": emotion})

    def brightness(self, value: int) -> None:
        body = json.dumps({"value": max(0, min(255, value))}).encode()
        self._request("POST", "/api/brightness", body, "application/json")

    def upload_assets(self, blob: bytes) -> None:
        self._upload("/api/assets", blob, timeout=60)


def build_device(config: Config):
    if config.device.backend == "stock":
        from .stock import StockDevice

        return StockDevice(config.device)
    if config.device.backend == "custom":
        return DeviceClient(config.device, config.secrets.device_token)
    raise ValueError(f"알 수 없는 backend: {config.device.backend}")
