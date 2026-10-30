"""클라이언트 API v1 서버."""

from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .brain import build_brain
from .chat import ChatLog
from .config import Config, load_config
from .device import build_device
from .service import MoniService
from .tools import WeatherService
from .web import INDEX_HTML

log = logging.getLogger(__name__)

MAX_BODY = 16 * 1024
MAX_CHAT_CHARS = 500
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def make_handler(
    service: MoniService, token: str, chat: ChatLog | None = None
) -> type[BaseHTTPRequestHandler]:
    chat_lock = threading.Lock()  # 장치 화면과 대화 순서가 섞이지 않게 한 번에 하나만 처리한다.

    class Handler(BaseHTTPRequestHandler):
        server_version = "moni"

        def log_message(self, format: str, *args) -> None:
            log.debug(format, *args)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _is_web_route(self) -> bool:
            return chat is not None and (
                self.path == "/" or self.path.startswith("/chat/")
            )

        def _web_allowed(self) -> bool:
            """채팅 화면은 토큰이 없으므로 같은 기기(localhost)에서 온 요청만 받는다.

            Host 헤더도 확인해 DNS 리바인딩으로 다른 사이트가 호출하는 것을 막고,
            POST/DELETE는 JSON 요청만 받아 다른 사이트의 폼 전송을 막는다.
            """
            if self.client_address[0] not in LOOPBACK:
                return False
            host = self.headers.get("Host", "")
            if not host.endswith("]"):  # IPv6 리터럴이 아니면 포트를 뗀다.
                host = host.rsplit(":", 1)[0]
            if host not in LOCAL_HOSTS:
                return False
            if self.command in ("POST", "DELETE"):
                return self.headers.get("Content-Type", "").startswith("application/json")
            return True

        def _handle_web(self) -> None:
            if not self._web_allowed():
                return self._send(403, {"error": "forbidden"})
            if self.command == "GET" and self.path == "/":
                return self._send_html(INDEX_HTML)
            if self.command == "GET" and self.path == "/chat/history":
                return self._send(200, {"messages": chat.history()})
            if self.command == "DELETE" and self.path == "/chat/history":
                chat.clear()
                return self._send(200, {"status": "cleared"})
            if self.command == "POST" and self.path == "/chat/send":
                body = self._json_body()
                text = body.get("text") if body else None
                if not isinstance(text, str) or not text.strip():
                    return self._send(400, {"error": "text is required"})
                text = text.strip()[:MAX_CHAT_CHARS]
                with chat_lock:
                    chat.add("user", text)
                    try:
                        result = service.command(text)
                    except Exception as error:  # 장치·모델 오류로 화면이 멈추지 않게 한다.
                        log.warning("채팅 명령 실패: %s", error)
                        return self._send(502, {"error": "command failed"})
                    reply = chat.add(
                        "moni", result["line"], speak=result["speak"],
                        emotion=result["emotion"], latency_ms=result["latency_ms"],
                    )
                return self._send(200, {"reply": reply})
            self._send(404, {"error": "not found"})

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())

        def _json_body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= MAX_BODY:
                    return None
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return None
            return data if isinstance(data, dict) else None

        def do_GET(self) -> None:
            if self._is_web_route():
                return self._handle_web()
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            if self.path == "/v1/health":
                return self._send(200, service.health())
            self._send(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            if self._is_web_route():
                return self._handle_web()
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self._is_web_route():
                return self._handle_web()
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            body = self._json_body()
            if body is None:
                return self._send(400, {"error": "invalid json"})
            if self.path == "/v1/listen":
                threading.Thread(target=service.listen, daemon=True).start()
                return self._send(202, {"status": "listening"})
            if self.path == "/v1/command":
                text = body.get("text")
                if not isinstance(text, str) or not text.strip():
                    return self._send(400, {"error": "text is required"})
                return self._send(200, service.command(text.strip()))
            self._send(404, {"error": "not found"})

    return Handler


def build_service(config: Config) -> MoniService:
    from .render import Renderer

    return MoniService(
        build_device(config),
        build_brain(config),
        Renderer(),
        WeatherService(config.weather),
        config.schedule,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    if not config.secrets.client_token:
        raise SystemExit("bridge/.env에 MONI_CLIENT_TOKEN이 필요합니다.")
    if config.device.backend == "custom" and not config.secrets.device_token:
        raise SystemExit("custom backend는 bridge/.env의 MONI_DEVICE_TOKEN이 필요합니다.")

    service = build_service(config)
    service.prepare()
    threading.Thread(target=service.run_schedule, daemon=True).start()
    chat = ChatLog(config.state_dir / "chat.jsonl")
    handler = make_handler(service, config.secrets.client_token, chat)
    server = ThreadingHTTPServer((config.server.bind, config.server.port), handler)
    log.info("모니 브리지 시작: http://%s:%d", config.server.bind, config.server.port)
    log.info("채팅 화면: http://localhost:%d/ (이 기기에서만 열림)", config.server.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
