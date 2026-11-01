"""128x64 흑백 OLED(SSD1306)용 화면 합성.

노랑·파랑 2색 패널이라 위 16줄(노랑)은 상태 띠, 아래 48줄(파랑)은 캐릭터와 대사다.
화면은 SSD1306 버퍼 형식(MONO_VLSB) 1,024바이트다. 바이트 하나가 세로 8픽셀이고
아래 비트가 위쪽이다. 층(layer)끼리는 바이트 단위 OR로 겹친다.
  띠: 위 16줄 256바이트. 날짜·시각·날씨·상태 문구이며 보드가 따로 받아 덮어쓴다.
  표정 층: 캐릭터 얼굴. 글자가 없으면 크게, 있으면 왼쪽에 작게 둔다.
  글자 층: 말풍선·패널. 얼굴 오른쪽 영역(TEXT_X 이후)에만 그린다.
표정·글자 층은 파랑 영역(AREA_Y 이후)에만 그린다.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .layout import paginate, wrap_text
from .render import Renderer

OLED_W = 128
OLED_H = 64
FRAME_BYTES = OLED_W * OLED_H // 8

BAND_H = 16
BAND_BYTES = OLED_W * BAND_H // 8
AREA_Y = BAND_H
AREA_H = OLED_H - BAND_H

BIG_FACE = 46
SMALL_FACE = 36
TEXT_X = 40
TEXT_PAD = 2
SIZES = (16, 14, 12)
BAND_FONT = 13
TEXT_THRESHOLD = 110  # 가는 한글 획이 끊기지 않도록 낮게 잡는다.
FACE_THRESHOLD = 100


def pack_frame(image: Image.Image) -> bytes:
    """1비트 이미지를 SSD1306 버퍼로 바꾼다."""
    pixels = image.convert("1").load()
    out = bytearray(FRAME_BYTES)
    for page in range(OLED_H // 8):
        for x in range(OLED_W):
            value = 0
            for bit in range(8):
                if pixels[x, page * 8 + bit]:
                    value |= 1 << bit
            out[page * OLED_W + x] = value
    return bytes(out)


def unpack_frame(frame: bytes) -> Image.Image:
    """pack_frame의 반대. 시험과 미리보기에 쓴다."""
    image = Image.new("1", (OLED_W, OLED_H), 0)
    pixels = image.load()
    for page in range(OLED_H // 8):
        for x in range(OLED_W):
            value = frame[page * OLED_W + x]
            for bit in range(8):
                if value >> bit & 1:
                    pixels[x, page * 8 + bit] = 1
    return image


def merge(*layers: bytes | None) -> bytes:
    """층을 겹친다. 없는 층은 건너뛴다."""
    out = bytearray(FRAME_BYTES)
    for layer in layers:
        if layer:
            for i, value in enumerate(layer):
                out[i] |= value
    return bytes(out)


def face_layer(sprite: bytes, size: int, text_beside: bool) -> bytes:
    """스프라이트(JPEG)를 1비트 얼굴로 바꾼다. 테두리와 눈·입이 흰색, 화면이 검정이다."""
    gray = Image.open(io.BytesIO(sprite)).convert("L").resize(
        (size, size), Image.Resampling.LANCZOS
    )
    face = gray.point(lambda v: 255 if v >= FACE_THRESHOLD else 0)
    canvas = Image.new("1", (OLED_W, OLED_H), 0)
    if text_beside:
        origin = (1, AREA_Y + (AREA_H - size) // 2)
    else:
        origin = ((OLED_W - size) // 2, AREA_Y + (AREA_H - size) // 2)
    canvas.paste(face.convert("1"), origin)
    return pack_frame(canvas)


class OledRenderer(Renderer):
    """말풍선과 패널을 글자 층(1,024바이트)으로 그린다."""

    def _text_layout(self, text: str):
        width = OLED_W - TEXT_X - TEXT_PAD
        height = AREA_H - TEXT_PAD * 2
        for size in SIZES:
            font = self._font(size)
            line_height = size + 1
            per_page = height // line_height
            lines = wrap_text(text, font.getlength, width)
            if len(lines) <= per_page or size == SIZES[-1]:
                return font, line_height, paginate(lines, per_page, font.getlength, width)
        raise AssertionError("unreachable")

    def _layer(self, draw_on) -> bytes:
        image = Image.new("L", (OLED_W, OLED_H), 0)
        draw_on(ImageDraw.Draw(image))
        return pack_frame(image.point(lambda v: 255 if v >= TEXT_THRESHOLD else 0))

    def bubble(self, text: str) -> list[bytes]:
        font, line_height, pages = self._text_layout(self._clean(text))
        layers = []
        for page in pages:
            def draw(d, page=page):
                y = AREA_Y + (AREA_H - line_height * len(page)) // 2
                for line in page:
                    d.text((TEXT_X, y), line, font=font, fill=255)
                    y += line_height

            layers.append(self._layer(draw))
        return layers

    def panel(self, panel: dict) -> bytes:
        kind = panel["type"]
        data = panel["data"]
        if kind == "timer":
            minutes, seconds = divmod(int(data["remaining"]), 60)
            label, value = "타이머", f"{minutes:02d}:{seconds:02d}"
        elif kind == "time":
            label, value = "지금", data["text"]
        elif kind == "weather":
            label = {"sun": "맑음", "cloud": "흐림", "rain": "비", "snow": "눈"}.get(
                data.get("icon"), "날씨"
            )
            value = f"{data['temp']}도"
        else:
            raise ValueError(f"알 수 없는 패널: {kind}")

        def draw(d):
            d.text((TEXT_X, AREA_Y + 3), self._clean(label), font=self._font(14), fill=255)
            d.text((TEXT_X, AREA_Y + 19), value, font=self._font(24), fill=255)

        return self._layer(draw)

    def band(self, left: str, right: str = "") -> bytes:
        """노랑 띠(256바이트). left는 왼쪽, right는 오른쪽 끝에 맞춘다. right가 없으면 가운데."""
        font = self._font(BAND_FONT)
        image = Image.new("L", (OLED_W, OLED_H), 0)
        draw = ImageDraw.Draw(image)
        left, right = self._clean(left), self._clean(right)
        if right:
            draw.text((2, 0), left, font=font, fill=255)
            draw.text((OLED_W - 2 - font.getlength(right), 0), right, font=font, fill=255)
        else:
            draw.text(((OLED_W - font.getlength(left)) / 2, 0), left, font=font, fill=255)
        frame = pack_frame(image.point(lambda v: 255 if v >= TEXT_THRESHOLD else 0))
        return frame[:BAND_BYTES]
