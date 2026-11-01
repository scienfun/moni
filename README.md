# 모니

말로 물으면 SmallTV-Ultra 화면 속 캐릭터가 표정과 한글 대사로 답하는 데스크 비서.

## 빠른 시작

```bash
brew install uv
uv tool install platformio
make setup      # 의존성, 토큰 생성, 폰트
make assets     # 표정 에셋팩
make test && make test-native
make doctor
```

## 실행 (순정 펌웨어 + LM Studio, 기본값)

플래싱 없이 SmallTV-Ultra 순정 HTTP API로 표시하고, API 키 없이 LM Studio 로컬 모델로 답한다. `bridge/moni.toml`의 `host`에 기기 IP를, `[llm] model`에 LM Studio 모델 ID를 넣고 실행한다. 첫 실행 때 표정 GIF 8종(약 440KB)을 기기에 올린다. 기존 사진은 건드리지 않는다.

```bash
make llm                          # LM Studio 서버 시작 + 모델 로드
make run
make say T="오늘 날씨 어때?"
```

시간·타이머·밝기·날씨는 규칙으로 바로 처리하고, 나머지 대화만 모델로 보낸다. LM Studio가 꺼져 있으면 규칙 응답만 동작한다. 모델을 고를 때는 `make doctor`로 확인한다.

Claude API를 쓰려면 `[llm]`을 `provider = "anthropic"`, `model = "claude-haiku-5-5"`로 바꾸고 `bridge/.env`에 `ANTHROPIC_API_KEY`를 넣는다.

기기 IP가 바뀌지 않도록 공유기에서 고정 IP를 예약해 둔다.

| 항목 | 순정 (`backend = "stock"`) | 커스텀 (`backend = "custom"`) |
| --- | --- | --- |
| 설치 | 없음 | 펌웨어 플래싱 |
| 응답 화면 | 기기 명령 약 2.2~3.9초, 대화 약 6초 실측(`qwen3.8-27b`, M4 Max). 업로드가 약 1.9초 | 목표 2.5초, 미검증 |
| 플래시 쓰기 | 응답마다 JPEG 1장 | 없음 |
| 표정 | 전체 화면 GIF 전환 약 0.3초 | 기기가 직접 재생 |
| 응답 중 애니메이션 | 없음(정지 화면) | 있음 |
| 타이머 표시 | 1분 단위 | 1초 단위 |
| 대기 시계 | 없음 | 있음 |
| 기기 인증 | 없음(같은 네트워크면 접근 가능) | 토큰 |
| 브리지 꺼짐 시 대기 복귀 | 안 됨 | 기기가 처리 |

## 기기 없이 시험

시뮬레이터는 커스텀 펌웨어 API를 흉내 낸다. `bridge/moni.toml`을 `backend = "custom"`, `host = "127.0.0.1:8081"`로 바꾸고 터미널 셋에서 실행한다.

```bash
make sim
make run
make say T="오늘 날씨 어때?"
```

http://127.0.0.1:8081/ 에서 화면을 본다.

## 커스텀 펌웨어 설치 (선택)

빌드는 되지만 실기기에서 실행해 보지 않았다. PRD의 설치 절차를 따른다. 경로 B(유선)를 권장한다.

```bash
make backup PORT=/dev/cu.usbserial-XXXX    # 순정 백업, 복귀 가능성을 남기려면 먼저
make flash PORT=/dev/cu.usbserial-XXXX     # 펌웨어 + 에셋
make ota                                   # 이후 무선 업데이트
make assets-upload                         # 에셋만 무선 교체
```

첫 부팅 시 `Moni-Setup` AP에 연결해 Wi-Fi를 입력한다. 화면에 `moni.local`과 IP가 뜨면 성공이다.
화면이 안 나오면 `-D TFT_SPI_MODE=SPI_MODE3`, 색이 반전되면 `-D TFT_INVERSION_ON=1` 또는 `-D TFT_RGB_ORDER=TFT_BGR`를 `platformio.ini`에 추가한다.

## 안드로이드 Termux에서 단독 실행

폰 하나에서 모델 서버, 브리지, 웹 채팅을 모두 돌린다. F-Droid 버전 Termux에서 아래 두 줄이면 설치와 실행이 끝난다. 처음에는 모델(약 1GB)을 받느라 시간이 걸린다.

```bash
pkg install -y git && git clone https://github.com/scienfun/moni
bash moni/install.sh
```

실행되면 폰 브라우저에 `http://localhost:8765/` 채팅 화면이 열린다. 대화 내역이 남고, 마이크 버튼(Android Chrome)으로 말할 수도 있다. 이후에는 `moni-start`만 입력한다. 채팅 화면은 토큰 없이 열리는 대신 같은 기기(localhost)에서 온 요청만 받는다. 폰에서 실행해 본 적은 없고, 가짜 모델 서버와 시뮬레이터로 흐름만 확인했다.

## USB로 연결한 RP2040 OLED (`backend = "serial"`)

Waveshare RP2040-Zero에 SSD1306 OLED(128×64, I2C: SDA GP0, SCL GP1, 주소 0x3C)를 달고 맥에 USB로 연결하면, 브리지가 시리얼로 화면을 바로 갱신한다. 네트워크와 플래시 쓰기가 없어 화면 갱신이 약 0.13초다(맥에서 실측, 모델 시간 제외). 보드에는 MicroPython과 `ssd1306.py`가 있어야 한다.

```bash
make rp2040-install PORT=/dev/cu.usbmodemXXXX   # firmware-rp2040/main.py를 보드에 올림, 기존 main.py는 main_prev.py로 보관
```

`bridge/moni.toml`의 `[device]`를 `backend = "serial"`로 바꾸고(`port`는 비워 두면 모니 펌웨어가 답하는 `/dev/cu.usbmodem*`를 찾는다) `make run` 한다.

- 노랑·파랑 2색 패널을 가정한다(위 16줄 노랑, 아래 48줄 파랑).
- 노랑 띠: 대기 중에 4초마다 `11/1(일) 14:05`와 `맑음 14도 / 비 20%`를 번갈아 보여 주고, 생각·듣는 중에는 상태 문구를 보여 준다. 날씨를 못 불러오면 날짜·시각만 나온다.
- 파랑 영역: 대기·생각 중에는 큰 얼굴만, 응답 때는 왼쪽에 작은 얼굴이 움직이고 오른쪽에 대사가 뜬다. 긴 대사는 2페이지까지, 글자 크기는 16·14·12px 중 맞는 것을 고른다.
- 날씨·시간 패널은 대사에 이미 들어 있어 겹쳐 그리지 않는다. 타이머는 1초마다 남은 시간을 보여 준다.
- 밝기 명령은 OLED 대비(contrast)로 바뀐다.
- macOS·Linux 전용이다(표준 라이브러리 `termios`). 폰(Termux)에서는 USB 시리얼 접근을 따로 만들어야 해서 지원하지 않는다.
- 보드가 다른 프로그램(예: 이전 `main.py`)을 돌리고 있으면 브리지가 "모니 펌웨어가 올라간 보드를 찾지 못했습니다"라고 알린다. 이전 프로그램으로 되돌리려면 `python tools/rp2040_repl.py PORT put <백업파일> main.py`를 쓴다.
- 보드 명령은 `firmware-rp2040/main.py` 머리말에 있다. `python tools/rp2040_repl.py PORT ls|get|put|reset`으로 보드 파일을 다룰 수 있다.

## 자동 시작

```bash
make install-service
```

## PRD와 다른 점

| 항목 | PRD | 구현 | 이유 |
| --- | --- | --- | --- |
| 기본 표시 경로 | 커스텀 펌웨어 | 순정 HTTP API (`backend = "stock"`), 커스텀은 선택 | 플래싱 위험과 추가 장비 없이 바로 동작. API는 [geekmagic-hacs](https://github.com/adrienbrault/geekmagic-hacs) 참고 |
| LLM | Claude 경량 모델 | LM Studio 로컬 모델 기본, Claude는 선택 | API 키와 비용 없이 동작. 응답은 더 느림 |
| 도구 호출 | 언급 없음 | LLM 1회 호출, 시각·날씨는 프롬프트에 포함 | 도구 왕복을 없애 응답 2.5초 목표를 지키기 위함 |
| 날씨 | 기상청 등 미정 | Open-Meteo, `WEATHER_API_KEY` 불필요 | 키 없이 바로 동작 |
| HTTP 서버 | 언급 없음 | 표준 라이브러리 | 의존성을 `anthropic`, `pillow`로 최소화 |
| 디바이스 API 추가 | — | `DELETE /api/bubble`, `DELETE /api/panel`, `/api/idle`의 `emotion` 인자 | 이전 패널 지우기, 야간 대기 표정 |
| 바이너리 본문 | 원시 바이트 | `multipart/form-data` | ESP8266 코어 3.1.2가 원시 본문을 전부 RAM에 올리기 때문 |
| 한글 범위 | 언급 없음 | Jua의 약 2,350자, 없는 글자는 `?`로 표시 | 폰트에 없는 글자가 □로 나오지 않게 함 |
| `.env` 추가 | — | `MONI_OTA_PASSWORD` | PRD의 `platformio.ini`가 요구 |
| 클라이언트 | 내보내기 파일 | 만드는 절차 문서 | 실제 앱에서 검증하지 못한 파일을 넣지 않음 |

P2 항목(메모 화면, `/v1/audio`, 호출어)은 구현하지 않았다.
