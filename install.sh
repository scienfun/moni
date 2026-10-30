#!/usr/bin/env bash
# Termux 한 번에 설치: 패키지, 모델, 모니 브리지, 웹 채팅까지 준비하고 실행한다.
#   pkg install -y git && git clone https://github.com/scienfun/moni && bash moni/install.sh
# 다시 실행해도 이미 있는 것은 건너뛴다. 환경변수: MODEL_URL, MODEL_DIR, MONI_DEVICE_HOST
set -euo pipefail

export LC_ALL=C LANG=C
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="${MODEL_DIR:-$HOME/models}"
MODEL_BASENAME="qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_URL="${MODEL_URL:-https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/$MODEL_BASENAME}"

die() { echo "오류: $*" >&2; exit 1; }

command -v pkg >/dev/null || die "Termux에서 실행하세요. (F-Droid 버전 Termux 권장)"

echo "[1/3] 패키지 설치"
pkg install -y python python-pillow curl git llama-cpp termux-api

echo "[2/3] 모델 준비"
if [ -f "$HOME/llama.cpp/models/$MODEL_BASENAME" ] || [ -f "$MODEL_DIR/$MODEL_BASENAME" ]; then
    echo "모델이 이미 있습니다."
else
    mkdir -p "$MODEL_DIR"
    # 끊겨도 이어받을 수 있게 .part 로 받고 끝나면 이름을 바꾼다.
    curl -fL -C - --retry 5 --retry-delay 3 -o "$MODEL_DIR/$MODEL_BASENAME.part" "$MODEL_URL" \
        || die "모델 다운로드 실패. 같은 명령을 다시 실행하면 이어받습니다."
    mv "$MODEL_DIR/$MODEL_BASENAME.part" "$MODEL_DIR/$MODEL_BASENAME"
fi

echo "[3/3] 실행 준비"
if ! grep -qs "moni-start" "$HOME/.bashrc" 2>/dev/null; then
    echo "alias moni-start='bash $ROOT/clients/termux/start.sh'" >> "$HOME/.bashrc"
    echo "다음부터는 moni-start 만 입력하면 됩니다."
fi

exec bash "$ROOT/clients/termux/start.sh"
