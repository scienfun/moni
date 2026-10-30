"""SmallTV 없이 디바이스 API를 시험하는 시뮬레이터.

요청 규격(토큰, 크기, 표정 이름)은 펌웨어와 같게 검사한다. Pillow가 있으면
http://127.0.0.1:8081/ 에서 240×240 화면을 볼 수 있다.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import EMOTIONS
from .config import REPO_ROOT, load_config

BUBBLE_BYTES = 3120
PANEL_MAX_BYTES = 12 * 1024
PAGE = b"""<!doctype html><meta charset=utf-8><title>Moni Sim</title>
<body style="background:#111;display:grid;place-items:center;height:100vh;margin:0">
<img id=s width=480 height=480 style="image-rendering:pixelated">
<script>setInterval(()=>s.src='/screen.png?'+Date.now(),300)</script>"""


def decode_multipart(content_type: str, body: bytes) -> bytes | None:
    """펌웨어처럼 multipart 파일 파트만 받는다. 아니면 None."""
    if not content_type.startswith("multipart/form-data; boundary="):
        return None
    boundary = content_type.split("boundary=", 1)[1].encode()
    start = body.find(b"\r\n\r\n")
    end = body.rfind(b"\r\n--" + boundary + b"--")
    if start < 0 or end < 0:
        return None
    return body[start + 4 : end]


class SimState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.emotion = "idle"
        self.bubble: bytes | None = None
        self.page = (1, 1)
        self.panel: bytes | None = None
        self.brightness = 200
        self.idle_after: int | None = None
        self.idle_emotion = "idle"
        self.log: list[str] = []
        self.assets: dict[str, list[bytes]] = {}

    def screen_png(self) -> bytes:
        from .render import compose_screen

        frames = self.assets.get(self.emotion)
        image = compose_screen(frames[0] if frames else None, self.panel, self.bubble)
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()


def make_handler(state: SimState, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

        def _send(self, status: int, body: bytes = b"{}", kind="application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))

        def _route(self) -> tuple[str, dict]:
            parts = urllib.parse.urlsplit(self.path)
            return parts.path, dict(urllib.parse.parse_qsl(parts.query))

        def _authorized(self) -> bool:
            return self.headers.get("X-Moni-Token") == token

        def do_GET(self) -> None:
            path, _ = self._route()
            if path == "/":
                return self._send(200, PAGE, "text/html")
            if path == "/screen.png":
                return self._send(200, state.screen_png(), "image/png")
            if not self._authorized():
                return self._send(401)
            if path == "/api/status":
                body = {"fw": "sim", "heap": 31240, "rssi": -50, "emotion": state.emotion}
                return self._send(200, json.dumps(body).encode())
            self._send(404)

        def do_DELETE(self) -> None:
            path, _ = self._route()
            if not self._authorized():
                return self._send(401)
            with state.lock:
                state.log.append(f"DELETE {path}")
                if path == "/api/bubble":
                    state.bubble = None
                elif path == "/api/panel":
                    state.panel = None
                else:
                    return self._send(404)
            self._send(200)

        def do_POST(self) -> None:
            path, query = self._route()
            body = self._body()
            if not self._authorized():
                return self._send(401)
            if path in ("/api/bubble", "/api/panel", "/api/assets"):
                body = decode_multipart(self.headers.get("Content-Type", ""), body)
                if body is None:
                    return self._send(400)
            with state.lock:
                state.log.append(f"POST {path}")
                if path == "/api/emotion":
                    name = json.loads(body).get("name")
                    if name not in EMOTIONS:
                        return self._send(400)
                    state.emotion = name
                elif path == "/api/bubble":
                    if len(body) != BUBBLE_BYTES:
                        return self._send(400)
                    state.bubble = body
                    state.page = (int(query["page"]), int(query["of"]))
                elif path == "/api/panel":
                    if not body or len(body) > PANEL_MAX_BYTES:
                        return self._send(400)
                    state.panel = body
                elif path == "/api/idle":
                    state.idle_after = int(query["after"])
                    state.idle_emotion = query.get("emotion", "idle")
                elif path == "/api/brightness":
                    state.brightness = json.loads(body)["value"]
                elif path == "/api/assets":
                    from .assetpack import read_pack

                    try:
                        state.assets = read_pack(body)
                    except ValueError:
                        return self._send(400)
                else:
                    return self._send(404)
            self._send(200)

    return Handler


def serve(token: str, port: int = 0) -> tuple[ThreadingHTTPServer, SimState]:
    state = SimState()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def main() -> None:
    from .assetpack import read_pack

    config = load_config()
    server, state = serve(config.secrets.device_token, 8081)
    pack = REPO_ROOT / "assets" / "build" / "moni-assets.bin"
    if pack.exists():
        state.assets = read_pack(pack.read_bytes())
    print("시뮬레이터: http://127.0.0.1:8081/ (Ctrl+C로 종료)")
    threading.Event().wait()


if __name__ == "__main__":
    main()
