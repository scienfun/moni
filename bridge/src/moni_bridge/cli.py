"""moni 명령: say, health, doctor, assets-upload."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request

from .config import REPO_ROOT, Config, load_config
from .device import DeviceError, build_device


def _call(config: Config, method: str, path: str, payload: dict | None = None) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{config.server.port}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {config.secrets.client_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with opener.open(request, timeout=15) as response:
        return json.load(response)


OPTIONAL_LLM = "LLM"


def _check_lmstudio(config: Config) -> tuple[str, bool, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{config.llm.base_url}/models", timeout=3) as response:
            models = [m["id"] for m in json.load(response)["data"]]
    except (OSError, ValueError, KeyError) as error:
        return OPTIONAL_LLM, False, f"LM Studio 연결 실패({error}), `make llm`으로 시작"
    if config.llm.model not in models:
        return OPTIONAL_LLM, False, f"{config.llm.model} 없음, 설치된 모델: {models}"
    return OPTIONAL_LLM, True, f"LM Studio {config.llm.model}"


def doctor(config: Config) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python 3.12 이상", sys.version_info >= (3, 12), sys.version.split()[0]))
    checks.append(("클라이언트 토큰", bool(config.secrets.client_token), "bridge/.env"))
    if config.device.backend == "custom":
        checks.append(("기기 토큰", bool(config.secrets.device_token), "bridge/.env"))
    if config.llm.provider == "lmstudio":
        checks.append(_check_lmstudio(config))
    else:
        checks.append(
            (OPTIONAL_LLM, bool(config.secrets.anthropic_api_key), "Anthropic 키가 없으면 규칙 응답만 동작")
        )
    font = REPO_ROOT / "assets" / "fonts" / "Jua-Regular.ttf"
    checks.append(("한글 폰트", font.exists(), "make fetch-font"))
    pack = REPO_ROOT / "assets" / "build" / "moni-assets.bin"
    checks.append(("에셋팩", pack.exists(), "make assets"))

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        free = probe.connect_ex(("127.0.0.1", config.server.port)) != 0
    checks.append((f"브리지 포트 {config.server.port}", True, "비어 있음" if free else "브리지 실행 중"))

    try:
        status = build_device(config).status()
        detail = f"{config.device.backend}, fw {status.get('fw')}"
        checks.append((f"기기 {config.device.host}", True, detail))
    except DeviceError as error:
        checks.append((f"기기 {config.device.host}", False, str(error)))

    for name, passed, detail in checks:
        print(f"[{'OK' if passed else '!!'}] {name}: {detail}")
    required_ok = all(passed for name, passed, _ in checks if name != OPTIONAL_LLM)
    return 0 if required_ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="moni")
    commands = parser.add_subparsers(dest="command", required=True)
    say = commands.add_parser("say", help="텍스트로 전체 흐름 시험")
    say.add_argument("text")
    commands.add_parser("health", help="브리지와 기기 상태")
    commands.add_parser("doctor", help="설치 점검")
    upload = commands.add_parser("assets-upload", help="에셋팩을 기기에 설치")
    upload.add_argument(
        "--file", default=str(REPO_ROOT / "assets" / "build" / "moni-assets.bin")
    )
    args = parser.parse_args()
    config = load_config()

    try:
        if args.command == "say":
            payload = {"text": args.text, "client": "cli", "lang": "ko-KR"}
            result = _call(config, "POST", "/v1/command", payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "health":
            print(json.dumps(_call(config, "GET", "/v1/health"), ensure_ascii=False))
        elif args.command == "doctor":
            sys.exit(doctor(config))
        else:
            with open(args.file, "rb") as pack:
                build_device(config).upload_assets(pack.read())
            print("에셋 설치 완료")
    except (urllib.error.URLError, DeviceError, OSError) as error:
        sys.exit(f"실패: {error}")


if __name__ == "__main__":
    main()
