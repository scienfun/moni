"""한글 말풍선과 정보 패널을 그린다. 시스템 폰트는 쓰지 않는다."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .config import REPO_ROOT
from .layout import paginate, wrap_text

FONT_PATH = REPO_ROOT / "assets" / "fonts" / "Jua-Regular.ttf"

BUBBLE_W = 240
BUBBLE_H = 104
BUBBLE_BYTES = 3120
PANEL_W = 108
PANEL_H = 120
PANEL_MAX_BYTES = 12 * 1024

PAD_X = 10
PAD_Y = 8
SIZES = (26, 22, 19)
WHITE = (255, 255, 255)
GRAY = (150, 160, 170)
BLUE = (110, 190, 255)
YELLOW = (255, 210, 70)

# Jua에 없는 기호의 대체 글자. 없는 글자는 _clean이 "?"로 바꾼다.
SYMBOLS = {"…": "...", "°": "도", "℃": "도", "·": ",", "“": '"', "”": '"', "‘": "'", "’": "'"}


def compose_screen(
    sprite: bytes | None, panel: bytes | None, bubble: bytes | None
) -> Image.Image:
    """화면 전체를 합성한다. 패널이 없으면 캐릭터를 가운데에 둔다."""
    image = Image.new("RGB", (240, 240), (0, 0, 0))
    if sprite:
        image.paste(Image.open(io.BytesIO(sprite)), (4 if panel else 60, 8))
    if panel:
        image.paste(Image.open(io.BytesIO(panel)), (128, 8))
    if bubble:
        bitmap = Image.frombytes("1", (BUBBLE_W, BUBBLE_H), bubble)
        image.paste((255, 255, 255), (0, 136), bitmap)
    return image


class Renderer:
    def __init__(self, font_path=FONT_PATH) -> None:
        if not font_path.exists():
            raise FileNotFoundError(
                f"{font_path}가 없습니다. `make setup`으로 폰트를 받으세요."
            )
        self.font_path = font_path
        self._notdef = self._shape("￿")
        self._supported: dict[str, bool] = {}

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.font_path), size)

    def _shape(self, char: str) -> bytes:
        image = Image.new("L", (32, 32), 0)
        ImageDraw.Draw(image).text((2, 2), char, font=self._font(24), fill=255)
        return image.tobytes()

    def _has_glyph(self, char: str) -> bool:
        if char.isspace():
            return True
        if char not in self._supported:
            self._supported[char] = self._shape(char) != self._notdef
        return self._supported[char]

    def _clean(self, text: str) -> str:
        text = "".join(SYMBOLS.get(char, char) for char in text)
        return "".join(char if self._has_glyph(char) else "?" for char in text)

    def _layout(self, text: str) -> tuple[ImageFont.FreeTypeFont, int, list[list[str]]]:
        """한 페이지에 들어가는 가장 큰 글자 크기를 고른다."""
        width = BUBBLE_W - PAD_X * 2
        height = BUBBLE_H - PAD_Y * 2
        for size in SIZES:
            font = self._font(size)
            line_height = size + 4
            per_page = height // line_height
            lines = wrap_text(text, font.getlength, width)
            if len(lines) <= per_page or size == SIZES[-1]:
                pages = paginate(lines, per_page, font.getlength, width)
                return font, line_height, pages
        raise AssertionError("unreachable")

    def bubble(self, text: str) -> list[bytes]:
        font, line_height, pages = self._layout(self._clean(text))
        result = []
        for page in pages:
            image = Image.new("L", (BUBBLE_W, BUBBLE_H), 0)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (0, 0, BUBBLE_W - 1, BUBBLE_H - 1), radius=12, outline=255
            )
            y = (BUBBLE_H - line_height * len(page)) // 2
            for line in page:
                draw.text((PAD_X, y), line, font=font, fill=255)
                y += line_height
            # 1비트, 1=글자, 행 단위 MSB 우선
            bitmap = image.point(lambda v: 255 if v >= 128 else 0).convert(
                "1", dither=Image.Dither.NONE
            )
            result.append(bitmap.tobytes())
        return result

    def panel(self, panel: dict) -> bytes:
        image = Image.new("RGB", (PANEL_W, PANEL_H), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        kind = panel["type"]
        data = panel["data"]
        if kind == "weather":
            self._draw_weather(draw, data)
        elif kind == "timer":
            minutes, seconds = divmod(int(data["remaining"]), 60)
            self._center(draw, 30, "타이머", 20, GRAY)
            self._center(draw, 62, f"{minutes:02d}:{seconds:02d}", 34, WHITE)
        elif kind == "time":
            self._center(draw, 42, data["text"], 34, WHITE)
        else:
            raise ValueError(f"알 수 없는 패널: {kind}")

        output = io.BytesIO()
        image.save(output, "JPEG", quality=85, optimize=True, progressive=False)
        blob = output.getvalue()
        if len(blob) > PANEL_MAX_BYTES:
            raise ValueError(f"패널 JPEG가 큽니다: {len(blob)}")
        return blob

    def _center(self, draw, y: int, text: str, size: int, fill) -> None:
        font = self._font(size)
        x = (PANEL_W - font.getlength(text)) / 2
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_weather(self, draw, data: dict) -> None:
        icon = data["icon"]
        if icon == "sun":
            draw.ellipse((34, 10, 74, 50), fill=YELLOW)
        else:
            fill = WHITE if icon == "snow" else GRAY
            draw.ellipse((24, 22, 54, 50), fill=fill)
            draw.ellipse((44, 12, 84, 50), fill=fill)
            draw.rectangle((38, 36, 70, 50), fill=fill)
        if icon == "rain":
            for x in (36, 54, 72):
                draw.line((x, 54, x - 5, 66), fill=BLUE, width=3)
        if icon == "snow":
            for x in (36, 54, 72):
                draw.ellipse((x - 3, 56, x + 3, 62), fill=WHITE)
        self._center(draw, 68, f"{data['temp']}도", 28, WHITE)
        self._center(draw, 98, f"강수 {data['pop']}%", 16, BLUE)
