UV_RUN := uv run --project bridge
PIO := pio
FW := firmware
ENV_FILE := bridge/.env

LMS := $(HOME)/.lmstudio/bin/lms
LLM_MODEL := $(shell sed -n 's/^model = "\([^"]*\)".*/\1/p' bridge/moni.toml 2>/dev/null)

.PHONY: setup fetch-font run llm sim say doctor test test-native assets assets-upload \
        firmware flash ota backup install-service rp2040-install

setup:
	uv sync --project bridge
	$(UV_RUN) python tools/setup_env.py
	$(UV_RUN) python tools/fetch_font.py

fetch-font:
	$(UV_RUN) python tools/fetch_font.py

run:
	$(UV_RUN) moni-bridge

# LM Studio 서버를 켜고 모델을 올린다.
llm:
	$(LMS) server start
	$(LMS) load "$(LLM_MODEL)" --yes

sim:
	$(UV_RUN) moni-sim

say:
	$(UV_RUN) moni say "$(T)"

doctor:
	$(UV_RUN) moni doctor

test:
	cd bridge && PYTHONPATH=src uv run python -m unittest discover -s tests

test-native:
	mkdir -p .build
	$(UV_RUN) python -c "from moni_bridge.assetpack import *; open('.build/t.bin','wb').write(build_pack([EmotionFrames('idle',6,[b'a',b'bb']),EmotionFrames('think',8,[b'ccc'])],120,120))"
	c++ -std=c++17 -I$(FW)/src $(FW)/test/native/test_pure.cpp -o .build/native
	.build/native .build/t.bin

assets:
	$(UV_RUN) python tools/make_assets.py

assets-upload:
	$(UV_RUN) moni assets-upload

firmware:
	cd $(FW) && $(PIO) run -e moni

# 사용: make flash PORT=/dev/cu.usbserial-XXXX
flash: assets
	cd $(FW) && $(PIO) run -e moni -t upload --upload-port $(PORT)
	cd $(FW) && $(PIO) run -e moni -t uploadfs --upload-port $(PORT)

ota:
	set -a; . ./$(ENV_FILE); set +a; cd $(FW) && $(PIO) run -e moni-ota -t upload

# 사용: make backup PORT=/dev/cu.usbserial-XXXX  (다운로드 모드에서)
backup:
	esptool --port $(PORT) --baud 460800 read-flash 0x0 0x400000 stock-backup.bin
	shasum -a 256 stock-backup.bin > stock-backup.sha256

install-service:
	python3 tools/install_service.py

# USB로 연결한 RP2040 OLED 보드에 모니 펌웨어를 올린다. 사용: make rp2040-install PORT=/dev/cu.usbmodemXXXX
rp2040-install:
	python3 tools/rp2040_repl.py $(PORT) install
