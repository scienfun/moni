"""순정 SmallTV-Ultra 펌웨어용 백엔드.

플래싱 없이 순정 HTTP API로 표시한다.
  표정: 전체 화면 GIF를 한 번 올려 두고 /set?img= 로 전환한다(플래시 쓰기 없음).
  응답: 캐릭터·패널·말풍선을 JPEG 한 장으로 합성해 올린다(응답마다 플래시 쓰기).
  대기 복귀: 기기에 예약 기능이 없어 브리지가 타이머로 처리한다.
"""

from __future__ import annotations

import http.client
import io
import json
import logging
import threading
import urllib.error
import urllib.request

from . import EMOTIONS
from .assetpack import read_pack
from .config import REPO_ROOT, DeviceConfig
from .device import DeviceError, encode_multipart, MULTIPART_BOUNDARY

log = logging.getLogger(__name__)

PACK_PATH = REPO_ROOT / "assets" / "build" / "moni-assets.bin"
CUSTOM_IMAGE_THEME = 3
RESPONSE_FILES = ("moni-a.jpg", "moni-b.jpg")
JPEG_QUALITY = 75
GIF_COLORS = 32
GIF_FRAME_MS = 200
UPLOAD_TIMEOUT = 15


def gif_name(emotion: str) -> str:
    return f"moni-{emotion}.gif"


def build_emotion_gif(frames: list[bytes]) -> bytes:
    from PIL import Image

    images = [
        Image.open(io.BytesIO(frame))
        .convert("RGB")
        .resize((240, 240), Image.Resampling.LANCZOS)
        .convert("P", palette=Image.Palette.ADAPTIVE, colors=GIF_COLORS)
        for frame in frames
    ]
    output = io.BytesIO()
    images[0].save(
        output,
        "GIF",
        save_all=True,
        append_images=images[1:],
        duration=GIF_FRAME_MS,
        loop=0,
        optimize=True,
    )
    return output.getvalue()


class StockDevice:
    # 타이머 패널 갱신 간격(초). 갱신할 때마다 플래시에 쓰므로 길게 잡는다.
    timer_interval = 60

    def __init__(self, config: DeviceConfig, pack_path=PACK_PATH) -> None:
        self.config = config
        self.pack_path = pack_path
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._lock = threading.RLock()
        self._frames: dict[str, list[bytes]] | None = None
        self._emotion = "idle"
        self._panel: bytes | None = None
        self._bubble: bytes | None = None
        self._next_file = 0
        self._idle_timer: threading.Timer | None = None

    def _hosts(self) -> list[str]:
        return [h for h in (self.config.host, self.config.fallback_ip) if h]

    def _send(
        self, request_for, timeout: float, tolerate_bad_response: bool = False
    ) -> bytes:
        last_error: Exception | None = None
        with self._lock:
            for host in self._hosts():
                try:
                    with self._opener.open(request_for(host), timeout=timeout) as response:
                        return response.read()
                except urllib.error.HTTPError as error:
                    raise DeviceError(f"HTTP {error.code}: {error.url}") from error
                except http.client.HTTPException as error:
                    if tolerate_bad_response:
                        return b""
                    last_error = error
                except OSError as error:
                    last_error = error
        raise DeviceError(f"기기에 연결할 수 없습니다 ({last_error})")

    def _get(self, path: str) -> bytes:
        # 경로의 "/"가 인코딩되면 펌웨어가 파일을 찾지 못하므로 그대로 보낸다.
        return self._send(
            lambda host: urllib.request.Request(f"http://{host}{path}"),
            self.config.timeout,
        )

    def _upload(self, filename: str, blob: bytes, content_type: str) -> None:
        body = encode_multipart(blob, filename, content_type)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={MULTIPART_BOUNDARY}"
        }

        def request_for(host: str) -> urllib.request.Request:
            url = f"http://{host}/doUpload?dir=/image/"
            return urllib.request.Request(url, body, headers, method="POST")

        # 순정 펌웨어는 업로드 뒤 규격에 어긋난 응답을 보낼 때가 있다.
        # 파일은 저장되므로 응답 해석 오류는 넘긴다.
        self._send(request_for, UPLOAD_TIMEOUT, tolerate_bad_response=True)

    def _select(self, filename: str) -> None:
        self._get(f"/set?img=/image/{filename}")

    def _load_frames(self) -> dict[str, list[bytes]]:
        if self._frames is None:
            if not self.pack_path.exists():
                raise DeviceError(f"{self.pack_path}가 없습니다. `make assets`를 실행하세요.")
            self._frames = read_pack(self.pack_path.read_bytes())
        return self._frames

    def _cancel_idle(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _publish(self) -> None:
        """현재 화면 상태를 JPEG로 합성해 표시한다."""
        from .render import compose_screen

        with self._lock:
            sprite = self._load_frames()[self._emotion][0]
            image = compose_screen(sprite, self._panel, self._bubble)
            output = io.BytesIO()
            image.save(
                output, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=False
            )
            # 같은 이름을 다시 고르면 화면이 안 바뀔 수 있어 두 파일을 번갈아 쓴다.
            filename = RESPONSE_FILES[self._next_file]
            self._next_file ^= 1
            self._upload(filename, output.getvalue(), "image/jpeg")
            self._select(filename)

    def status(self) -> dict:
        version = json.loads(self._get("/v.json"))
        return {"fw": version.get("v"), "heap": None, "model": version.get("m")}

    def prepare(self) -> None:
        """사진 표시 모드로 전환하고 빠진 표정 GIF를 올린다."""
        listing = self._get("/filelist?dir=/image/").decode("utf-8", "replace")
        missing = [e for e in EMOTIONS if gif_name(e) not in listing]
        if missing:
            log.info("표정 GIF 업로드: %s", ", ".join(missing))
            self._upload_emotions(missing)
        self._get(f"/set?theme={CUSTOM_IMAGE_THEME}")
        self._emotion = "idle"
        self._select(gif_name("idle"))

    def _upload_emotions(self, emotions: list[str]) -> None:
        frames = self._load_frames()
        for emotion in emotions:
            self._upload(gif_name(emotion), build_emotion_gif(frames[emotion]), "image/gif")

    def upload_assets(self, blob: bytes) -> None:
        self._frames = read_pack(blob)
        self._upload_emotions(list(EMOTIONS))

    def emotion(self, name: str) -> None:
        with self._lock:
            self._cancel_idle()
            self._emotion = name
            self._panel = None
            self._bubble = None
            self._select(gif_name(name))

    def show(
        self,
        emotion: str,
        bubble: bytes,
        pages: int,
        panel: bytes | None,
        ttl: int,
        idle_emotion: str,
    ) -> None:
        with self._lock:
            self._cancel_idle()
            self._emotion = emotion
            self._bubble = bubble
            self._panel = panel
            self._publish()
            self.idle(ttl, idle_emotion)

    def bubble(self, bitmap: bytes, page: int = 1, of: int = 1) -> None:
        with self._lock:
            self._bubble = bitmap
            self._publish()

    def panel(self, jpeg: bytes, x: int = 128, y: int = 8) -> None:
        with self._lock:
            self._panel = jpeg
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
        # 공통 API는 0~255, 순정 펌웨어는 0~100이다.
        self._get(f"/set?brt={round(max(0, min(255, value)) * 100 / 255)}")
