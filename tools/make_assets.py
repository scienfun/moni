"""표정 8종 × 4프레임을 그려 에셋팩을 만든다.

결과: assets/src/*.png(원본), assets/build/moni-assets.bin,
      firmware/data/moni-assets.bin(`pio run -t uploadfs`용)
실행: make assets
"""

from __future__ import annotations

import io
import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from moni_bridge.assetpack import EmotionFrames, build_pack

ROOT = Path(__file__).resolve().parents[1]
SIZE = 120
SCALE = 4
FRAMES = 4
MAX_FRAME_BYTES = 8 * 1024

BODY = (74, 196, 190)
BODY_DARK = (46, 140, 136)
SCREEN = (16, 28, 36)
FACE = (240, 248, 250)
ACCENT = (255, 150, 170)
TEAR = (110, 190, 255)
ZZZ = (170, 190, 220)

FPS = {
    "idle": 4, "listen": 6, "think": 6, "happy": 6,
    "excited": 8, "worry": 5, "sleepy": 3, "confused": 5,
}


def px(value: float) -> int:
    return round(value * SCALE)


class Painter:
    def __init__(self) -> None:
        self.image = Image.new("RGB", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    def box(self, x0, y0, x1, y1, fill, radius=0):
        self.draw.rounded_rectangle(
            (px(x0), px(y0), px(x1), px(y1)), radius=px(radius), fill=fill
        )

    def dot(self, x, y, r, fill=FACE):
        self.draw.ellipse((px(x - r), px(y - r), px(x + r), px(y + r)), fill=fill)

    def line(self, points, fill=FACE, width=3):
        scaled = [(px(x), px(y)) for x, y in points]
        self.draw.line(scaled, fill=fill, width=px(width), joint="curve")

    def smile(self, cx, cy, w, h, fill=FACE, width=3):
        box = (px(cx - w), px(cy - h), px(cx + w), px(cy + h))
        self.draw.arc(box, 20, 160, fill=fill, width=px(width))

    def frown(self, cx, cy, w, h):
        box = (px(cx - w), px(cy), px(cx + w), px(cy + h * 2))
        self.draw.arc(box, 200, 340, fill=FACE, width=px(3))

    def happy_eye(self, cx, cy):
        box = (px(cx - 7), px(cy - 6), px(cx + 7), px(cy + 8))
        self.draw.arc(box, 200, 340, fill=FACE, width=px(3))

    def closed_eye(self, cx, cy):
        self.line([(cx - 7, cy), (cx + 7, cy)])

    def finish(self) -> Image.Image:
        return self.image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def draw_body(p: Painter, bob: float) -> tuple[float, float, float]:
    """몸통을 그리고 왼눈·오른눈 x와 눈 y를 돌려준다."""
    p.box(46, 104 + bob, 74, 112, BODY_DARK, 3)
    p.box(32, 110, 88, 115, BODY_DARK, 2)
    p.box(10, 12 + bob, 110, 104 + bob, BODY, 16)
    p.box(18, 20 + bob, 102, 96 + bob, SCREEN, 10)
    return 42, 78, 50 + bob


def face_idle(p, n, lx, rx, y):
    if n == 3:
        p.closed_eye(lx, y)
        p.closed_eye(rx, y)
    else:
        p.dot(lx, y, 6)
        p.dot(rx, y, 6)
    p.smile(60, y + 14, 10, 6)


def face_listen(p, n, lx, rx, y):
    p.dot(lx, y, 7)
    p.dot(rx, y, 7)
    p.dot(60, y + 22, 4)
    # 귀를 기울이는 소리 물결
    for i in range(n % 3 + 1):
        r = 8 + i * 6
        p.draw.arc(
            (px(96 - r), px(y - r), px(96 + r), px(y + r)), 300, 60,
            fill=ACCENT, width=px(2),
        )


def face_think(p, n, lx, rx, y):
    offset = (-3, 0, 3, 0)[n]
    p.dot(lx + offset, y - 3, 6)
    p.dot(rx + offset, y - 3, 6)
    p.line([(50, y + 22), (70, y + 20)])
    for i in range(3):
        p.dot(44 + i * 16, 34, 3 if i == n % 3 else 1.5, ACCENT)


def face_happy(p, n, lx, rx, y):
    p.happy_eye(lx, y)
    p.happy_eye(rx, y)
    p.smile(60, y + 12, 13, 10)
    p.dot(lx - 8, y + 14, 4, ACCENT)
    p.dot(rx + 8, y + 14, 4, ACCENT)


def face_excited(p, n, lx, rx, y):
    p.happy_eye(lx, y)
    p.happy_eye(rx, y)
    p.draw.chord(
        (px(46), px(y + 2), px(74), px(y + 30)), 0, 180, fill=ACCENT
    )
    angle = n * math.pi / 2
    for cx, cy in ((26, 30), (94, 30)):
        dx, dy = 6 * math.cos(angle), 6 * math.sin(angle)
        p.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], ACCENT, 2)
        p.line([(cx + dy, cy - dx), (cx - dy, cy + dx)], ACCENT, 2)


def face_worry(p, n, lx, rx, y):
    p.dot(lx, y, 6)
    p.dot(rx, y, 6)
    p.line([(lx - 8, y - 10), (lx + 6, y - 15)], width=2)
    p.line([(rx - 6, y - 15), (rx + 8, y - 10)], width=2)
    p.frown(60, y + 14, 10, 6)
    p.dot(rx + 12, y + 4 + n * 3, 3, TEAR)


def face_sleepy(p, n, lx, rx, y):
    p.closed_eye(lx, y)
    p.closed_eye(rx, y)
    p.dot(60, y + 18, 2 + (n % 2) * 2)
    for i in range(n % 3 + 1):
        x, top, s = 84 + i * 7, 40 - i * 8, 3 + i
        p.line(
            [(x, top), (x + s, top), (x, top + s), (x + s, top + s)], ZZZ, 1.5
        )


def face_confused(p, n, lx, rx, y):
    p.dot(lx, y, 6)
    p.dot(rx, y - 3, 4)
    wave = (0, 2, 0, -2)[n]
    p.line(
        [(46, y + 20), (53, y + 16 + wave), (60, y + 20), (67, y + 16 - wave), (74, y + 20)],
        width=2,
    )
    p.draw.arc((px(84), px(26), px(96), px(38)), 180, 90, fill=ACCENT, width=px(2))
    p.dot(90, 44, 1.5, ACCENT)


FACES = {
    "idle": face_idle, "listen": face_listen, "think": face_think,
    "happy": face_happy, "excited": face_excited, "worry": face_worry,
    "sleepy": face_sleepy, "confused": face_confused,
}


def render(name: str, frame: int) -> Image.Image:
    painter = Painter()
    bob = (0, -1, 0, 1)[frame] * (2 if name == "excited" else 1)
    FACES[name](painter, frame, *draw_body(painter, bob))
    return painter.finish()


def encode(image: Image.Image) -> bytes:
    """기기 프레임 상한 안에 들어가는 baseline JPEG를 만든다."""
    for quality in (85, 75, 65, 55):
        output = io.BytesIO()
        image.save(output, "JPEG", quality=quality, optimize=True, progressive=False)
        if output.tell() <= MAX_FRAME_BYTES:
            return output.getvalue()
    raise SystemExit("프레임을 8KB 안에 넣을 수 없습니다")


def main() -> None:
    source = ROOT / "assets" / "src"
    build = ROOT / "assets" / "build"
    data = ROOT / "firmware" / "data"
    for folder in (source, build, data):
        folder.mkdir(parents=True, exist_ok=True)

    emotions = []
    for name, fps in FPS.items():
        frames = []
        for frame in range(FRAMES):
            image = render(name, frame)
            image.save(source / f"{name}_{frame}.png")
            frames.append(encode(image))
        emotions.append(EmotionFrames(name, fps, frames))

    blob = build_pack(emotions, SIZE, SIZE)
    (build / "moni-assets.bin").write_bytes(blob)
    shutil.copy(build / "moni-assets.bin", data / "moni-assets.bin")
    print(f"moni-assets.bin {len(blob) / 1024:.1f}KB, 표정 {len(emotions)}종")


if __name__ == "__main__":
    main()
