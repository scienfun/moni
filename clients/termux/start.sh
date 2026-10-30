#!/usr/bin/env bash
# Termux에서 llama.cpp 서버와 모니 브리지를 한 번에 준비하고 실행한다.
# 사용: bash clients/termux/start.sh        (Ctrl+C로 둘 다 종료)
# 처음 설치는 저장소 루트의 install.sh 가 이 스크립트까지 실행한다.
# 환경변수로 바꿀 수 있다: LLAMA_DIR, MODEL_FILE, MODEL_NAME, LLAMA_PORT, THREADS, MONI_DEVICE_HOST
set -euo pipefail

export LC_ALL=C LANG=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRIDGE="$ROOT/bridge"
LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
MODEL_BASENAME="qwen2.5-1.5b-instruct-q4_k_m.gguf"
# 직접 빌드한 llama.cpp가 있으면 그것을, 없으면 pkg로 설치한 llama-server를 쓴다.
if [ -x "$LLAMA_DIR/build/bin/llama-server" ]; then
    LLAMA_BIN="${LLAMA_BIN:-$LLAMA_DIR/build/bin/llama-server}"
else
    LLAMA_BIN="${LLAMA_BIN:-$(command -v llama-server || true)}"
fi
if [ -f "$LLAMA_DIR/models/$MODEL_BASENAME" ]; then
    MODEL_FILE="${MODEL_FILE:-$LLAMA_DIR/models/$MODEL_BASENAME}"
else
    MODEL_FILE="${MODEL_FILE:-$HOME/models/$MODEL_BASENAME}"
fi
MODEL_NAME="${MODEL_NAME:-qwen2.5-1.5b-instruct}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
THREADS="${THREADS:-4}"
MONI_DEVICE_HOST="${MONI_DEVICE_HOST:-192.168.1.230}"  # SmallTV IP, moni.toml을 새로 만들 때만 쓴다
LOG="${TMPDIR:-/tmp}/llama-server.log"

die() { echo "오류: $*" >&2; exit 1; }

[ -n "$LLAMA_BIN" ] && [ -x "$LLAMA_BIN" ] || die "llama-server가 없습니다. bash install.sh 를 먼저 실행하세요."
[ -f "$MODEL_FILE" ] || die "모델 파일이 없습니다: $MODEL_FILE (bash install.sh 로 받을 수 있습니다)"
PY="$(command -v python || command -v python3)" || die "pkg install python python-pillow 를 먼저 실행하세요."

# 1. 설정 파일: 없을 때만 만들고 LLM 주소를 이 서버로 맞춘다. 기존 설정은 건드리지 않는다.
if [ ! -f "$BRIDGE/moni.toml" ]; then
    "$PY" "$ROOT/tools/setup_env.py"
    sed -i.bak \
        -e "s|^model = .*|model = \"$MODEL_NAME\"|" \
        -e "s|^base_url = .*|base_url = \"http://127.0.0.1:$LLAMA_PORT/v1\"|" \
        "$BRIDGE/moni.toml"
    sed -i.bak -e "s|^host = .*|host = \"$MONI_DEVICE_HOST\"|" "$BRIDGE/moni.toml"
    rm -f "$BRIDGE/moni.toml.bak"
    echo "bridge/moni.toml 을 새로 만들었습니다. [device] host(SmallTV IP)를 확인하세요."
fi
[ -f "$BRIDGE/.env" ] || "$PY" "$ROOT/tools/setup_env.py"

# 2. 브리지는 설치 없이 소스에서 바로 실행한다. 필요한 패키지는 pillow뿐이다.
"$PY" -c "import PIL" 2>/dev/null || die "pkg install python-pillow 를 먼저 실행하세요."

# 3. 화면이 꺼져도 프로세스가 죽지 않게 한다. Termux:API가 없으면 건너뛴다.
command -v termux-wake-lock >/dev/null && termux-wake-lock || true

# 4. llama-server를 백그라운드로 띄우고 준비될 때까지 기다린다.
if curl -fs "http://127.0.0.1:$LLAMA_PORT/health" >/dev/null 2>&1; then
    echo "llama-server가 이미 실행 중입니다. 그대로 씁니다."
else
    "$LLAMA_BIN" -m "$MODEL_FILE" --host 127.0.0.1 --port "$LLAMA_PORT" \
        -c 2048 -t "$THREADS" >"$LOG" 2>&1 &
    LLAMA_PID=$!
    trap 'kill "$LLAMA_PID" 2>/dev/null || true' EXIT INT TERM
    echo -n "모델 로딩 중"
    for _ in $(seq 1 120); do
        curl -fs "http://127.0.0.1:$LLAMA_PORT/health" >/dev/null 2>&1 && break
        kill -0 "$LLAMA_PID" 2>/dev/null || { echo; tail -n 20 "$LOG"; die "llama-server가 종료됐습니다."; }
        echo -n "."; sleep 2
    done
    curl -fs "http://127.0.0.1:$LLAMA_PORT/health" >/dev/null 2>&1 \
        || die "4분 안에 모델이 준비되지 않았습니다. 로그: $LOG"
    echo " 준비됨"
fi

# 5. 브리지 실행 (여기서 계속 돈다)
echo "모니 브리지 시작 (Ctrl+C로 종료)"
BRIDGE_PORT="$(sed -n 's/^port *= *\([0-9]*\).*/\1/p' "$BRIDGE/moni.toml" | head -n1)"
CHAT_URL="http://localhost:${BRIDGE_PORT:-8765}/"
echo "채팅 화면: $CHAT_URL"
if command -v termux-open-url >/dev/null; then
    (sleep 4; termux-open-url "$CHAT_URL") &
fi
PYTHONPATH="$BRIDGE/src" "$PY" -m moni_bridge.server
